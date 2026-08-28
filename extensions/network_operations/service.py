"""Workspace-scoped read-only network inspection service."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from extensions.network_operations.device_tools import (
    DeviceCredential,
    DeviceTarget,
    is_read_only_command,
    normalize_read_only_commands,
    probe_target,
)
from extensions.sdk import ExtensionDataStore, ExtensionSecretStore
from storage.time_utils import now_iso


EXTENSION_ID = "network.operations"
MAX_INSPECTION_SCHEDULES = 200
INTERNAL_SCAN_LIMIT = 5000
DEFAULT_COMMANDS = {
    "h3c": ["display version", "display device", "display interface brief", "display ip routing-table summary"],
    "huawei": ["display version", "display device", "display interface brief", "display ip routing-table statistics"],
    "cisco": ["show version", "show inventory", "show interfaces status", "show ip route summary"],
    "generic": ["uname -a", "uptime", "df -h", "ip address"],
}
_TASK_CANCEL: dict[str, threading.Event] = {}
_TASK_LOCK = threading.Lock()


STARTER_SCRIPTS: tuple[dict[str, Any], ...] = (
    {"script_id": "starter-h3c-health", "name": "H3C 健康巡检", "description": "采集 H3C 设备版本、CPU、内存、接口和日志摘要。", "vendors": ["h3c"], "commands": ["display version", "display cpu-usage", "display memory", "display interface brief", "display logbuffer | include ERROR|WARN"], "checks": [{"check_id": "log-alert", "name": "日志告警关键字", "description": "日志中出现 ERROR、FATAL 或 CRITICAL。", "severity": "medium", "kind": "output_matches", "pattern": "\\b(?:ERROR|FATAL|CRITICAL)\\b"}], "readonly": True, "builtin": True, "version": 1},
    {"script_id": "starter-huawei-health", "name": "华为健康巡检", "description": "采集华为设备版本、CPU、内存、接口和日志摘要。", "vendors": ["huawei"], "commands": ["display version", "display cpu-usage", "display memory-usage", "display interface brief", "display logbuffer | include ERROR|WARN"], "checks": [{"check_id": "log-alert", "name": "日志告警关键字", "description": "日志中出现 ERROR、FATAL 或 CRITICAL。", "severity": "medium", "kind": "output_matches", "pattern": "\\b(?:ERROR|FATAL|CRITICAL)\\b"}], "readonly": True, "builtin": True, "version": 1},
    {"script_id": "starter-cisco-health", "name": "Cisco 健康巡检", "description": "采集 Cisco 设备版本、CPU、内存、接口和日志摘要。", "vendors": ["cisco"], "commands": ["show version", "show processes cpu", "show memory statistics", "show ip interface brief", "show logging | include ERROR|WARN"], "checks": [{"check_id": "log-alert", "name": "日志告警关键字", "description": "日志中出现 ERROR、FATAL 或 CRITICAL。", "severity": "medium", "kind": "output_matches", "pattern": "\\b(?:ERROR|FATAL|CRITICAL)\\b"}], "readonly": True, "builtin": True, "version": 1},
)

def _script_safe(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record.get(key) for key in ("script_id", "name", "description", "vendors", "commands", "checks", "readonly", "builtin", "version", "created_at", "updated_at") if key in record}

def _script_id(value: str) -> str:
    result = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", result):
        raise ValueError("invalid script_id")
    return result

def _ensure_starter_scripts(workspace_id: str) -> None:
    """Create editable per-workspace starter scripts only once.

    The marker deliberately prevents deleted templates from being recreated.
    """
    store = _store(workspace_id)
    if store.get("script_meta", "starter_scripts_initialized"):
        # Older per-workspace starter records predate deterministic health
        # checks.  Only enrich untouched starter records; custom scripts and
        # any user-owned rule set remain exactly as saved.
        for template in STARTER_SCRIPTS:
            existing = store.get("scripts", template["script_id"])
            if existing and existing.get("source") == "starter" and int(existing.get("version") or 0) == 1 and "checks" not in existing:
                existing["checks"] = list(template["checks"])
                existing["updated_at"] = now_iso()
                store.save("scripts", template["script_id"], existing)
        return
    for template in STARTER_SCRIPTS:
        record = {**dict(template), "readonly": True, "builtin": False, "source": "starter", "created_at": now_iso(), "updated_at": now_iso()}
        store.save("scripts", record["script_id"], record)
    store.save("script_meta", "starter_scripts_initialized", {"initialized_at": now_iso()})

def list_inspection_scripts(workspace_id: str) -> list[dict[str, Any]]:
    _ensure_starter_scripts(workspace_id)
    return [_script_safe(item) for item in _store(workspace_id).list("scripts", limit=200)]

def get_inspection_script(workspace_id: str, script_id: str) -> dict[str, Any] | None:
    _ensure_starter_scripts(workspace_id)
    identifier = _script_id(script_id)
    record = _store(workspace_id).get("scripts", identifier)
    return _script_safe(record) if record else None


_CHECK_SEVERITIES = {"low", "medium", "high", "critical"}


def _normalize_checks(value: Any) -> list[dict[str, str]]:
    """Validate deterministic checks without pretending every vendor has one parser.

    A check is deliberately a small, auditable evidence rule.  It never uses an
    LLM or a local "quality gate" to decide whether an operational condition is
    true: it either matches persisted command output, or it does not.
    """
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 30:
        raise ValueError("checks must be an array containing at most 30 items")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("each check must be an object")
        check_id = str(raw.get("check_id") or "").strip()
        name = str(raw.get("name") or "").strip()
        description = str(raw.get("description") or "").strip()
        severity = str(raw.get("severity") or "medium").strip().lower()
        kind = str(raw.get("kind") or "output_matches").strip()
        pattern = str(raw.get("pattern") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", check_id) or check_id in seen:
            raise ValueError("check_id must be unique and use letters, numbers, _ or -")
        if not name or len(name) > 100 or len(description) > 300:
            raise ValueError("check name or description is invalid")
        if severity not in _CHECK_SEVERITIES or kind != "output_matches" or not pattern or len(pattern) > 240:
            raise ValueError("invalid check severity, kind, or pattern")
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError("invalid check pattern") from exc
        seen.add(check_id)
        normalized.append({"check_id": check_id, "name": name, "description": description, "severity": severity, "kind": kind, "pattern": pattern})
    return normalized

def save_inspection_script(workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    script_id = _script_id(str(payload.get("script_id") or _id("script")))
    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    raw_vendors = payload.get("vendors")
    if not isinstance(raw_vendors, list) or any(not isinstance(item, str) for item in raw_vendors):
        raise ValueError("script vendors must be an array")
    vendors = [item.strip().lower() for item in raw_vendors if item.strip()]
    allowed = {"h3c", "huawei", "cisco", "generic"}
    if not name or len(name) > 80: raise ValueError("script name is required and must be at most 80 characters")
    if not vendors or any(item not in allowed for item in vendors): raise ValueError("invalid script vendors")
    raw_commands = payload.get("commands")
    commands = normalize_read_only_commands(raw_commands)
    for vendor in set(vendors):
        normalize_read_only_commands(commands, vendor)
    existing = _store(workspace_id).get("scripts", script_id) or {}
    checks = _normalize_checks(payload["checks"]) if "checks" in payload else list(existing.get("checks") or [])
    record = {"script_id": script_id, "name": name, "description": description[:300], "vendors": sorted(set(vendors)), "commands": commands, "checks": checks, "readonly": True, "builtin": False, "source": str(existing.get("source") or "custom"), "version": int(existing.get("version") or 0) + 1, "created_at": str(existing.get("created_at") or now_iso()), "updated_at": now_iso()}
    _store(workspace_id).save("scripts", script_id, record)
    return _script_safe(record)

def delete_inspection_script(workspace_id: str, script_id: str) -> bool:
    _ensure_starter_scripts(workspace_id)
    return _store(workspace_id).delete("scripts", _script_id(script_id))

def _resolve_script(workspace_id: str, script_id: str | None) -> dict[str, Any] | None:
    if not script_id: return None
    script = get_inspection_script(workspace_id, str(script_id))
    if not script: raise ValueError("inspection_script_not_found")
    return script


def _store(workspace_id: str) -> ExtensionDataStore:
    return ExtensionDataStore(EXTENSION_ID, workspace_id)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _safe_asset(record: dict[str, Any]) -> dict[str, Any]:
    item = dict(record)
    item.pop("credential_ref", None)
    item.pop("key_ref", None)
    item.pop("key_passphrase_ref", None)
    item["credential_configured"] = bool(record.get("credential_ref") or record.get("key_ref"))
    item["host_key_trusted"] = bool(record.get("host_key_fingerprint"))
    return item


def list_assets(workspace_id: str) -> list[dict[str, Any]]:
    return [_safe_asset(item) for item in _store(workspace_id).list("assets", limit=1000)]


def get_asset(workspace_id: str, asset_id: str, *, include_secret: bool = False) -> dict[str, Any] | None:
    item = _store(workspace_id).get("assets", asset_id)
    if not item:
        return None
    return item if include_secret else _safe_asset(item)


def save_asset(workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    host = str(payload.get("host") or "").strip()
    username = str(payload.get("username") or "").strip()
    if not name or not host or not username:
        raise ValueError("name, host and username are required")
    if not _valid_host(host):
        raise ValueError("invalid host")
    try:
        port = int(payload.get("port") or 22)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid port") from exc
    if port < 1 or port > 65535:
        raise ValueError("invalid port")
    asset_id = str(payload.get("asset_id") or _id("asset"))
    existing = _store(workspace_id).get("assets", asset_id) or {}
    existing_assets = _store(workspace_id).list("assets", limit=1001)
    if not existing and len(existing_assets) >= 1000:
        raise ValueError("extension_asset_quota_exceeded")
    for item in existing_assets:
        if item.get("asset_id") != asset_id and item.get("host") == host and int(item.get("port") or 22) == port:
            raise ValueError("host and port already exist")
    credential_ref = str(existing.get("credential_ref") or payload.get("credential_ref") or "")
    key_ref = str(existing.get("key_ref") or payload.get("key_ref") or "")
    key_passphrase_ref = str(existing.get("key_passphrase_ref") or payload.get("key_passphrase_ref") or "")
    auth_method = str(payload.get("auth_method") or existing.get("auth_method") or "password").strip().lower()
    password = str(payload.get("password") or "")
    private_key = str(payload.get("private_key") or "")
    key_passphrase = str(payload.get("key_passphrase") or payload.get("passphrase") or "")
    if password:
        credential_ref = ExtensionSecretStore(EXTENSION_ID, workspace_id).set(f"asset_{asset_id}", password)
    if private_key:
        key_ref = ExtensionSecretStore(EXTENSION_ID, workspace_id).set(f"asset_{asset_id}_key", private_key)
        auth_method = "private_key"
    if key_passphrase:
        key_passphrase_ref = ExtensionSecretStore(EXTENSION_ID, workspace_id).set(f"asset_{asset_id}_key_passphrase", key_passphrase)
    if auth_method not in {"password", "private_key"}:
        raise ValueError("invalid auth_method")
    record = {
        "asset_id": asset_id,
        "name": name,
        "host": host,
        "port": port,
        "username": username,
        "auth_method": auth_method,
        "vendor": str(payload.get("vendor") or "generic").strip().lower(),
        "device_type": str(payload.get("device_type") or "switch").strip().lower(),
        "region": str(payload.get("region") or "").strip(),
        "tags": [str(item).strip() for item in (payload.get("tags") or []) if str(item).strip()],
        "credential_ref": credential_ref,
        "key_ref": key_ref,
        "key_passphrase_ref": key_passphrase_ref,
        "host_key_fingerprint": str(payload.get("host_key_fingerprint") or existing.get("host_key_fingerprint") or "").strip(),
        "created_at": str(existing.get("created_at") or now_iso()),
        "updated_at": now_iso(),
    }
    _store(workspace_id).save("assets", asset_id, record)
    return _safe_asset(record)


def delete_asset(workspace_id: str, asset_id: str) -> bool:
    existing = get_asset(workspace_id, asset_id, include_secret=True)
    if not existing:
        return False
    reference = str(existing.get("credential_ref") or "")
    if reference:
        ExtensionSecretStore.delete(reference)
    key_reference = str(existing.get("key_ref") or "")
    if key_reference:
        ExtensionSecretStore.delete(key_reference)
    passphrase_reference = str(existing.get("key_passphrase_ref") or "")
    if passphrase_reference:
        ExtensionSecretStore.delete(passphrase_reference)
    return _store(workspace_id).delete("assets", asset_id)


def _valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}[A-Za-z0-9]", host))


def commands_for(asset: dict[str, Any], commands: list[str] | None = None, script: dict[str, Any] | None = None) -> list[str]:
    vendor = str(asset.get("vendor") or "generic").lower()
    if script:
        vendors = set(script.get("vendors") or [])
        if vendor not in vendors:
            raise ValueError(f"script_not_supported_for_vendor:{vendor}")
        selected = script.get("commands") or []
    else:
        selected = commands or DEFAULT_COMMANDS.get(vendor, DEFAULT_COMMANDS["generic"])
    return normalize_read_only_commands(selected, vendor)


def _target_for(asset: dict[str, Any]) -> DeviceTarget:
    password = ExtensionSecretStore.get(str(asset.get("credential_ref") or ""))
    private_key = ExtensionSecretStore.get(str(asset.get("key_ref") or ""))
    passphrase = ExtensionSecretStore.get(str(asset.get("key_passphrase_ref") or ""))
    auth_method = str(asset.get("auth_method") or ("private_key" if private_key else "password")).lower()
    credential = DeviceCredential(
        auth_method=auth_method,
        username=str(asset.get("username") or ""),
        password=password,
        private_key=private_key,
        passphrase=passphrase,
    )
    return DeviceTarget(
        host=str(asset.get("host") or ""),
        port=int(asset.get("port") or 22),
        vendor=str(asset.get("vendor") or "generic"),
        name=str(asset.get("name") or ""),
        expected_fingerprint=str(asset.get("host_key_fingerprint") or ""),
        credential=credential,
    )


def probe_asset(
    workspace_id: str,
    asset_id: str,
    *,
    commands: list[str] | None = None,
    accept_host_key: bool = False,
    read: bool = False,
    timeout: int = 15,
) -> dict[str, Any]:
    asset = get_asset(workspace_id, asset_id, include_secret=True)
    if not asset:
        return {"ok": False, "status": "failed", "error": "asset_not_found"}
    selected = commands_for(asset, commands) if read else []
    result = probe_target(_target_for(asset), commands=selected, accept_host_key=accept_host_key, read=read, timeout=timeout)
    fingerprint = str(result.get("fingerprint") or "")
    if fingerprint and accept_host_key and result.get("status") == "succeeded" and not asset.get("host_key_fingerprint"):
        asset["host_key_fingerprint"] = fingerprint
        asset["updated_at"] = now_iso()
        _store(workspace_id).save("assets", asset_id, asset)
        result["host_key_saved"] = True
    result["asset"] = _safe_asset(asset)
    return result


def collect_ssh(asset: dict[str, Any], commands: list[str], *, timeout: int = 15) -> dict[str, str]:
    result = probe_target(_target_for(asset), commands=commands, read=True, timeout=timeout)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "device connection failed"))
    output = result.get("output") or {}
    return {str(key): str(value) for key, value in output.items()}


def _inspection_assets(workspace_id: str, asset_ids: list[str] | None) -> list[dict[str, Any]]:
    if not isinstance(asset_ids, list) or not asset_ids:
        raise ValueError("asset_ids must be a non-empty array")
    if any(not isinstance(item, str) or not item.strip() for item in asset_ids):
        raise ValueError("asset_ids must contain non-empty strings")
    normalized = [item.strip() for item in asset_ids]
    if len(set(normalized)) != len(normalized):
        raise ValueError("asset_ids must not contain duplicates")
    assets = [get_asset(workspace_id, asset_id, include_secret=True) for asset_id in normalized]
    missing = [asset_id for asset_id, asset in zip(normalized, assets) if asset is None]
    if missing:
        raise ValueError(f"inspection_assets_not_found:{','.join(missing)}")
    return [item for item in assets if item]


def _command_plan(commands: list[str] | None, script: dict[str, Any] | None) -> dict[str, Any]:
    if script:
        return {"mode": "script", "script": _script_safe(script)}
    if commands is not None:
        return {"mode": "inline_commands", "commands": list(commands)}
    return {"mode": "vendor_defaults"}


def _restore_command_plan(task: dict[str, Any]) -> tuple[list[str] | None, dict[str, Any] | None]:
    plan = task.get("command_plan")
    if not isinstance(plan, dict):
        raise ValueError("inspection_command_plan_missing")
    mode = str(plan.get("mode") or "")
    if mode == "script":
        script = plan.get("script")
        if not isinstance(script, dict) or not script.get("script_id"):
            raise ValueError("inspection_script_snapshot_invalid")
        return None, dict(script)
    if mode == "inline_commands":
        commands = plan.get("commands")
        if not isinstance(commands, list):
            raise ValueError("inspection_inline_commands_invalid")
        return list(commands), None
    if mode == "vendor_defaults":
        return None, None
    raise ValueError("inspection_command_plan_invalid")


def _build_inspection_task(assets: list[dict[str, Any]], commands: list[str] | None, script: dict[str, Any] | None, *, job_id: str = "") -> dict[str, Any]:
    for asset in assets:
        commands_for(asset, commands, script)
    task_id = _id("inspection")
    return {
        "task_id": task_id, "job_id": job_id, "status": "queued",
        "asset_ids": [item["asset_id"] for item in assets],
        # Results remain attributable after an intentionally hard-deleted asset.
        # The snapshot is safe: it contains no secret references or credentials.
        "asset_snapshots": {item["asset_id"]: _safe_asset(item) for item in assets},
        "total": len(assets), "completed": 0, "succeeded": 0, "failed": 0,
        "results": {}, "artifact_id": "",
        "command_plan": _command_plan(commands, script),
        "script": _script_safe(script) if script else {
            "script_id": "inline-commands" if commands is not None else "vendor-defaults",
            "name": "临时只读命令" if commands is not None else "厂商默认命令",
            "commands": list(commands or []),
        },
        "created_at": now_iso(), "updated_at": now_iso(),
    }


def _new_inspection_task(workspace_id: str, asset_ids: list[str] | None, commands: list[str] | None, script_id: str, *, job_id: str = "") -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    assets = _inspection_assets(workspace_id, asset_ids)
    script = _resolve_script(workspace_id, script_id)
    task = _build_inspection_task(assets, commands, script, job_id=job_id)
    return task, assets, script


class _DurableCancellation:
    """Cancellation probe backed by the canonical durable job record."""
    def __init__(self, workspace_id: str, job_id: str):
        self.workspace_id = workspace_id
        self.job_id = job_id
    def is_set(self) -> bool:
        from jobs.store import get_job
        job = get_job(self.workspace_id, self.job_id)
        return bool(job and job.cancel_requested)


def start_inspection(
    workspace_id: str,
    asset_ids: list[str] | None = None,
    commands: list[str] | None = None,
    script_id: str = "",
    *,
    collector: Callable[[dict[str, Any], list[str]], dict[str, str]] | None = None,
    background: bool = True,
) -> dict[str, Any]:
    """Synchronous-capable entrypoint for deterministic internal tests.

    Production HTTP and LLM entrypoints use ``enqueue_inspection`` so execution
    always runs through the durable jobs Worker.
    """
    task, assets, script = _new_inspection_task(workspace_id, asset_ids, commands, script_id)
    _store(workspace_id).save("inspections", task["task_id"], task)
    cancel = threading.Event()
    with _TASK_LOCK:
        _TASK_CANCEL[task["task_id"]] = cancel
    args = (workspace_id, task["task_id"], assets, commands, collector or collect_ssh, cancel, script)
    if background:
        threading.Thread(target=_execute_inspection, args=args, name=f"inspection-{task['task_id']}", daemon=True).start()
    else:
        _execute_inspection(*args)
    return get_inspection(workspace_id, task["task_id"]) or task


def _enqueue_prepared_inspection(workspace_id: str, task: dict[str, Any], *, created_by: str) -> dict[str, Any]:
    from jobs.manager import create_job
    job = create_job(
        workspace_id=workspace_id,
        job_type="network_inspection",
        title=f"网络巡检 · {task['script'].get('name') or task['task_id']}",
        payload={"task_id": task["task_id"]},
        created_by=created_by,
        enqueue=False,
    )
    task["job_id"] = job.job_id
    _store(workspace_id).save("inspections", task["task_id"], task)
    try:
        from jobs.manager import enqueue_job
        enqueue_job(workspace_id, job.job_id)
    except Exception:
        task.update({"status": "failed", "error": "inspection_enqueue_failed", "finished_at": now_iso(), "updated_at": now_iso()})
        _store(workspace_id).save("inspections", task["task_id"], task)
        raise
    return get_inspection(workspace_id, task["task_id"]) or task


def enqueue_inspection(workspace_id: str, asset_ids: list[str] | None = None, commands: list[str] | None = None, script_id: str = "", *, created_by: str = "user") -> dict[str, Any]:
    """Create a durable inspection task and queue it on the platform Worker."""
    task, _assets, _script = _new_inspection_task(workspace_id, asset_ids, commands, script_id)
    return _enqueue_prepared_inspection(workspace_id, task, created_by=created_by)


def execute_queued_inspection(workspace_id: str, task_id: str, job_id: str) -> dict[str, Any]:
    task = get_inspection(workspace_id, task_id)
    if not task:
        raise ValueError("inspection_not_found")
    assets = _inspection_assets(workspace_id, list(task.get("asset_ids") or []))
    commands, script = _restore_command_plan(task)
    cancel = _DurableCancellation(workspace_id, job_id)
    _execute_inspection(workspace_id, task_id, assets, commands, collect_ssh, cancel, script)
    return get_inspection(workspace_id, task_id) or task


def _execute_inspection(workspace_id: str, task_id: str, assets: list[dict[str, Any]], commands: list[str] | None, collector: Callable, cancel: Any, script: dict[str, Any] | None = None) -> None:
    store = _store(workspace_id)
    task = store.get("inspections", task_id) or {}
    if cancel.is_set():
        task.update({"status": "cancelled", "finished_at": now_iso(), "updated_at": now_iso()})
        store.save("inspections", task_id, task)
        return
    task.update({"status": "running", "started_at": now_iso(), "updated_at": now_iso()})
    store.save("inspections", task_id, task)
    def run_one(asset: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if cancel.is_set():
            return asset["asset_id"], {"status": "cancelled", "name": asset["name"]}
        started = time.monotonic()
        try:
            selected = commands_for(asset, commands, script)
            raw = collector(asset, selected)
            normalized = json.dumps(raw, ensure_ascii=False, sort_keys=True)
            return asset["asset_id"], {
                "status": "succeeded", "name": asset["name"], "host": asset["host"],
                "commands": selected, "output_hash": hashlib.sha256(normalized.encode()).hexdigest(),
                "_raw_output": raw, "duration_ms": int((time.monotonic() - started) * 1000),
            }
        except Exception as exc:
            return asset["asset_id"], {
                "status": "failed", "name": asset["name"], "host": asset["host"],
                "error": str(exc)[:300], "duration_ms": int((time.monotonic() - started) * 1000),
            }
    workers = min(5, max(1, len(assets)))
    raw_outputs: dict[str, dict[str, str]] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="network-inspection") as pool:
        futures = [pool.submit(run_one, asset) for asset in assets]
        for future in as_completed(futures):
            asset_id, result = future.result()
            raw = result.pop("_raw_output", None)
            if isinstance(raw, dict): raw_outputs[asset_id] = raw
            task["results"][asset_id] = result
            task["completed"] += 1
            task["succeeded"] += int(result["status"] == "succeeded")
            task["failed"] += int(result["status"] == "failed")
            task["updated_at"] = now_iso()
            store.save("inspections", task_id, task)
    task["status"] = (
        "cancelled" if cancel.is_set()
        else "succeeded" if task["failed"] == 0
        else "partial" if task["succeeded"] > 0
        else "failed"
    )
    task["finished_at"] = now_iso()
    task["updated_at"] = now_iso()
    try:
        task["artifact_id"] = _save_evidence_artifact(workspace_id, task, raw_outputs)
        task["findings"] = _derive_findings(workspace_id, task, raw_outputs)
        task["finding_count"] = len(task["findings"])
        store.save("inspections", task_id, task)
    except Exception:
        task.update({"status": "failed", "error": "inspection_evidence_persist_failed", "finished_at": now_iso(), "updated_at": now_iso()})
        store.save("inspections", task_id, task)
        raise
    finally:
        with _TASK_LOCK:
            _TASK_CANCEL.pop(task_id, None)


def _save_evidence_artifact(workspace_id: str, task: dict[str, Any], raw_outputs: dict[str, dict[str, str]]) -> str:
    from artifacts.store import save_artifact
    artifact = save_artifact(
        workspace_id=workspace_id,
        content=json.dumps({**task, "raw_outputs": raw_outputs}, ensure_ascii=False, indent=2),
        artifact_type="output_data",
        title=f"网络巡检证据 {task['task_id']}",
        sensitivity="secret",
        module=EXTENSION_ID,
        capability_id="network_inspection",
        metadata={"inspection_task_id": task["task_id"], "evidence_authority": "status_baseline_inspection"},
        tags=["network", "inspection", "evidence"],
        created_by="extension:network.operations",
    )
    return artifact.artifact_id if artifact else ""


def _finding_id(asset_id: str, category: str, rule_id: str) -> str:
    digest = hashlib.sha256(f"{asset_id}|{category}|{rule_id}".encode()).hexdigest()[:20]
    return f"finding_{digest}"


def _finding_view(record: dict[str, Any]) -> dict[str, Any]:
    """Project a finding without leaking command output or encrypted secrets."""
    allowed = (
        "finding_id", "asset_id", "asset_name", "asset_host", "category", "rule_id",
        "title", "description", "severity", "status", "first_seen_at", "last_seen_at",
        "last_seen_task_id", "evidence", "occurrences", "state_history", "updated_at",
    )
    return {key: record.get(key) for key in allowed if key in record}


def _upsert_finding(
    workspace_id: str,
    *,
    asset: dict[str, Any],
    category: str,
    rule_id: str,
    title: str,
    description: str,
    severity: str,
    task: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Record an evidence-backed observation while preserving human decisions.

    A re-observed resolved finding becomes open again.  An acknowledged or
    suppressed finding remains in its human-selected state; the new evidence is
    visible through ``last_seen_*`` rather than silently overriding that choice.
    """
    store = _store(workspace_id)
    finding_id = _finding_id(str(asset["asset_id"]), category, rule_id)
    previous = store.get("findings", finding_id) or {}
    previous_status = str(previous.get("status") or "")
    status = "open" if previous_status in {"", "resolved"} else previous_status
    occurrences = int(previous.get("occurrences") or 0) + 1
    record = {
        **previous,
        "finding_id": finding_id,
        "asset_id": asset["asset_id"],
        "asset_name": asset.get("name", ""),
        "asset_host": asset.get("host", ""),
        "category": category,
        "rule_id": rule_id,
        "title": title,
        "description": description,
        "severity": severity,
        "status": status,
        "first_seen_at": previous.get("first_seen_at") or now_iso(),
        "last_seen_at": now_iso(),
        "last_seen_task_id": task["task_id"],
        "evidence": evidence,
        "occurrences": occurrences,
        "state_history": list(previous.get("state_history") or []),
        "updated_at": now_iso(),
    }
    store.save("findings", finding_id, record)
    return _finding_view(record)


def _derive_findings(workspace_id: str, task: dict[str, Any], raw_outputs: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """Turn a completed read-only inspection into stable, traceable findings."""
    current_assets = {item["asset_id"]: item for item in list_assets(workspace_id)}
    snapshots = task.get("asset_snapshots") if isinstance(task.get("asset_snapshots"), dict) else {}
    checks = list((task.get("script") or {}).get("checks") or [])
    baseline = next((item for item in list_baselines(workspace_id) if item.get("current") and item.get("confirmed")), None)
    findings: list[dict[str, Any]] = []
    for asset_id, result in sorted((task.get("results") or {}).items()):
        asset = current_assets.get(asset_id) or snapshots.get(asset_id)
        if not asset:
            continue
        result_status = str(result.get("status") or "")
        evidence = {
            "task_id": task["task_id"],
            "artifact_id": task.get("artifact_id", ""),
            "output_hash": result.get("output_hash", ""),
            "result_status": result_status,
        }
        if result_status == "failed":
            findings.append(_upsert_finding(
                workspace_id, asset=asset, category="connectivity", rule_id="inspection-failed",
                title="设备巡检未完成", description="设备无法完成本次只读巡检；请结合连接阶段和凭据状态人工核查。",
                severity="high", task=task, evidence={**evidence, "error": str(result.get("error") or "")[:240]},
            ))
            continue
        raw = raw_outputs.get(asset_id) or {}
        joined_output = "\n".join(f"{command}\n{output}" for command, output in sorted(raw.items()))
        for check in checks:
            if re.search(str(check["pattern"]), joined_output, re.IGNORECASE):
                findings.append(_upsert_finding(
                    workspace_id, asset=asset, category="inspection_rule", rule_id=str(check["check_id"]),
                    title=str(check["name"]), description=str(check["description"]), severity=str(check["severity"]),
                    task=task, evidence={**evidence, "check_id": check["check_id"], "check_version": int((task.get("script") or {}).get("version") or 0)},
                ))
        before = (baseline or {}).get("devices", {}).get(asset_id) if baseline else None
        after = {"status": result_status, "output_hash": result.get("output_hash", "")}
        if before is not None and before != after:
            findings.append(_upsert_finding(
                workspace_id, asset=asset, category="baseline_change", rule_id="state-diff",
                title="状态与已确认基线不一致", description="本次巡检输出或设备状态与当前人工确认基线不同；该结果需要人工判断是否为预期变更。",
                severity="medium", task=task, evidence={**evidence, "baseline_id": baseline.get("baseline_id", ""), "before": before, "after": after},
            ))
    return findings


def list_findings(
    workspace_id: str,
    *,
    status: str = "",
    severity: str = "",
    asset_id: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    records = _store(workspace_id).list("findings", limit=max(1, min(limit, 1000)))
    filtered = [record for record in records if (
        (not status or str(record.get("status") or "") == status)
        and (not severity or str(record.get("severity") or "") == severity)
        and (not asset_id or str(record.get("asset_id") or "") == asset_id)
    )]
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    # Stable two-pass ordering: current high-severity findings first, newest
    # evidence first within the same business priority.
    filtered.sort(key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)
    filtered.sort(key=lambda item: (str(item.get("status") or "") not in {"open", "acknowledged"}, severity_rank.get(str(item.get("severity") or ""), 9)))
    return [_finding_view(record) for record in filtered]


def update_finding_state(workspace_id: str, finding_id: str, action: str, *, comment: str = "", actor: str = "user") -> dict[str, Any]:
    transitions = {"acknowledge": "acknowledged", "resolve": "resolved", "suppress": "suppressed", "reopen": "open"}
    target = transitions.get(str(action or "").strip().lower())
    if not target:
        raise ValueError("unsupported finding action")
    store = _store(workspace_id)
    finding = store.get("findings", finding_id)
    if not finding:
        raise ValueError("finding_not_found")
    note = str(comment or "").strip()
    if len(note) > 500:
        raise ValueError("finding comment must be at most 500 characters")
    history = list(finding.get("state_history") or [])
    history.append({"from": str(finding.get("status") or "open"), "to": target, "action": action, "comment": note, "actor": actor, "at": now_iso()})
    finding.update({"status": target, "state_history": history[-50:], "updated_at": now_iso()})
    store.save("findings", finding_id, finding)
    return _finding_view(finding)


def list_inspections(workspace_id: str) -> list[dict[str, Any]]:
    return _store(workspace_id).list("inspections", limit=200)


def get_inspection(workspace_id: str, task_id: str) -> dict[str, Any] | None:
    return _store(workspace_id).get("inspections", task_id)


def retry_inspection(workspace_id: str, task_id: str) -> dict[str, Any]:
    task = get_inspection(workspace_id, task_id)
    if not task or task.get("status") not in {"failed", "cancelled", "partial"}:
        raise ValueError("retryable inspection task is required")
    commands, script = _restore_command_plan(task)
    assets = _inspection_assets(workspace_id, list(task.get("asset_ids") or []))
    retried = _enqueue_prepared_inspection(
        workspace_id,
        _build_inspection_task(assets, commands, script),
        created_by="retry",
    )
    retried["retry_of_task_id"] = task_id
    _store(workspace_id).save("inspections", retried["task_id"], retried)
    return retried


def reconcile_interrupted_inspections() -> int:
    """Mirror canonical reconciled job state back to inspection task state."""
    from backend.core.identity import get_user
    from jobs.store import get_job
    from storage.principal import known_storage_principals, storage_principal
    from storage.workspace_store import list_workspace_ids
    reconciled = 0
    workspace_ids = list_workspace_ids(include_system=False) or ["default"]
    for principal in known_storage_principals() or [""]:
        identity = get_user(principal)
        scoped_workspaces = list(identity.get("workspace_ids") or []) if isinstance(identity, dict) else workspace_ids
        with storage_principal(principal):
            for workspace_id in sorted(set(scoped_workspaces)):
                store = _store(workspace_id)
                for task in store.list("inspections", limit=INTERNAL_SCAN_LIMIT):
                    if task.get("status") not in {"queued", "running"}:
                        continue
                    job_id = str(task.get("job_id") or "")
                    if not job_id:
                        continue
                    job = get_job(workspace_id, job_id)
                    if not job or job.status not in {"failed", "cancelled"}:
                        continue
                    task.update({
                        "status": "cancelled" if job.status == "cancelled" else "failed",
                        "error": "" if job.status == "cancelled" else str(job.error or "backend_restart_during_job"),
                        "finished_at": str(job.finished_at or now_iso()),
                        "updated_at": now_iso(),
                    })
                    store.save("inspections", task["task_id"], task)
                    reconciled += 1
    return reconciled


def list_inspection_schedules(workspace_id: str) -> list[dict[str, Any]]:
    return _store(workspace_id).list("schedules", limit=200)


def save_inspection_schedule(workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    raw_interval = payload.get("interval_minutes", 60)
    try:
        interval_minutes = int(raw_interval)
    except (TypeError, ValueError):
        raise ValueError("interval_minutes must be an integer")
    if not 5 <= interval_minutes <= 10080:
        raise ValueError("interval_minutes must be between 5 and 10080")
    asset_ids = payload.get("asset_ids") or []
    if not isinstance(asset_ids, list) or not all(isinstance(item, str) for item in asset_ids):
        raise ValueError("asset_ids must be an array")
    # Validate selection and script before a schedule can be persisted.
    assets = _inspection_assets(workspace_id, asset_ids)
    script = _resolve_script(workspace_id, str(payload.get("script_id") or ""))
    if not script:
        raise ValueError("inspection_script_required")
    for asset in assets: commands_for(asset, None, script)
    schedule_id = str(payload.get("schedule_id") or _id("schedule"))
    store = _store(workspace_id)
    existing = store.get("schedules", schedule_id) or {}
    if not existing and len(store.list("schedules", limit=MAX_INSPECTION_SCHEDULES + 1)) >= MAX_INSPECTION_SCHEDULES:
        raise ValueError("inspection_schedule_limit_reached")
    now = time.time()
    record = {
        "schedule_id": schedule_id,
        "name": str(payload.get("name") or script.get("name") or "计划巡检").strip()[:120] or "计划巡检",
        "asset_ids": [item["asset_id"] for item in assets],
        "script_id": script["script_id"],
        "script_name": script.get("name", ""),
        "interval_minutes": interval_minutes,
        "enabled": bool(payload.get("enabled", True)),
        "next_run_at_epoch": float(existing.get("next_run_at_epoch") or (now + interval_minutes * 60)),
        "last_task_id": str(existing.get("last_task_id") or ""),
        "last_error": str(existing.get("last_error") or ""),
        "created_at": existing.get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }
    store.save("schedules", schedule_id, record)
    return record


def delete_inspection_schedule(workspace_id: str, schedule_id: str) -> bool:
    return _store(workspace_id).delete("schedules", schedule_id)


def run_due_inspection_schedules(now_epoch: float | None = None) -> dict[str, int]:
    """Tick schedules from the existing durable Worker; never execute SSH inline."""
    from backend.core.identity import get_user
    from storage.locking import FileLock
    from storage.principal import known_storage_principals, storage_principal
    from storage.workspace_store import list_workspace_ids
    now_epoch = float(now_epoch if now_epoch is not None else time.time())
    queued = failed = 0
    all_workspace_ids = list_workspace_ids(include_system=False) or ["default"]
    for principal in known_storage_principals() or [""]:
        identity = get_user(principal)
        workspace_ids = list(identity.get("workspace_ids") or []) if isinstance(identity, dict) else all_workspace_ids
        with storage_principal(principal):
            for workspace_id in sorted(set(workspace_ids)):
                store = _store(workspace_id)
                for listed in store.list("schedules", limit=INTERNAL_SCAN_LIMIT):
                    schedule_id = str(listed.get("schedule_id") or "")
                    if not schedule_id:
                        continue
                    try:
                        with FileLock(store.root() / "schedules" / f"{schedule_id}.tick.lock", timeout=0):
                            schedule = store.get("schedules", schedule_id) or {}
                            if not schedule.get("enabled") or float(schedule.get("next_run_at_epoch") or 0) > now_epoch:
                                continue
                            try:
                                task = enqueue_inspection(workspace_id, list(schedule.get("asset_ids") or []), script_id=str(schedule.get("script_id") or ""), created_by="schedule")
                                schedule.update({"last_task_id": task["task_id"], "last_error": "", "next_run_at_epoch": now_epoch + int(schedule["interval_minutes"]) * 60, "updated_at": now_iso()})
                                queued += 1
                            except Exception as exc:
                                schedule.update({"last_error": str(exc)[:240], "next_run_at_epoch": now_epoch + min(300, int(schedule.get("interval_minutes") or 5) * 60), "updated_at": now_iso()})
                                failed += 1
                            store.save("schedules", schedule_id, schedule)
                    except TimeoutError:
                        continue
    return {"queued": queued, "failed": failed}


def cancel_inspection(workspace_id: str, task_id: str) -> bool:
    task = get_inspection(workspace_id, task_id)
    if not task or task.get("status") not in {"queued", "running"}:
        return False
    job_id = str(task.get("job_id") or "")
    if job_id:
        try:
            from jobs.manager import cancel_job
            job = cancel_job(workspace_id, job_id)
        except ValueError:
            return False
        task["cancel_requested"] = True
        task["updated_at"] = now_iso()
        if job.status == "cancelled":
            task.update({"status": "cancelled", "finished_at": now_iso()})
        _store(workspace_id).save("inspections", task_id, task)
        return True
    with _TASK_LOCK:
        event = _TASK_CANCEL.get(task_id)
    if event:
        event.set()
    task["cancel_requested"] = True
    task["updated_at"] = now_iso()
    _store(workspace_id).save("inspections", task_id, task)
    return True


def create_baseline(workspace_id: str, task_id: str, *, confirm: bool = False) -> dict[str, Any]:
    task = get_inspection(workspace_id, task_id)
    if not task or task.get("status") not in {"succeeded", "partial"}:
        raise ValueError("completed inspection task is required")
    baseline_id = _id("baseline")
    baseline = {
        "baseline_id": baseline_id, "task_id": task_id, "confirmed": bool(confirm),
        "current": False, "artifact_id": task.get("artifact_id", ""),
        "devices": {asset_id: {"status": result.get("status"), "output_hash": result.get("output_hash", "")} for asset_id, result in task.get("results", {}).items()},
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    _store(workspace_id).save("baselines", baseline_id, baseline)
    if confirm:
        return confirm_baseline(workspace_id, baseline_id)
    return baseline


def confirm_baseline(workspace_id: str, baseline_id: str) -> dict[str, Any]:
    store = _store(workspace_id)
    target = store.get("baselines", baseline_id)
    if not target:
        raise ValueError("baseline not found")
    for item in store.list("baselines", limit=1000):
        if item.get("current"):
            item["current"] = False
            item["updated_at"] = now_iso()
            store.save("baselines", item["baseline_id"], item)
    target.update({"confirmed": True, "current": True, "confirmed_at": now_iso(), "updated_at": now_iso()})
    store.save("baselines", baseline_id, target)
    return target


def list_baselines(workspace_id: str) -> list[dict[str, Any]]:
    return _store(workspace_id).list("baselines", limit=200)


def inspection_evidence_summary(workspace_id: str, task_id: str) -> dict[str, Any]:
    """Return a safe evidence index without disclosing raw SSH output."""
    task = get_inspection(workspace_id, task_id)
    if not task:
        raise ValueError("inspection_not_found")
    devices = []
    for asset_id, result in sorted((task.get("results") or {}).items()):
        devices.append({
            "asset_id": asset_id,
            "name": result.get("name", ""),
            "host": result.get("host", ""),
            "status": result.get("status", ""),
            "command_count": len(result.get("commands") or []),
            "output_hash": result.get("output_hash", ""),
            "duration_ms": result.get("duration_ms", 0),
            "error": result.get("error", ""),
        })
    return {
        "ok": True,
        "task_id": task_id,
        "artifact_id": task.get("artifact_id", ""),
        "artifact_sensitivity": "secret",
        "devices": devices,
    }


def diff_against_current(workspace_id: str, task_id: str) -> dict[str, Any]:
    current = next((item for item in list_baselines(workspace_id) if item.get("current") and item.get("confirmed")), None)
    task = get_inspection(workspace_id, task_id)
    if not current or not task:
        raise ValueError("current baseline and inspection task are required")
    changes = []
    all_ids = sorted(set(current.get("devices", {})) | set(task.get("results", {})))
    for asset_id in all_ids:
        before = current.get("devices", {}).get(asset_id)
        result = task.get("results", {}).get(asset_id)
        after = {"status": result.get("status"), "output_hash": result.get("output_hash", "")} if result else None
        if before != after:
            changes.append({"asset_id": asset_id, "before": before, "after": after})
    return {"ok": True, "baseline_id": current["baseline_id"], "task_id": task_id, "changed": bool(changes), "changes": changes}


def overview(workspace_id: str) -> dict[str, Any]:
    assets = list_assets(workspace_id)
    inspections = list_inspections(workspace_id)
    baselines = list_baselines(workspace_id)
    findings = list_findings(workspace_id)
    active_findings = [item for item in findings if item.get("status") in {"open", "acknowledged"}]
    by_severity = {severity: sum(1 for item in active_findings if item.get("severity") == severity) for severity in ("critical", "high", "medium", "low")}
    latest = inspections[0] if inspections else None
    return {
        "ok": True, "assets": len(assets), "inspections": len(inspections),
        "current_baseline": next((item for item in baselines if item.get("current")), None),
        "latest_inspection": latest,
        "health": {
            "registered_assets": len(assets),
            "assets_with_credentials": sum(1 for item in assets if item.get("credential_configured")),
            "active_findings": len(active_findings),
            "findings_by_severity": by_severity,
            "latest_inspection_status": str((latest or {}).get("status") or "not_started"),
            "latest_inspection_at": str((latest or {}).get("finished_at") or (latest or {}).get("created_at") or ""),
        },
    }
