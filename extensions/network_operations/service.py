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
DEFAULT_COMMANDS = {
    "h3c": ["display version", "display device", "display interface brief", "display ip routing-table summary"],
    "huawei": ["display version", "display device", "display interface brief", "display ip routing-table statistics"],
    "cisco": ["show version", "show inventory", "show interfaces status", "show ip route summary"],
    "generic": ["uname -a", "uptime", "df -h", "ip address"],
}
_TASK_CANCEL: dict[str, threading.Event] = {}
_TASK_LOCK = threading.Lock()


STARTER_SCRIPTS: tuple[dict[str, Any], ...] = (
    {"script_id": "starter-h3c-health", "name": "H3C 健康巡检", "description": "采集 H3C 设备版本、CPU、内存、接口和日志摘要。", "vendors": ["h3c"], "commands": ["display version", "display cpu-usage", "display memory", "display interface brief", "display logbuffer | include ERROR|WARN"], "readonly": True, "builtin": True, "version": 1},
    {"script_id": "starter-huawei-health", "name": "华为健康巡检", "description": "采集华为设备版本、CPU、内存、接口和日志摘要。", "vendors": ["huawei"], "commands": ["display version", "display cpu-usage", "display memory-usage", "display interface brief", "display logbuffer | include ERROR|WARN"], "readonly": True, "builtin": True, "version": 1},
    {"script_id": "starter-cisco-health", "name": "Cisco 健康巡检", "description": "采集 Cisco 设备版本、CPU、内存、接口和日志摘要。", "vendors": ["cisco"], "commands": ["show version", "show processes cpu", "show memory statistics", "show ip interface brief", "show logging | include ERROR|WARN"], "readonly": True, "builtin": True, "version": 1},
)

def _script_safe(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record.get(key) for key in ("script_id", "name", "description", "vendors", "commands", "readonly", "builtin", "version", "created_at", "updated_at") if key in record}

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
    record = {"script_id": script_id, "name": name, "description": description[:300], "vendors": sorted(set(vendors)), "commands": commands, "readonly": True, "builtin": False, "source": str(existing.get("source") or "custom"), "version": int(existing.get("version") or 0) + 1, "created_at": str(existing.get("created_at") or now_iso()), "updated_at": now_iso()}
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


def start_inspection(
    workspace_id: str,
    asset_ids: list[str] | None = None,
    commands: list[str] | None = None,
    script_id: str = "",
    *,
    collector: Callable[[dict[str, Any], list[str]], dict[str, str]] | None = None,
    background: bool = True,
) -> dict[str, Any]:
    assets = [get_asset(workspace_id, asset_id, include_secret=True) for asset_id in (asset_ids or [])]
    assets = [item for item in assets if item] if asset_ids else [get_asset(workspace_id, item["asset_id"], include_secret=True) for item in list_assets(workspace_id)]
    assets = [item for item in assets if item]
    if not assets:
        raise ValueError("no assets selected")
    script = _resolve_script(workspace_id, script_id)
    for asset in assets:
        commands_for(asset, commands, script)
    task_id = _id("inspection")
    task = {
        "task_id": task_id,
        "status": "queued" if background else "running",
        "asset_ids": [item["asset_id"] for item in assets],
        "total": len(assets), "completed": 0, "succeeded": 0, "failed": 0,
        "results": {}, "artifact_id": "", "script": _script_safe(script) if script else {"script_id": "legacy-default", "name": "厂商默认命令", "commands": commands or []}, "created_at": now_iso(), "updated_at": now_iso(),
    }
    _store(workspace_id).save("inspections", task_id, task)
    cancel = threading.Event()
    with _TASK_LOCK:
        _TASK_CANCEL[task_id] = cancel
    args = (workspace_id, task_id, assets, commands, collector or collect_ssh, cancel, script)
    if background:
        threading.Thread(target=_execute_inspection, args=args, name=f"inspection-{task_id}", daemon=True).start()
    else:
        _execute_inspection(*args)
    return get_inspection(workspace_id, task_id) or task


def _execute_inspection(workspace_id: str, task_id: str, assets: list[dict[str, Any]], commands: list[str] | None, collector: Callable, cancel: threading.Event, script: dict[str, Any] | None = None) -> None:
    store = _store(workspace_id)
    task = store.get("inspections", task_id) or {}
    task.update({"status": "running", "started_at": now_iso(), "updated_at": now_iso()})
    store.save("inspections", task_id, task)

    def run_one(asset: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if cancel.is_set():
            return asset["asset_id"], {"status": "cancelled", "name": asset["name"]}
        selected = commands_for(asset, commands, script)
        started = time.monotonic()
        try:
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
            if isinstance(raw, dict):
                raw_outputs[asset_id] = raw
            task["results"][asset_id] = result
            task["completed"] += 1
            task["succeeded"] += int(result["status"] == "succeeded")
            task["failed"] += int(result["status"] == "failed")
            task["updated_at"] = now_iso()
            store.save("inspections", task_id, task)
    task["status"] = "cancelled" if cancel.is_set() else ("succeeded" if task["failed"] == 0 else "partial")
    task["finished_at"] = now_iso()
    task["updated_at"] = now_iso()
    task["artifact_id"] = _save_evidence_artifact(workspace_id, task, raw_outputs)
    store.save("inspections", task_id, task)
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


def list_inspections(workspace_id: str) -> list[dict[str, Any]]:
    return _store(workspace_id).list("inspections", limit=200)


def get_inspection(workspace_id: str, task_id: str) -> dict[str, Any] | None:
    return _store(workspace_id).get("inspections", task_id)


def cancel_inspection(workspace_id: str, task_id: str) -> bool:
    task = get_inspection(workspace_id, task_id)
    if not task or task.get("status") not in {"queued", "running"}:
        return False
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
    return {
        "ok": True, "assets": len(assets), "inspections": len(inspections),
        "current_baseline": next((item for item in baselines if item.get("current")), None),
        "latest_inspection": inspections[0] if inspections else None,
    }
