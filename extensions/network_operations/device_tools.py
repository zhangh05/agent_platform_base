"""Governed read-only device connectivity helpers."""

from __future__ import annotations

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

from extensions.network_operations.cli_runtime import InteractiveCLISession
from extensions.network_operations.device_drivers import resolve_driver

READ_ONLY_DENY = re.compile(
    r"(^|\s)(undo|delete|remove|erase|format|reload|reboot|shutdown|write|copy|configure|system-view|enable|install|upgrade|reset|clear)(\s|$)",
    re.IGNORECASE,
)
MAX_READ_ONLY_COMMANDS = 20

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
    if READ_ONLY_DENY.search(value):
        return False
    normalized_vendor = str(vendor or "").strip().lower()
    network_match = _NETWORK_READ_COMMAND.fullmatch(value)
    if normalized_vendor in {"h3c", "huawei"}:
        return bool(network_match and value.lower().startswith("display "))
    if normalized_vendor == "cisco":
        return bool(network_match and value.lower().startswith("show "))
    if normalized_vendor == "generic":
        return any(pattern.fullmatch(value) for pattern in _GENERIC_READ_COMMANDS)
    return bool(network_match) or any(pattern.fullmatch(value) for pattern in _GENERIC_READ_COMMANDS)


def normalize_read_only_commands(commands: list[str] | tuple[str, ...] | None, vendor: str = "") -> list[str]:
    """Validate the shared read-only command boundary for every probe path."""
    if not isinstance(commands, (list, tuple)) or any(not isinstance(command, str) for command in commands):
        raise ValueError("commands must be an array of strings")
    selected = [command.strip() for command in commands]
    if not selected or len(selected) > MAX_READ_ONLY_COMMANDS:
        raise ValueError("commands must contain 1 to 20 read-only commands")
    if len(set(selected)) != len(selected):
        raise ValueError("commands must be unique")
    if any(not is_read_only_command(command, vendor) for command in selected):
        raise ValueError("commands_must_be_read_only")
    return selected


def _stage(name: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "status": status, **{k: v for k, v in extra.items() if v not in ("", None)}}


def _load_private_key(private_key: str, passphrase: str = ""):
    import paramiko

    last_error: Exception | None = None
    for key_cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey, paramiko.DSSKey):
        try:
            return key_cls.from_private_key(io.StringIO(private_key), password=passphrase or None)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"private key could not be loaded: {last_error}")


def _run_shell_commands(
    transport: Any,
    vendor: str,
    commands: list[str] | None,
    timeout: int,
    *,
    facts: list[str] | None = None,
) -> dict[str, Any]:
    channel = transport.open_session(timeout=timeout)
    channel.get_pty(width=200, height=80)
    channel.invoke_shell()
    channel.settimeout(timeout)

    def receive() -> bytes | None:
        return channel.recv(65535) if channel.recv_ready() else None

    driver, source = resolve_driver(vendor)
    session = InteractiveCLISession(
        send=lambda data: channel.send(data),
        receive=receive,
        driver=driver,
        timeout=timeout,
    )
    try:
        bootstrap = session.bootstrap()
        if not bootstrap.complete:
            return {
                "ok": False,
                "error": bootstrap.error_code or "device_prompt_not_detected",
                "session": bootstrap.as_dict(),
                "device_profile": driver.public_profile(detected_from=source),
                "output": {},
                "command_results": [],
            }
        profile = session.refine_driver(bootstrap.text)
        if facts:
            command_plan = session.driver.commands_for(facts)
            selected_commands = [command for _fact, command in command_plan]
            command_facts = {command: fact for fact, command in command_plan}
        else:
            selected_commands = list(commands or [])
            command_facts = {}
        selected_commands = normalize_read_only_commands(selected_commands, session.driver.vendor) if selected_commands else []
        paging = session.disable_paging() if selected_commands else None
        output: dict[str, str] = {}
        command_results: list[dict[str, Any]] = []
        for command in selected_commands:
            result = session.run_command(command)
            output[command] = result.output
            command_results.append({**result.as_dict(), "fact": command_facts.get(command, "")})
        complete = all(item.get("complete") and not item.get("error_code") for item in command_results)
        parsed_facts = session.driver.parse_facts(output, command_facts)
        return {
            "ok": True,
            "read_ok": complete,
            "status": "succeeded" if complete else "partial",
            "output": output,
            "facts": parsed_facts,
            "command_results": command_results,
            "device_profile": profile,
            "session": {
                "prompt": session.prompt,
                "encoding": session.encoding,
                "pagination": paging.as_dict() if paging else None,
            },
        }
    finally:
        channel.close()


def probe_target(
    target: DeviceTarget,
    *,
    commands: list[str] | None = None,
    facts: list[str] | None = None,
    accept_host_key: bool = False,
    read: bool = False,
    timeout: int = 15,
) -> dict[str, Any]:
    protocol = str(target.protocol or "ssh").strip().lower()
    if protocol == "telnet":
        return _probe_telnet(target, commands=commands, facts=facts, read=read, timeout=timeout)
    if protocol != "ssh":
        return _result(False, [_stage("target", "failed")], time.monotonic(), error="unsupported_protocol")
    import paramiko

    stages: list[dict[str, Any]] = []
    started = time.monotonic()
    sock: socket.socket | None = None
    transport: Any = None
    try:
        stages.append(_stage("target", "ok", host=target.host, port=target.port, protocol="ssh", vendor=target.vendor, source_address=target.source_address))
        source = (target.source_address, 0) if target.source_address else None
        sock = socket.create_connection((target.host, target.port), timeout=timeout, source_address=source)
        stages.append(_stage("tcp", "ok"))

        transport = paramiko.Transport(sock)
        transport.start_client(timeout=timeout)
        remote_key = transport.get_remote_server_key()
        fingerprint = fingerprint_for_key(remote_key)
        expected = (target.expected_fingerprint or "").strip()
        if expected and expected != fingerprint:
            stages.append(_stage("host_key", "failed", fingerprint=fingerprint, expected_fingerprint=expected))
            return _result(False, stages, started, error="host_key_mismatch", fingerprint=fingerprint)
        if not expected and not accept_host_key:
            stages.append(_stage("host_key", "blocked", fingerprint=fingerprint))
            return _result(False, stages, started, error="host_key_not_trusted", fingerprint=fingerprint, requires_host_key_acceptance=True)
        stages.append(_stage("host_key", "ok", fingerprint=fingerprint, accepted=bool(accept_host_key and not expected)))

        credential = target.credential or DeviceCredential()
        if not credential.username:
            stages.append(_stage("auth", "failed"))
            return _result(False, stages, started, error="username_required", fingerprint=fingerprint)
        if credential.auth_method == "private_key":
            pkey = _load_private_key(credential.private_key, credential.passphrase)
            transport.auth_publickey(credential.username, pkey)
        else:
            transport.auth_password(credential.username, credential.password)
        if not transport.is_authenticated():
            stages.append(_stage("auth", "failed"))
            return _result(False, stages, started, error="authentication_failed", fingerprint=fingerprint)
        stages.append(_stage("auth", "ok", username=credential.username, method=credential.auth_method))

        if not read:
            shell_result = _run_shell_commands(transport, target.vendor, [], timeout)
            if not shell_result.get("ok"):
                stages.append(_stage("prompt", "failed"))
                return _result(
                    False, stages, started,
                    error=str(shell_result.get("error") or "device_prompt_not_detected"),
                    fingerprint=fingerprint,
                    device_profile=shell_result.get("device_profile"),
                    session=shell_result.get("session"),
                )
            stages.append(_stage("prompt", "ok"))
            return _result(
                True, stages, started, fingerprint=fingerprint,
                device_profile=shell_result.get("device_profile"),
                session=shell_result.get("session"),
            )

        execution = _run_shell_commands(transport, target.vendor, commands, timeout, facts=facts)
        if not execution.get("ok"):
            stages.append(_stage("prompt", "failed"))
            return _result(
                False, stages, started,
                error=str(execution.get("error") or "device_cli_session_failed"),
                fingerprint=fingerprint,
                device_profile=execution.get("device_profile"),
                session=execution.get("session"),
            )
        stages.append(_stage("prompt", "ok"))
        stages.append(_stage("read", "ok" if execution.get("read_ok") else "partial", command_count=len(execution.get("command_results") or [])))
        return _result(
            True, stages, started,
            status=str(execution.get("status") or "succeeded"),
            fingerprint=fingerprint,
            output=execution.get("output") or {},
            facts=execution.get("facts") or {},
            command_results=execution.get("command_results") or [],
            device_profile=execution.get("device_profile") or {},
            session=execution.get("session") or {},
            read_ok=bool(execution.get("read_ok")),
        )
    except Exception as exc:
        failed_stage = "ssh" if any(item["name"] == "tcp" and item["status"] == "ok" for item in stages) else "tcp"
        stages.append(_stage(failed_stage, "failed"))
        return _result(False, stages, started, error=str(exc)[:300])
    finally:
        try:
            if transport:
                transport.close()
        finally:
            if sock:
                sock.close()


def _result(ok: bool, stages: list[dict[str, Any]], started: float, **extra: Any) -> dict[str, Any]:
    status = "succeeded" if ok else ("blocked" if any(s.get("status") == "blocked" for s in stages) else "failed")
    return {
        "ok": ok,
        "status": status,
        "stages": stages,
        "duration_ms": int((time.monotonic() - started) * 1000),
        **{k: v for k, v in extra.items() if v not in ("", None)},
    }


_TELNET_IAC = 255
_TELNET_DO = 253
_TELNET_DONT = 254
_TELNET_WILL = 251
_TELNET_WONT = 252
_LOGIN_PROMPT = re.compile(r"(?:login|username|user\s*name)\s*[:：]\s*$", re.IGNORECASE)
_PASSWORD_PROMPT = re.compile(r"password\s*[:：]\s*$", re.IGNORECASE)
_DEVICE_PROMPT = re.compile(r"(?:^|\r?\n)[^\r\n]{0,120}[>#\]]\s*$")


def _telnet_negotiate(sock: socket.socket, data: bytes) -> bytes:
    """Strip Telnet negotiation and reject optional features safely."""
    clean = bytearray()
    index = 0
    while index < len(data):
        if data[index] != _TELNET_IAC:
            clean.append(data[index])
            index += 1
            continue
        if index + 1 >= len(data):
            break
        command = data[index + 1]
        if command == _TELNET_IAC:
            clean.append(_TELNET_IAC)
            index += 2
            continue
        if command in {_TELNET_DO, _TELNET_DONT, _TELNET_WILL, _TELNET_WONT} and index + 2 < len(data):
            option = data[index + 2]
            reply = _TELNET_WONT if command in {_TELNET_DO, _TELNET_DONT} else _TELNET_DONT
            sock.sendall(bytes((_TELNET_IAC, reply, option)))
            index += 3
            continue
        index += 2
    return bytes(clean)


def _telnet_read(sock: socket.socket, *, timeout: float, stop_on_prompt: bool = True) -> str:
    deadline = time.monotonic() + timeout
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        try:
            data = sock.recv(65535)
        except socket.timeout:
            break
        if not data:
            break
        chunks.append(_telnet_negotiate(sock, data))
        text = b"".join(chunks).decode("utf-8", errors="replace")[-200_000:]
        if stop_on_prompt and (_LOGIN_PROMPT.search(text) or _PASSWORD_PROMPT.search(text) or _DEVICE_PROMPT.search(text)):
            break
    return b"".join(chunks).decode("utf-8", errors="replace")[-200_000:]


def _probe_telnet(
    target: DeviceTarget,
    *,
    commands: list[str] | None,
    facts: list[str] | None,
    read: bool,
    timeout: int,
) -> dict[str, Any]:
    stages: list[dict[str, Any]] = []
    started = time.monotonic()
    sock: socket.socket | None = None
    try:
        stages.append(_stage("target", "ok", host=target.host, port=target.port, protocol="telnet", vendor=target.vendor, source_address=target.source_address))
        source = (target.source_address, 0) if target.source_address else None
        sock = socket.create_connection((target.host, target.port), timeout=timeout, source_address=source)
        sock.settimeout(min(float(timeout), 2.0))
        stages.append(_stage("tcp", "ok"))
        credential = target.credential or DeviceCredential(auth_method="none")
        banner = _telnet_read(sock, timeout=min(timeout, 4))
        if _LOGIN_PROMPT.search(banner):
            if not credential.username:
                return _result(False, stages + [_stage("auth", "failed")], started, error="username_required_by_device")
            sock.sendall((credential.username + "\r\n").encode())
            banner += _telnet_read(sock, timeout=min(timeout, 4))
        if _PASSWORD_PROMPT.search(banner):
            if not credential.password:
                return _result(False, stages + [_stage("auth", "failed")], started, error="password_required_by_device")
            sock.sendall((credential.password + "\r\n").encode())
            banner += _telnet_read(sock, timeout=min(timeout, 4))
        # Telnet devices may expose a prompt immediately and require no login.
        if not _DEVICE_PROMPT.search(banner):
            sock.sendall(b"\r\n")
            banner += _telnet_read(sock, timeout=min(timeout, 3))
        if not _DEVICE_PROMPT.search(banner):
            return _result(False, stages + [_stage("prompt", "failed")], started, error="device_prompt_not_detected")
        stages.append(_stage("auth", "ok", method="none" if not credential.username and not credential.password else "password"))
        driver, source = resolve_driver(target.vendor, banner)

        def receive() -> bytes | None:
            try:
                data = sock.recv(65535)
            except socket.timeout:
                return None
            return _telnet_negotiate(sock, data) if data else b""

        session = InteractiveCLISession(
            send=sock.sendall,
            receive=receive,
            driver=driver,
            timeout=timeout,
            initial_text=banner,
        )
        bootstrap = session.bootstrap()
        if not bootstrap.complete:
            return _result(
                False, stages + [_stage("prompt", "failed")], started,
                error=bootstrap.error_code or "device_prompt_not_detected",
                session=bootstrap.as_dict(),
                device_profile=driver.public_profile(detected_from=source),
            )
        profile = session.refine_driver(banner + "\n" + bootstrap.text)
        if not read:
            stages.append(_stage("prompt", "ok"))
            return _result(
                True, stages, started,
                banner=banner[-2000:],
                device_profile=profile,
                session={"prompt": session.prompt, "encoding": session.encoding},
            )
        if facts:
            command_plan = session.driver.commands_for(facts)
            commands = [command for _fact, command in command_plan]
            command_facts = {command: fact for fact, command in command_plan}
        else:
            command_facts = {}
        safe_commands = normalize_read_only_commands(commands, session.driver.vendor)
        paging = session.disable_paging()
        output: dict[str, str] = {}
        command_results: list[dict[str, Any]] = []
        for command in safe_commands:
            command_result = session.run_command(command)
            output[command] = command_result.output
            command_results.append({**command_result.as_dict(), "fact": command_facts.get(command, "")})
        read_ok = all(item.get("complete") and not item.get("error_code") for item in command_results)
        stages.extend((
            _stage("prompt", "ok"),
            _stage("read", "ok" if read_ok else "partial", command_count=len(output)),
        ))
        return _result(
            True, stages, started,
            status="succeeded" if read_ok else "partial",
            output=output,
            facts=session.driver.parse_facts(output, command_facts),
            command_results=command_results,
            device_profile=profile,
            session={
                "prompt": session.prompt,
                "encoding": session.encoding,
                "pagination": paging.as_dict() if paging else None,
            },
            read_ok=read_ok,
        )
    except Exception as exc:
        stages.append(_stage("telnet", "failed"))
        return _result(False, stages, started, error=str(exc)[:300])
    finally:
        if sock:
            sock.close()
