"""Interactive, vendor-aware CLI state machine for network devices."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from extensions.network_operations.device_drivers import DeviceDriver, resolve_driver

ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
CONTROL_EXCEPT_LAYOUT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_OUTPUT_BYTES = 200_000
MAX_PAGER_ADVANCES = 500
INTERACTION_PROMPT = re.compile(
    r"(?:\[\s*(?:y/n|yes/no|confirm)\s*\]|\(\s*(?:y/n|yes/no)\s*\)|"
    r"(?:password|continue|confirm|filename)\s*[:?])\s*[:：]?\s*$", re.IGNORECASE,
)


def _prompt_before_async_notice(text: str, driver: DeviceDriver) -> str:
    """Recognize a completed prompt followed only by a console/syslog notice.

    Some Comware Telnet consoles append asynchronous messages immediately after
    returning a prompt (for example ``<PE 1>%... SHELL_LOGIN``).  The command
    has completed, but a strict final-line prompt check turns that transcript
    into a false timeout.  Accept only a known full prompt followed by lines
    that repeat that prompt as a percent-prefixed notification; arbitrary
    output after a prompt remains incomplete.
    """
    lines = [
        line.strip()
        for line in normalize_terminal_text(text).split("\n")
        if line.strip()
    ]
    for index in range(len(lines) - 2, -1, -1):
        prompt = lines[index]
        if not any(pattern.fullmatch(prompt) for pattern in driver.prompt_patterns):
            continue
        trailing = lines[index + 1:]
        if trailing and all(
            # Different Comware console servers either repeat the prompt
            # before a syslog line (``<PE>%...``) or emit the notice on its
            # own line (``%...``).  Both forms are asynchronous output after
            # an already observed prompt, never command payload.
            line.startswith(prompt + "%") or line.startswith("%")
            for line in trailing
        ):
            return prompt
    return ""


@dataclass
class CLIReadResult:
    text: str
    prompt: str = ""
    complete: bool = False
    pages: int = 0
    encoding: str = "utf-8"
    error_code: str = ""
    truncated: bool = False
    duration_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "prompt": self.prompt,
            "complete": self.complete,
            "pages": self.pages,
            "encoding": self.encoding,
            "error_code": self.error_code,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
            "output_hash": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
        }


@dataclass
class CLICommandResult:
    command: str
    output: str
    prompt: str
    complete: bool
    pages: int
    encoding: str
    duration_ms: int
    error_code: str = ""
    device_error: str = ""
    truncated: bool = False
    dispatch_status: str = "not_sent"

    def as_dict(self) -> dict:
        return {
            "command": self.command,
            "output": self.output,
            "prompt": self.prompt,
            "complete": self.complete,
            "pages": self.pages,
            "encoding": self.encoding,
            "duration_ms": self.duration_ms,
            "error_code": self.error_code,
            "device_error": self.device_error,
            "truncated": self.truncated,
            "dispatch_status": self.dispatch_status,
            "output_hash": hashlib.sha256(self.output.encode("utf-8")).hexdigest(),
        }


def decode_terminal_bytes(data: bytes, encodings: tuple[str, ...] = ("utf-8", "gb18030")) -> tuple[str, str]:
    for encoding in dict.fromkeys((*encodings, "utf-8", "gb18030", "latin-1")):
        try:
            return data.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def normalize_terminal_text(text: str) -> str:
    """Normalize terminal redraws while retaining meaningful line layout."""
    value = ANSI_ESCAPE.sub("", str(text or ""))
    # Apply destructive backspace semantics instead of merely deleting the
    # backspace byte; many pagers erase their own marker this way.
    reduced: list[str] = []
    for char in value:
        if char == "\b":
            if reduced and reduced[-1] not in {"\n", "\r"}:
                reduced.pop()
            continue
        reduced.append(char)
    value = "".join(reduced).replace("\r\n", "\n").replace("\r", "\n")
    value = CONTROL_EXCEPT_LAYOUT.sub("", value)
    return value


def strip_command_envelope(text: str, command: str, prompt: str) -> str:
    lines = normalize_terminal_text(text).splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].strip() == command.strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and prompt and lines[-1].strip() == prompt.strip():
        lines.pop()
    return "\n".join(lines).strip()


class InteractiveCLISession:
    """Stateful reader over an authenticated SSH shell or Telnet socket.

    ``receive`` returns bytes when data is available, ``None`` when no data is
    currently ready, and ``b''`` when the peer closed the session.
    """

    def __init__(
        self,
        *,
        send: Callable[[bytes], None],
        receive: Callable[[], bytes | None],
        driver: DeviceDriver,
        timeout: float = 15,
        initial_text: str = "",
    ):
        self._send = send
        self._receive = receive
        self.driver = driver
        self.timeout = max(1.0, float(timeout))
        self.prompt = driver.extract_prompt(initial_text)
        self.banner = normalize_terminal_text(initial_text)
        self.encoding = "utf-8"
        self._synchronized = True
        self.deadline: float | None = None

    @property
    def synchronized(self) -> bool:
        return self._synchronized

    def invalidate(self) -> None:
        self._synchronized = False

    def check_ready(self) -> bool:
        """A harmless prompt handshake, never a replay of a business command."""
        if not self._synchronized:
            return False
        self._send(b"\r\n")
        result = self.read_until_prompt(timeout=min(self.timeout, 2.0))
        self._synchronized = result.complete
        if result.prompt:
            self.prompt = result.prompt
        return result.complete

    def bootstrap(self) -> CLIReadResult:
        if self.prompt:
            return CLIReadResult(self.banner, self.prompt, True, encoding=self.encoding)
        self._send(b"\r\n")
        result = self.read_until_prompt(timeout=min(self.timeout, 8.0))
        self.banner = (self.banner + "\n" + result.text).strip()
        if result.prompt:
            self.prompt = result.prompt
        return result

    def refine_driver(self, transcript: str) -> dict:
        resolved, source = resolve_driver(self.driver.vendor, self.banner + "\n" + str(transcript or ""))
        self.driver = resolved
        return resolved.public_profile(detected_from=source)

    def disable_paging(self) -> CLICommandResult | None:
        command = self.driver.disable_paging_command
        return self.run_command(command, internal=True) if command else None

    def run_command(self, command: str, *, internal: bool = False) -> CLICommandResult:
        started = time.monotonic()
        from core.tools.context import get_runtime_cancel_check
        cancel = get_runtime_cancel_check()
        cancelled = bool(cancel and cancel())
        expired = self.deadline is not None and started >= self.deadline
        if cancelled or expired:
            self._synchronized = False
            return CLICommandResult(
                command=command, output="", prompt=self.prompt, complete=False,
                pages=0, encoding=self.encoding, duration_ms=0,
                error_code="cancelled" if cancelled else "execution_timeout",
            )
        if not self._synchronized:
            return CLICommandResult(
                command=command, output="", prompt=self.prompt, complete=False,
                pages=0, encoding=self.encoding, duration_ms=0,
                error_code="cli_session_unsynchronized",
            )
        try:
            self._send((str(command).strip() + "\r\n").encode("ascii", errors="strict"))
        except (OSError, EOFError):
            self._synchronized = False
            return CLICommandResult(
                command=command, output="", prompt=self.prompt, complete=False,
                pages=0, encoding=self.encoding, duration_ms=0,
                error_code="command_dispatch_uncertain", dispatch_status="uncertain",
            )
        read = self.read_until_prompt(timeout=self.timeout)
        if read.prompt:
            self.prompt = read.prompt
        output = strip_command_envelope(read.text, command, self.prompt)
        # A duplicate prompt from the previous command can remain buffered and
        # falsely complete this read before the new response arrives. Keep the
        # same command boundary open briefly; never resend the command.
        prompt_only = bool(output and self.driver.extract_prompt(output) == output.strip())
        if not internal and read.complete and (not output or prompt_only):
            follow_up = self.read_until_prompt(timeout=min(self.timeout, 1.0))
            if follow_up.text:
                if follow_up.prompt:
                    self.prompt = follow_up.prompt
                candidate = strip_command_envelope(follow_up.text, command, self.prompt)
                if candidate and self.driver.extract_prompt(candidate) != candidate.strip():
                    read = follow_up
                    output = candidate
        if not read.complete:
            # Never attribute the unfinished command's late bytes to the next
            # command. A new tool operation establishes a fresh connection.
            self._synchronized = False
        device_error = self.driver.command_error(output)
        error_code = read.error_code
        if device_error and not error_code:
            error_code = "device_command_rejected"
        if internal and command == self.driver.disable_paging_command and device_error:
            # Disabling pagination is an optimization. Interactive pager
            # handling remains active when the device rejects the command.
            error_code = "paging_disable_rejected"
        return CLICommandResult(
            command=command,
            output=output,
            prompt=self.prompt,
            complete=read.complete,
            pages=read.pages,
            encoding=read.encoding,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_code=error_code,
            device_error=device_error,
            truncated=read.truncated,
            dispatch_status="sent",
        )

    def read_until_prompt(self, *, timeout: float | None = None) -> CLIReadResult:
        started = time.monotonic()
        deadline = started + max(0.5, float(timeout or self.timeout))
        if self.deadline is not None:
            deadline = min(deadline, self.deadline)
        from core.tools.context import get_runtime_cancel_check
        cancel = get_runtime_cancel_check()
        raw = bytearray()
        pages = 0
        peer_closed = False
        truncated = False
        matched_spans: set[tuple[int, int, str]] = set()
        encoding = self.encoding

        while time.monotonic() < deadline:
            if cancel and cancel():
                decoded, encoding = decode_terminal_bytes(bytes(raw), self.driver.encodings)
                return CLIReadResult(normalize_terminal_text(decoded), error_code="cancelled", encoding=encoding)
            try:
                chunk = self._receive()
            except (OSError, EOFError):
                peer_closed = True
                break
            if chunk == b"":
                peer_closed = True
                break
            if chunk:
                raw.extend(chunk)
                if len(raw) > MAX_OUTPUT_BYTES:
                    del raw[MAX_OUTPUT_BYTES:]
                    truncated = True
                    break
                decoded, encoding = decode_terminal_bytes(bytes(raw), self.driver.encodings)
                normalized = normalize_terminal_text(decoded)
                for rule in self.driver.pager_rules:
                    for match in rule.pattern.finditer(normalized):
                        key = (match.start(), match.end(), rule.name)
                        if key in matched_spans:
                            continue
                        matched_spans.add(key)
                        pages += 1
                        if pages > MAX_PAGER_ADVANCES:
                            return CLIReadResult(
                                _remove_pagers(normalized, self.driver),
                                pages=pages,
                                encoding=encoding,
                                error_code="pager_limit_exceeded",
                                duration_ms=int((time.monotonic() - started) * 1000),
                            )
                        self._send(rule.response)
                prompt = self.driver.extract_prompt(_remove_pagers(normalized, self.driver))
                if not prompt:
                    prompt = _prompt_before_async_notice(normalized, self.driver)
                if not prompt and INTERACTION_PROMPT.search(normalized):
                    return CLIReadResult(normalized, pages=pages, encoding=encoding,
                                         error_code="interaction_required")
                if prompt:
                    text = _remove_pagers(normalized, self.driver)
                    self.encoding = encoding
                    return CLIReadResult(
                        text=text,
                        prompt=prompt,
                        complete=True,
                        pages=pages,
                        encoding=encoding,
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
            else:
                time.sleep(0.02)

        decoded, encoding = decode_terminal_bytes(bytes(raw), self.driver.encodings)
        text = _remove_pagers(normalize_terminal_text(decoded), self.driver)
        error_code = (
            "output_limit_exceeded" if truncated else
            "connection_closed" if peer_closed else
            "prompt_timeout"
        )
        return CLIReadResult(
            text=text,
            complete=False,
            pages=pages,
            encoding=encoding,
            error_code=error_code,
            truncated=truncated,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def _remove_pagers(text: str, driver: DeviceDriver) -> str:
    value = text
    for rule in driver.pager_rules:
        value = rule.pattern.sub("", value)
    return value
