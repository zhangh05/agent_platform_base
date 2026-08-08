"""Governed read-only device connectivity helpers."""

from __future__ import annotations

import base64
import io
import re
import socket
import time
from dataclasses import dataclass
from typing import Any

from core.tools.schemas import ToolInvocation


READ_ONLY_DENY = re.compile(
    r"(^|\s)(undo|delete|remove|erase|format|reload|reboot|shutdown|write|copy|configure|system-view|enable|install|upgrade|reset|clear)(\s|$)",
    re.IGNORECASE,
)

PAGING_COMMANDS = {
    "h3c": "screen-length disable",
    "huawei": "screen-length 0 temporary",
    "cisco": "terminal length 0",
}


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
    vendor: str = "generic"
    name: str = ""
    expected_fingerprint: str = ""
    credential: DeviceCredential | None = None


def fingerprint_for_key(key: Any) -> str:
    digest = base64.b64encode(__import__("hashlib").sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{digest}"


def is_read_only_command(command: str) -> bool:
    value = str(command or "").strip()
    return bool(value and "\n" not in value and "\r" not in value and ";" not in value and not READ_ONLY_DENY.search(value))


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


def _read_channel(channel: Any, *, idle: float = 0.35, timeout: float = 6.0, limit: int = 200_000) -> str:
    chunks: list[str] = []
    deadline = time.monotonic() + timeout
    idle_deadline = time.monotonic() + idle
    while time.monotonic() < deadline and len("".join(chunks)) < limit:
        if channel.recv_ready():
            data = channel.recv(65535)
            if not data:
                break
            chunks.append(data.decode("utf-8", errors="replace"))
            idle_deadline = time.monotonic() + idle
            continue
        if chunks and time.monotonic() >= idle_deadline:
            break
        time.sleep(0.05)
    return "".join(chunks)[-limit:]


def _run_shell_commands(transport: Any, vendor: str, commands: list[str], timeout: int) -> dict[str, str]:
    channel = transport.open_session(timeout=timeout)
    channel.get_pty(width=200, height=80)
    channel.invoke_shell()
    channel.settimeout(timeout)
    _read_channel(channel, timeout=min(timeout, 5))

    pager = PAGING_COMMANDS.get(vendor.lower())
    if pager:
        channel.send(pager + "\n")
        _read_channel(channel, timeout=min(timeout, 5))

    output: dict[str, str] = {}
    for command in commands:
        channel.send(command + "\n")
        text = _read_channel(channel, timeout=timeout)
        output[command] = text[:200_000]
    channel.close()
    return output


def probe_target(
    target: DeviceTarget,
    *,
    commands: list[str] | None = None,
    accept_host_key: bool = False,
    read: bool = False,
    timeout: int = 15,
) -> dict[str, Any]:
    import paramiko

    stages: list[dict[str, Any]] = []
    started = time.monotonic()
    sock: socket.socket | None = None
    transport: Any = None
    try:
        stages.append(_stage("target", "ok", host=target.host, port=target.port, vendor=target.vendor))
        sock = socket.create_connection((target.host, target.port), timeout=timeout)
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
            stages.append(_stage("prompt", "skipped"))
            return _result(True, stages, started, fingerprint=fingerprint)

        safe_commands = [str(command).strip() for command in (commands or []) if is_read_only_command(str(command))]
        if not safe_commands or len(safe_commands) != len(commands or []):
            stages.append(_stage("read", "failed"))
            return _result(False, stages, started, error="commands_must_be_read_only", fingerprint=fingerprint)
        output = _run_shell_commands(transport, target.vendor, safe_commands[:20], timeout)
        stages.append(_stage("prompt", "ok"))
        stages.append(_stage("read", "ok", command_count=len(output)))
        return _result(True, stages, started, fingerprint=fingerprint, output=output)
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


def handle_device_manage(inv: ToolInvocation) -> dict[str, Any]:
    args = inv.arguments or {}
    action = str(args.get("action") or "probe").lower()
    if action not in {"probe", "read"}:
        return {"ok": False, "error": "unsupported action for device.manage; expected probe|read"}
    if args.get("asset_id"):
        from extensions.network_operations import service
        return service.probe_asset(
            inv.workspace_id or str(args.get("workspace_id") or ""),
            str(args.get("asset_id") or ""),
            commands=[str(item) for item in (args.get("commands") or [])],
            accept_host_key=bool(args.get("accept_host_key")),
            read=action == "read",
            timeout=int(args.get("timeout") or 15),
        )

    credential = DeviceCredential(
        auth_method=str(args.get("auth_method") or "password"),
        username=str(args.get("username") or ""),
        password=str(args.get("password") or ""),
        private_key=str(args.get("private_key") or ""),
        passphrase=str(args.get("passphrase") or ""),
    )
    target = DeviceTarget(
        host=str(args.get("host") or ""),
        port=int(args.get("port") or 22),
        vendor=str(args.get("vendor") or "generic"),
        expected_fingerprint=str(args.get("host_key_fingerprint") or ""),
        credential=credential,
    )
    if not target.host:
        return {"ok": False, "error": "host is required"}
    return probe_target(
        target,
        commands=[str(item) for item in (args.get("commands") or [])],
        accept_host_key=bool(args.get("accept_host_key")),
        read=action == "read",
        timeout=int(args.get("timeout") or 15),
    )
