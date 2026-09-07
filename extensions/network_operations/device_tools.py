"""Governed device connectivity with separate read and Skill-authorized write paths."""

from __future__ import annotations

import atexit
import threading
import uuid
from contextlib import contextmanager

import base64
import io
import ipaddress
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from extensions.network_operations.cli_runtime import CLICommandResult, InteractiveCLISession
from extensions.network_operations.device_drivers import resolve_driver

READ_ONLY_DENY = re.compile(
    r"(^|\s)(undo|delete|remove|erase|format|reload|reboot|shutdown|write|copy|configure|system-view|enable|install|upgrade|reset|clear)(\s|$)",
    re.IGNORECASE,
)
_NONEMPTY_SEMANTIC_FACTS = frozenset({"device_version", "current_config"})

_NETWORK_READ_COMMAND = re.compile(
    r"^(display|show)\s+[A-Za-z0-9_./:() -]+"
    r"(?:\s+\|\s+(?:include|exclude|begin)\s+[A-Za-z0-9_./:()\[\]{}^$*+?\\|-]+)?$",
    re.IGNORECASE,
)
_GENERIC_READ_COMMANDS = (
    re.compile(r"^uname(?:\s+-[A-Za-z]+)?$", re.IGNORECASE),
    re.compile(r"^uptime$", re.IGNORECASE),
    re.compile(r"^df(?:\s+-[A-Za-z]+)?$", re.IGNORECASE),
    re.compile(r"^ip\s+(?:address|addr|link|route)(?:\s+(?:show|list))?$", re.IGNORECASE),
    re.compile(r"^hostname$", re.IGNORECASE),
    re.compile(r"^date$", re.IGNORECASE),
)
_NETWORK_DIAGNOSTIC_COMMAND = re.compile(
    r"^(?:ping|tracert|traceroute)\s+[A-Za-z0-9_.:/-]+(?:\s+[A-Za-z0-9_.:/-]+){0,15}$",
    re.IGNORECASE,
)


def _semantic_result_payload(result, fact: str) -> dict[str, Any]:
    payload = {**result.as_dict(), "fact": fact}
    if (
        fact in _NONEMPTY_SEMANTIC_FACTS
        and payload.get("complete")
        and not payload.get("error_code")
        and not str(payload.get("output") or "").strip()
    ):
        payload["error_code"] = "empty_command_output"
    return payload

@dataclass
class DeviceCredential:
    auth_method: str = "password"
    username: str = ""
    password: str = ""
    private_key: str = ""
    passphrase: str = ""


@dataclass
class DeviceTarget:
    host: str
    port: int = 22
    protocol: str = "ssh"
    vendor: str = "generic"
    name: str = ""
    source_address: str = ""
    expected_fingerprint: str = ""
    credential: DeviceCredential | None = None


_AUTO_SOURCE_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("100.64.0.0/10", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")
)


def local_ipv4_addresses() -> list[str]:
    """Return local IPv4 addresses without requiring a platform dependency."""
    found: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            found.add(str(item[4][0]))
    except OSError:
        pass
    command = shutil.which("ip")
    argv = [command, "-o", "-4", "addr", "show"] if command else []
    if not argv:
        command = shutil.which("ifconfig")
        argv = [command] if command else []
    if argv:
        try:
            output = subprocess.run(argv, capture_output=True, check=False, text=True, timeout=2).stdout
            found.update(re.findall(r"\binet\s+(\d{1,3}(?:\.\d{1,3}){3})\b", output))
        except (OSError, subprocess.SubprocessError):
            pass
    valid = []
    for value in found:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version == 4 and not address.is_loopback and not address.is_unspecified:
            valid.append(str(address))
    return sorted(valid, key=lambda value: int(ipaddress.ip_address(value)))


def resolve_source_address(host: str, configured: str = "") -> str:
    """Select a local source for scoped private/VPN destinations.

    Explicit configuration always wins. Blank configuration means automatic:
    only addresses in the same well-defined private/VPN scope are considered,
    so public destinations continue to use the operating-system route.
    """
    explicit = str(configured or "").strip()
    if explicit:
        return explicit
    try:
        target = ipaddress.ip_address(str(host or "").strip())
    except ValueError:
        return ""
    if target.version != 4:
        return ""
    scope = next((network for network in _AUTO_SOURCE_NETWORKS if target in network), None)
    if scope is None:
        return ""
    candidates = []
    for value in local_ipv4_addresses():
        address = ipaddress.ip_address(value)
        if address in scope:
            common_prefix = 32 - (int(target) ^ int(address)).bit_length()
            candidates.append((common_prefix, int(address), value))
    return max(candidates, default=(-1, -1, ""))[2]


def fingerprint_for_key(key: Any) -> str:
    digest = base64.b64encode(__import__("hashlib").sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{digest}"


def is_read_only_command(command: str, vendor: str = "") -> bool:
    value = str(command or "").strip()
    if not value or any(marker in value for marker in ("\n", "\r", ";", "&&", "`", "$(", ">", "<")):
        return False
    # The runtime owns exactly one read/write classifier: a command is read
    # only when its first verb is display, show, or ping. Every other command is a
    # device operation and therefore follows the configure/approval path.
    del vendor
    return bool(re.match(r"^(?:display|show|ping)(?:\s|$)", value, re.IGNORECASE))


def normalize_read_only_commands(commands: list[str] | tuple[str, ...] | None, vendor: str = "") -> list[str]:
    """Validate the shared read-only command boundary for every probe path."""
    if not isinstance(commands, (list, tuple)) or any(not isinstance(command, str) for command in commands):
        raise ValueError("commands must be an array of strings")
    # A command batch is model-owned work, not a UI-sized form. Preserve its
    # order and collapse accidental duplicates instead of rejecting the whole
    # device operation. There is intentionally no artificial batch ceiling.
    selected = list(dict.fromkeys(command.strip() for command in commands if command.strip()))
    if not selected:
        raise ValueError("commands must contain at least one read-only command")
    if any(not is_read_only_command(command, vendor) for command in selected):
        raise ValueError("commands_must_be_read_only")
    return selected


def _stage(name: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "status": status, **{k: v for k, v in extra.items() if v not in ("", None)}}


def _load_private_key(private_key: str, passphrase: str = ""):
    import paramiko

    last_error: Exception | None = None
    for name in ("Ed25519Key", "RSAKey", "ECDSAKey", "DSSKey"):
        key_cls = getattr(paramiko, name, None)
        if key_cls is None:
            continue
        try:
            return key_cls.from_private_key(io.StringIO(private_key), password=passphrase or None)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"private key could not be loaded: {last_error}")


@dataclass
class _Connection:
    session: InteractiveCLISession
    close: Any
    stages: list[dict[str, Any]]
    fingerprint: str = ""
    paging: Any = None
    paging_initialized: bool = False


class _SessionPool:
    """Bounded, process-local leases; idle handles never outlive their TTL.

    The service also locks the endpoint across processes. A pool key contains
    server-owned task identity and configuration revision, never model input.
    """
    def __init__(self, *, ttl: float = 60, limit: int = 32):
        self.ttl, self.limit = ttl, limit
        self.guard = threading.RLock()
        self.entries: dict[str, dict] = {}

    def _remove(self, key, entry):
        if self.entries.get(key) is entry:
            self.entries.pop(key)
            if entry.get("timer"):
                entry["timer"].cancel()
            if entry.get("connection"):
                entry["connection"].close()

    def _expire(self, key, entry):
        with self.guard:
            if not entry["busy"]:
                self._remove(key, entry)

    @contextmanager
    def lease(self, key):
        with self.guard:
            entry = self.entries.get(key)
            if entry and entry["busy"]:
                raise RuntimeError("device_session_busy")
            if not entry:
                if len(self.entries) >= self.limit:
                    idle = next(((k, e) for k, e in self.entries.items() if not e["busy"]), None)
                    if idle:
                        self._remove(*idle)
                    else:
                        raise RuntimeError("device_session_capacity_exceeded")
                entry = {"busy": False, "connection": None, "timer": None}
                self.entries[key] = entry
            entry["busy"] = True
            if entry["timer"]:
                entry["timer"].cancel()
        try:
            yield entry
        finally:
            with self.guard:
                entry["busy"] = False
                if not key or not entry["connection"] or not entry["connection"].session.synchronized:
                    self._remove(key, entry)
                else:
                    timer = threading.Timer(self.ttl, self._expire, (key, entry))
                    timer.daemon = True
                    entry["timer"] = timer
                    timer.start()

    def close_all(self):
        with self.guard:
            for key, entry in list(self.entries.items()):
                if not entry["busy"]:
                    self._remove(key, entry)


_SESSIONS = _SessionPool()
atexit.register(_SESSIONS.close_all)


def _open_connection(target: DeviceTarget, timeout: int, accept_host_key: bool) -> _Connection:
    """Authenticate a transport; all command execution is protocol-neutral."""
    deadline = time.monotonic() + timeout
    def remaining():
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise TimeoutError("connection_setup_timeout")
        return budget
    stages = [_stage("target", "ok", host=target.host, port=target.port,
                     protocol=target.protocol, vendor=target.vendor, source_address=target.source_address)]
    source = (target.source_address, 0) if target.source_address else None
    sock = socket.create_connection((target.host, target.port), timeout=remaining(), source_address=source)
    transport = channel = None
    def close():
        try:
            if channel is not None:
                channel.close()
            if transport is not None:
                transport.close()
        finally:
            sock.close()
    try:
        stages.append(_stage("tcp", "ok"))
        fingerprint = ""
        banner = ""
        credential = target.credential or DeviceCredential(auth_method="none")
        if target.protocol == "ssh":
            import paramiko
            transport = paramiko.Transport(sock)
            transport.banner_timeout = remaining()
            transport.start_client(timeout=remaining())
            fingerprint = fingerprint_for_key(transport.get_remote_server_key())
            expected = (target.expected_fingerprint or "").strip()
            if expected and expected != fingerprint:
                raise _ConnectError("host_key_mismatch", stages, fingerprint=fingerprint)
            if not expected and not accept_host_key:
                raise _ConnectError("host_key_not_trusted", stages + [_stage("host_key", "blocked")],
                                    fingerprint=fingerprint, requires_host_key_acceptance=True)
            stages.append(_stage("host_key", "ok", fingerprint=fingerprint))
            if not credential.username:
                raise _ConnectError("username_required", stages)
            transport.auth_timeout = remaining()
            if credential.auth_method == "private_key":
                transport.auth_publickey(credential.username, _load_private_key(credential.private_key, credential.passphrase))
            else:
                transport.auth_password(credential.username, credential.password)
            if not transport.is_authenticated():
                raise _ConnectError("authentication_failed", stages)
            channel = transport.open_session(timeout=remaining())
            channel.get_pty(width=200, height=80)
            channel.invoke_shell()
            channel.settimeout(remaining())
            def receive():
                if channel.recv_ready():
                    return channel.recv(65535)
                return b"" if channel.closed or channel.exit_status_ready() else None
            send = channel.sendall
        elif target.protocol == "telnet":
            sock.settimeout(min(float(timeout), 0.2))
            decoder = _TelnetDecoder(sock)
            handshake_deadline = deadline
            banner = _telnet_read(sock, timeout=min(timeout, 1.0), decoder=decoder)
            if _LOGIN_PROMPT.search(banner):
                if not credential.username:
                    raise _ConnectError("username_required_by_device", stages)
                sock.sendall((credential.username + "\r\n").encode())
                banner += _telnet_read(sock, timeout=max(0.0, handshake_deadline-time.monotonic()), decoder=decoder)
            if _PASSWORD_PROMPT.search(banner):
                if not credential.password:
                    raise _ConnectError("password_required_by_device", stages)
                sock.sendall((credential.password + "\r\n").encode())
                banner += _telnet_read(sock, timeout=max(0.0, handshake_deadline-time.monotonic()), decoder=decoder)
            if not _DEVICE_PROMPT.search(banner):
                sock.sendall(b"\r\n")
                banner += _telnet_read(sock, timeout=max(0.0, handshake_deadline-time.monotonic()), decoder=decoder)
            if not _DEVICE_PROMPT.search(banner):
                raise _ConnectError("device_prompt_not_detected", stages)
            def receive():
                try:
                    data = sock.recv(65535)
                except TimeoutError:
                    return None
                # Negotiation-only data is not EOF.
                return (decoder.feed(data) or None) if data else b""
            send = sock.sendall
        else:
            raise _ConnectError("unsupported_protocol", stages)
        stages.append(_stage("auth", "ok"))
        driver, _ = resolve_driver(target.vendor, banner)
        session = InteractiveCLISession(send=send, receive=receive, driver=driver, timeout=timeout, initial_text=banner)
        session.deadline = deadline
        bootstrap = session.bootstrap()
        if not bootstrap.complete:
            raise _ConnectError(bootstrap.error_code or "device_prompt_not_detected", stages)
        session.refine_driver(banner + "\n" + bootstrap.text)
        stages.append(_stage("prompt", "ok"))
        return _Connection(session, close, stages, fingerprint)
    except Exception:
        close()
        raise


class _ConnectError(RuntimeError):
    def __init__(self, message, stages, **details):
        super().__init__(message)
        self.stages, self.details = stages, details


def normalize_configuration_commands(commands, vendor: str) -> list[str]:
    """Pass authorized model commands to the selected device without policy rewriting.

    Device credentials, the selected Skill and its registered connections are
    the execution boundary.  This function deliberately validates only the
    transport shape needed to send commands, never command content, vendor,
    count, encoding or configuration intent.
    """
    if not isinstance(commands, list) or not commands:
        raise ValueError("configuration_commands_are_required")
    if any(not isinstance(command, str) or not command for command in commands):
        raise ValueError("configuration_commands_must_be_strings")
    return list(commands)


def _execute_commands(connection: _Connection, commands, facts, *, read: bool, configure: bool = False) -> dict:
    session = connection.session
    if facts:
        plan = session.driver.commands_for(facts)
        selected = [command for _, command in plan]
        command_facts = {command: fact for fact, command in plan}
    else:
        selected, command_facts = commands, {}
    selected = (normalize_configuration_commands(selected, session.driver.vendor) if configure
                else normalize_read_only_commands(selected, session.driver.vendor) if read else [])
    # Some Telnet console servers retain the remote view after disconnect.
    # Normalize only a positively identified config view; this is terminal
    # housekeeping, never a save/commit or an implicit business command.
    prompt = session.prompt.strip()
    exit_command = (
        "return" if session.driver.vendor in {"h3c", "huawei"} and prompt.startswith("[")
        else "end" if session.driver.vendor == "cisco" and re.search(r"\(config[^)]*\)#$", prompt)
        else ""
    )
    mode_reset = None
    if selected and exit_command:
        mode_reset = session.run_command(exit_command, internal=True)
        if not mode_reset.complete or mode_reset.error_code:
            raise ValueError("operational_mode_reset_failed")
    if selected and not connection.paging_initialized:
        connection.paging = session.disable_paging()
        connection.paging_initialized = True
    results = []
    dispatch_blocked = False
    for command in selected:
        if dispatch_blocked:
            # A previous transport exception made it objectively impossible to
            # send this command through the same session. Keep an explicit row
            # rather than silently dropping the remainder of the model plan.
            result = CLICommandResult(
                command, "", session.prompt, False, 0, session.encoding, 0,
                error_code="command_not_sent_after_transport_failure",
                dispatch_status="not_sent",
            )
            results.append(_semantic_result_payload(result, command_facts.get(command, "")))
            continue
        try:
            result = session.run_command(command)
        except Exception:
            if not configure:
                raise
            session.invalidate()
            result = CLICommandResult(command, "", session.prompt, False, 0, session.encoding, 0,
                                      error_code="command_dispatch_uncertain", dispatch_status="uncertain")
            dispatch_blocked = True
        results.append(_semantic_result_payload(result, command_facts.get(command, "")))
        # A configuration batch is an ordered collection of independent model
        # instructions, not an implicit transaction.  Preserve the outcome of
        # every command and keep sending later commands after a deterministic
        # command failure.  The model receives the complete sequence and
        # decides whether a follow-up, read-back, or a different plan is
        # warranted.  A transport uncertainty still invalidates the session,
        # but it must not be misrepresented as a policy-created short circuit.
    output = {item["command"]: item["output"] for item in results}
    complete = all(item["complete"] and not item["error_code"] for item in results)
    payload = {
        "read_ok": complete,
        "status": "succeeded" if complete else "partial",
        "command_source": "explicit_semantic_template" if facts else "explicit_commands" if read or configure else "probe",
        "output": output, "command_results": results,
        "facts": session.driver.parse_facts(output, command_facts, results) if facts else {},
        "device_profile": session.driver.public_profile(detected_from="live_session"),
        "session": {
            "prompt": session.prompt, "encoding": session.encoding,
            "synchronized": session.synchronized,
            "pagination": connection.paging.as_dict() if connection.paging else None,
            "mode_reset": mode_reset.as_dict() if mode_reset else None,
        },
    }
    if configure:
        # A prompt acknowledges CLI processing, not the desired network outcome.
        # Interrupted writes may already have taken effect; never claim rollback.
        uncertain = any(item.get("dispatch_status") == "uncertain" or
                        (item.get("dispatch_status") == "sent" and not item["complete"])
                        for item in results)
        payload.pop("read_ok")
        payload.update(ok=complete, configuration_ok=complete,
                       status="unknown" if uncertain else "succeeded" if complete else "partial",
                       error="configuration_outcome_unknown" if uncertain else "configuration_batch_incomplete" if not complete else "",
                       execution_may_continue=uncertain, automatic_retry_allowed=False,
                       unexecuted_commands=[
                           item["command"] for item in results
                           if item.get("dispatch_status") == "not_sent"
                       ],
                       recommended_readback=True, rollback_performed=False)
    return payload


def probe_target(
    target: DeviceTarget, *, commands: list[str] | None = None,
    facts: list[str] | None = None, accept_host_key: bool = False,
    read: bool = False, timeout: int = 15, session_key: str = "", configure: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    # A one-shot probe must never share the empty key with another caller.
    # Writes use an isolated shell: a configuration view must never leak into a
    # later read or another authorized batch. Business mode transitions are explicit;
    # terminal housekeeping may first leave a retained console configuration view.
    key = session_key if session_key and not configure else "oneshot:" + uuid.uuid4().hex
    try:
        if target.protocol not in {"ssh", "telnet"}:
            raise ValueError("unsupported_protocol")
        if commands is not None and facts and commands:
            raise ValueError("commands_and_facts_are_mutually_exclusive")
        if read and not facts:
            normalize_read_only_commands(commands, target.vendor)
        if configure:
            if read or facts:
                raise ValueError("configuration_cannot_use_read_or_templates")
            normalize_configuration_commands(commands, target.vendor)
        with _SESSIONS.lease(key) as entry:
            reused = entry["connection"] is not None
            if reused:
                connection = entry["connection"]
                connection.session.timeout = max(1.0, float(timeout))
                connection.session.deadline = started + timeout
                try:
                    # Synchronize before sending any requested command. A stale
                    # channel can be reopened here, never after command dispatch.
                    healthy = connection.session.check_ready()
                except (OSError, EOFError):
                    healthy = False
                if not healthy:
                    connection.close()
                    entry["connection"] = None
                    reused = False
            if entry["connection"] is None:
                entry["connection"] = _open_connection(target, timeout, accept_host_key)
            connection = entry["connection"]
            connection.session.deadline = started + timeout
            try:
                execution = _execute_commands(connection, commands, facts, read=read, configure=configure)
                execution["session"].update({"reused": reused, "scope": "task" if session_key and not configure else "operation"})
                return _result(execution.pop("ok", True), connection.stages, started, fingerprint=connection.fingerprint, **execution)
            except ValueError as exc:
                # An unsupported optional template is not a connection outage.
                return _result(not configure, connection.stages, started, read_ok=False,
                               status="partial", error=str(exc)[:300],
                               failure_stage="command_validation", command_results=[],
                               device_profile=connection.session.driver.public_profile(detected_from="live_session"),
                               session={"reused": reused, "prompt": connection.session.prompt})
            except Exception as exc:
                connection.session.invalidate()
                if configure:
                    return _result(False, connection.stages, started,
                                   error="configuration_outcome_unknown", detail=str(exc)[:300],
                                   status="unknown", execution_may_continue=True,
                                   automatic_retry_allowed=False, recommended_readback=True)
                raise
            finally:
                if not session_key or configure:
                    connection.session.invalidate()
    except _ConnectError as exc:
        return _result(False, exc.stages, started, error=str(exc), **exc.details)
    except Exception as exc:
        return _result(False, [_stage("connection", "failed")], started, error=str(exc)[:300])


def _result(ok: bool, stages: list[dict[str, Any]], started: float, **extra: Any) -> dict[str, Any]:
    status = "succeeded" if ok else ("blocked" if any(s.get("status") == "blocked" for s in stages) else "failed")
    return {
        "ok": ok, "status": status, "stages": stages,
        "duration_ms": int((time.monotonic() - started) * 1000),
        **{k: v for k, v in extra.items() if v not in ("", None)},
    }


_LOGIN_PROMPT = re.compile(r"(?:login|username|user\s*name)\s*[:：]\s*$", re.IGNORECASE)
_PASSWORD_PROMPT = re.compile(r"password\s*[:：]\s*$", re.IGNORECASE)
_DEVICE_PROMPT = re.compile(r"(?:^|\r?\n)[^\r\n]{0,120}[>#\]]\s*$")


class _TelnetDecoder:
    """Incremental IAC parser including fragmented option/subnegotiation bytes."""
    def __init__(self, sock):
        self.sock = sock
        self.pending = bytearray()

    def feed(self, data):
        self.pending.extend(data)
        clean = bytearray()
        index = 0
        while index < len(self.pending):
            if self.pending[index] != 255:
                end = self.pending.find(b"\xff", index)
                end = len(self.pending) if end < 0 else end
                clean.extend(self.pending[index:end])
                index = end
                continue
            if len(self.pending) - index < 2:
                break
            command = self.pending[index + 1]
            if command == 255:
                clean.append(255)
                index += 2
            elif command in {251, 252, 253, 254}:
                if len(self.pending) - index < 3:
                    break
                # Do not answer negative acknowledgements (negotiation loops).
                if command in {251, 253}:
                    self.sock.sendall(bytes((255, 254 if command == 251 else 252, self.pending[index + 2])))
                index += 3
            elif command == 250:
                end = self.pending.find(b"\xff\xf0", index + 2)
                if end < 0:
                    if len(self.pending) - index > 65536:
                        raise ValueError("telnet_subnegotiation_limit")
                    break
                index = end + 2
            else:
                index += 2
        del self.pending[:index]
        return bytes(clean)


def _telnet_read(sock: socket.socket, *, timeout: float, stop_on_prompt: bool = True, decoder=None) -> str:
    from extensions.network_operations.cli_runtime import decode_terminal_bytes
    decoder = decoder or _TelnetDecoder(sock)
    deadline = time.monotonic() + timeout
    raw = bytearray()
    socket_timeout = sock.gettimeout()
    try:
        while (remaining := deadline - time.monotonic()) > 0:
            sock.settimeout(min(socket_timeout or remaining, remaining))
            try:
                data = sock.recv(65535)
            except TimeoutError:
                continue
            if not data:
                break
            raw.extend(decoder.feed(data))
            if len(raw) > 200_000:
                raise ValueError("telnet_banner_limit")
            text, _ = decode_terminal_bytes(bytes(raw))
            if stop_on_prompt and (_LOGIN_PROMPT.search(text) or _PASSWORD_PROMPT.search(text) or _DEVICE_PROMPT.search(text)):
                break
    finally:
        sock.settimeout(socket_timeout)
    return decode_terminal_bytes(bytes(raw))[0]
