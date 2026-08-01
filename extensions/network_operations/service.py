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

from extensions.sdk import ExtensionDataStore, ExtensionSecretStore
from storage.time_utils import now_iso


EXTENSION_ID = "network.operations"
DEFAULT_COMMANDS = {
    "h3c": ["display version", "display device", "display interface brief", "display ip routing-table summary"],
    "huawei": ["display version", "display device", "display interface brief", "display ip routing-table statistics"],
    "cisco": ["show version", "show inventory", "show interfaces status", "show ip route summary"],
    "generic": ["uname -a", "uptime", "df -h", "ip address"],
}
_WRITE_PATTERNS = re.compile(
    r"(^|\s)(undo|delete|remove|erase|format|reload|reboot|shutdown|write|copy|configure|system-view|enable|install|upgrade|reset|clear)(\s|$)",
    re.IGNORECASE,
)
_TASK_CANCEL: dict[str, threading.Event] = {}
_TASK_LOCK = threading.Lock()


def _store(workspace_id: str) -> ExtensionDataStore:
    return ExtensionDataStore(EXTENSION_ID, workspace_id)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _safe_asset(record: dict[str, Any]) -> dict[str, Any]:
    item = dict(record)
    item.pop("credential_ref", None)
    item["credential_configured"] = bool(record.get("credential_ref"))
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
    for item in _store(workspace_id).list("assets", limit=1000):
        if item.get("asset_id") != asset_id and item.get("host") == host and int(item.get("port") or 22) == port:
            raise ValueError("host and port already exist")
    credential_ref = str(existing.get("credential_ref") or payload.get("credential_ref") or "")
    password = str(payload.get("password") or "")
    if password:
        credential_ref = ExtensionSecretStore(EXTENSION_ID, workspace_id).set(f"asset_{asset_id}", password)
    record = {
        "asset_id": asset_id,
        "name": name,
        "host": host,
        "port": port,
        "username": username,
        "vendor": str(payload.get("vendor") or "generic").strip().lower(),
        "device_type": str(payload.get("device_type") or "switch").strip().lower(),
        "region": str(payload.get("region") or "").strip(),
        "tags": [str(item).strip() for item in (payload.get("tags") or []) if str(item).strip()],
        "credential_ref": credential_ref,
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
    return _store(workspace_id).delete("assets", asset_id)


def _valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}[A-Za-z0-9]", host))


def is_read_only_command(command: str) -> bool:
    value = str(command or "").strip()
    return bool(value and "\n" not in value and "\r" not in value and ";" not in value and not _WRITE_PATTERNS.search(value))


def commands_for(asset: dict[str, Any], commands: list[str] | None = None) -> list[str]:
    selected = commands or DEFAULT_COMMANDS.get(str(asset.get("vendor") or "generic").lower(), DEFAULT_COMMANDS["generic"])
    safe = [str(command).strip() for command in selected if is_read_only_command(str(command))]
    if len(safe) != len(selected) or not safe:
        raise ValueError("inspection commands must be non-empty and read-only")
    return safe[:20]


def collect_ssh(asset: dict[str, Any], commands: list[str], *, timeout: int = 15) -> dict[str, str]:
    import paramiko

    password = ExtensionSecretStore.get(str(asset.get("credential_ref") or ""))
    if not password:
        raise RuntimeError("credential is not configured")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=str(asset["host"]), port=int(asset.get("port") or 22),
        username=str(asset["username"]), password=password,
        timeout=timeout, auth_timeout=timeout, banner_timeout=timeout,
        look_for_keys=False, allow_agent=False,
    )
    try:
        output: dict[str, str] = {}
        for command in commands:
            _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            text = stdout.read().decode("utf-8", errors="replace")
            error = stderr.read().decode("utf-8", errors="replace")
            output[command] = (text + (f"\n{error}" if error else ""))[:200_000]
        return output
    finally:
        client.close()


def start_inspection(
    workspace_id: str,
    asset_ids: list[str] | None = None,
    commands: list[str] | None = None,
    *,
    collector: Callable[[dict[str, Any], list[str]], dict[str, str]] | None = None,
    background: bool = True,
) -> dict[str, Any]:
    assets = [get_asset(workspace_id, asset_id, include_secret=True) for asset_id in (asset_ids or [])]
    assets = [item for item in assets if item] if asset_ids else [get_asset(workspace_id, item["asset_id"], include_secret=True) for item in list_assets(workspace_id)]
    assets = [item for item in assets if item]
    if not assets:
        raise ValueError("no assets selected")
    task_id = _id("inspection")
    task = {
        "task_id": task_id,
        "status": "queued" if background else "running",
        "asset_ids": [item["asset_id"] for item in assets],
        "total": len(assets), "completed": 0, "succeeded": 0, "failed": 0,
        "results": {}, "artifact_id": "", "created_at": now_iso(), "updated_at": now_iso(),
    }
    _store(workspace_id).save("inspections", task_id, task)
    cancel = threading.Event()
    with _TASK_LOCK:
        _TASK_CANCEL[task_id] = cancel
    args = (workspace_id, task_id, assets, commands, collector or collect_ssh, cancel)
    if background:
        threading.Thread(target=_execute_inspection, args=args, name=f"inspection-{task_id}", daemon=True).start()
    else:
        _execute_inspection(*args)
    return get_inspection(workspace_id, task_id) or task


def _execute_inspection(workspace_id: str, task_id: str, assets: list[dict[str, Any]], commands: list[str] | None, collector: Callable, cancel: threading.Event) -> None:
    store = _store(workspace_id)
    task = store.get("inspections", task_id) or {}
    task.update({"status": "running", "started_at": now_iso(), "updated_at": now_iso()})
    store.save("inspections", task_id, task)

    def run_one(asset: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if cancel.is_set():
            return asset["asset_id"], {"status": "cancelled", "name": asset["name"]}
        selected = commands_for(asset, commands)
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
