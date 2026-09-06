"""Workspace-scoped device, Skill authorization and network execution service."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import time
import uuid
from concurrent.futures import as_completed
from functools import wraps
from typing import Any, Callable

from extensions.network_operations.device_tools import (
    DeviceCredential,
    DeviceTarget,
    normalize_read_only_commands,
    normalize_configuration_commands,
    probe_target,
    resolve_source_address,
)
from extensions.network_operations.device_drivers import SEMANTIC_FACTS, semantic_catalog
from extensions.sdk import ExtensionDataStore, ExtensionSecretStore
from storage.locking import FileLock
from storage.principal import ContextThreadPoolExecutor
from storage.time_utils import now_iso


EXTENSION_ID = "network.operations"
SKILL_BASE_TOOL_ID = "network.operations.device.manage"
SKILL_TOOL_IDS = frozenset({
    "network.operations.devices_read",
    "network.operations.skills_read",
    "network.operations.context_read",
    "network.operations.device.manage",
    "network.operations.inspection",
})
INTERNAL_SCAN_LIMIT = 5000


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


def _connection_lock(workspace_id: str) -> FileLock:
    """Serialize endpoint identity changes across workers and processes."""
    return FileLock(_store(workspace_id).root() / ".connections.lock", timeout=10.0)


def _connection_transaction(func):
    """Serialize related device, connection and Skill mutations, never network IO."""
    @wraps(func)
    def mutate(workspace_id, *args, **kwargs):
        with _connection_lock(workspace_id):
            return func(workspace_id, *args, **kwargs)
    return mutate


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _public_connection(record: dict[str, Any]) -> dict[str, Any]:
    item = {key: value for key, value in record.items() if not key.endswith("_ref") and key not in {"revision", "probe_id"}}
    item["credential_configured"] = bool(
        record.get("password_ref") or record.get("private_key_ref") or record.get("auth_method") == "none"
    )
    item["verified"] = str(record.get("status") or "") == "connected"
    return item


def list_regions(workspace_id: str) -> list[dict[str, Any]]:
    return _store(workspace_id).list("regions", limit=500)


@_connection_transaction
def save_region(workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ValueError("region name is required and must be at most 80 characters")
    region_id = str(payload.get("region_id") or _id("region"))
    existing = _store(workspace_id).get("regions", region_id) or {}
    parent_id = str(payload.get("parent_id") or "").strip()
    if parent_id and (parent_id == region_id or not _store(workspace_id).get("regions", parent_id)):
        raise ValueError("invalid parent region")
    record = {
        "region_id": region_id,
        "name": name,
        "parent_id": parent_id,
        "description": str(payload.get("description") or "").strip()[:300],
        "created_at": str(existing.get("created_at") or now_iso()),
        "updated_at": now_iso(),
    }
    _store(workspace_id).save("regions", region_id, record)
    return record


@_connection_transaction
def delete_region(workspace_id: str, region_id: str) -> bool:
    if any(str(item.get("region_id") or "") == region_id for item in list_devices(workspace_id)):
        raise ValueError("region_has_devices")
    if any(str(item.get("parent_id") or "") == region_id for item in list_regions(workspace_id)):
        raise ValueError("region_has_children")
    return _store(workspace_id).delete("regions", region_id)


def list_devices(workspace_id: str) -> list[dict[str, Any]]:
    return _store(workspace_id).list("devices", limit=1000)


def get_device(workspace_id: str, device_id: str) -> dict[str, Any] | None:
    return _store(workspace_id).get("devices", device_id)


def _device_identity(name: str, host: str) -> tuple[str, str]:
    """A management address may expose multiple independently named devices."""
    return (name.strip().casefold(), host.strip().casefold())


@_connection_transaction
def save_device(workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    host = str(payload.get("host") or "").strip()
    if not name or not host or not _valid_host(host):
        raise ValueError("valid device name and host are required")
    device_id = str(payload.get("device_id") or _id("device"))
    existing = get_device(workspace_id, device_id) or {}
    region_id = str(payload.get("region_id") or "").strip()
    if region_id and not _store(workspace_id).get("regions", region_id):
        raise ValueError("region_not_found")
    records = list_devices(workspace_id)
    if not existing and len(records) >= 1000:
        raise ValueError("extension_device_quota_exceeded")
    identity = _device_identity(name, host)
    if any(
        item.get("device_id") != device_id
        and _device_identity(str(item.get("name") or ""), str(item.get("host") or "")) == identity
        for item in records
    ):
        raise ValueError("device name and host already exist")
    record = {
        "device_id": device_id,
        "name": name,
        "host": host,
        "vendor": str(payload.get("vendor") or "generic").strip().lower(),
        "device_type": str(payload.get("device_type") or "switch").strip().lower(),
        "region_id": region_id,
        "tags": sorted({str(item).strip() for item in (payload.get("tags") or []) if str(item).strip()}),
        "created_at": str(existing.get("created_at") or now_iso()),
        "updated_at": now_iso(),
    }
    _store(workspace_id).save("devices", device_id, record)
    identity_changed = bool(existing) and any(
        str(existing.get(key) or "") != str(record.get(key) or "")
        for key in ("host", "vendor")
    )
    if identity_changed:
        for visible in list_connections(workspace_id, device_id=device_id):
            connection_id = str(visible.get("connection_id") or "")
            connection = get_connection(workspace_id, connection_id, include_secret=True)
            if not connection:
                continue
            connection.update({
                "revision": uuid.uuid4().hex,
                "host_key_fingerprint": "",
                "status": "untested",
                "last_error": "device_identity_changed_retest_required",
                "updated_at": now_iso(),
            })
            _store(workspace_id).save("connections", connection_id, connection)
    return record


@_connection_transaction
def delete_device(workspace_id: str, device_id: str) -> bool:
    if not get_device(workspace_id, device_id):
        return False
    deleted_connection_ids = {
        str(connection.get("connection_id") or "")
        for connection in list_connections(workspace_id, device_id=device_id)
    }
    for connection_id in deleted_connection_ids:
        _delete_connection_record(workspace_id, connection_id)
    for skill in list_skills(workspace_id):
        if device_id in set(skill.get("device_ids") or []):
            skill["device_ids"] = [item for item in skill.get("device_ids") or [] if item != device_id]
            skill["connection_ids"] = [
                item for item in skill.get("connection_ids") or [] if item not in deleted_connection_ids
            ]
            _save_or_delete_depleted_skill(workspace_id, skill)
    return _store(workspace_id).delete("devices", device_id)


def _raw_connections(workspace_id: str) -> list[dict[str, Any]]:
    return _store(workspace_id).list("connections", limit=2000)


def _connection_identity(record: dict[str, Any]) -> tuple[str, str, int]:
    protocol = str(record.get("protocol") or "ssh").strip().lower()
    default_port = 23 if protocol == "telnet" else 22
    return (
        str(record.get("device_id") or "").strip(),
        protocol,
        int(record.get("port") or default_port),
    )


def _referenced_connection_ids(workspace_id: str) -> set[str]:
    return {
        str(connection_id)
        for skill in list_skills(workspace_id)
        for connection_id in (skill.get("connection_ids") or [])
        if str(connection_id)
    }


def _canonical_connection(records: list[dict[str, Any]], referenced: set[str]) -> dict[str, Any]:
    """Keep the most useful stable record when legacy duplicates exist."""
    return max(
        records,
        key=lambda item: (
            str(item.get("connection_id") or "") in referenced,
            bool(_public_connection(item).get("verified")),
            str(item.get("updated_at") or item.get("created_at") or ""),
            str(item.get("connection_id") or ""),
        ),
    )


def _replace_connection_references(workspace_id: str, old_ids: set[str], canonical_id: str) -> None:
    if not old_ids:
        return
    for skill in list_skills(workspace_id):
        current = [str(item) for item in (skill.get("connection_ids") or [])]
        if not old_ids.intersection(current):
            continue
        skill["connection_ids"] = list(dict.fromkeys(
            canonical_id if item in old_ids else item for item in current
        ))
        skill["updated_at"] = now_iso()
        _store(workspace_id).save("skills", str(skill.get("skill_id") or ""), skill)


def _reconcile_duplicate_connections_unlocked(workspace_id: str) -> int:
    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for record in _raw_connections(workspace_id):
        groups.setdefault(_connection_identity(record), []).append(record)
    referenced = _referenced_connection_ids(workspace_id)
    removed = 0
    for records in groups.values():
        if len(records) < 2:
            continue
        canonical = _canonical_connection(records, referenced)
        canonical_id = str(canonical.get("connection_id") or "")
        duplicate_ids = {
            str(item.get("connection_id") or "")
            for item in records
            if str(item.get("connection_id") or "") != canonical_id
        }
        _replace_connection_references(workspace_id, duplicate_ids, canonical_id)
        for duplicate_id in duplicate_ids:
            removed += int(_delete_connection_record(workspace_id, duplicate_id))
    return removed


def reconcile_duplicate_connections(workspace_id: str) -> int:
    """Enforce one logical connection for each device/protocol/port endpoint."""
    with _connection_lock(workspace_id):
        return _reconcile_duplicate_connections_unlocked(workspace_id)


def list_connections(workspace_id: str, *, device_id: str = "") -> list[dict[str, Any]]:
    records = _raw_connections(workspace_id)
    if device_id:
        records = [item for item in records if str(item.get("device_id") or "") == device_id]
    return [_public_connection(item) for item in records]


def get_connection(workspace_id: str, connection_id: str, *, include_secret: bool = False) -> dict[str, Any] | None:
    record = _store(workspace_id).get("connections", connection_id)
    if not record:
        return None
    return record if include_secret else _public_connection(record)


def _connection_target(workspace_id: str, connection: dict[str, Any]) -> DeviceTarget:
    device = get_device(workspace_id, str(connection.get("device_id") or ""))
    if not device:
        raise ValueError("connection_device_not_found")
    credential = DeviceCredential(
        auth_method=str(connection.get("auth_method") or "none"),
        username=str(connection.get("username") or ""),
        password=ExtensionSecretStore.get(str(connection.get("password_ref") or "")),
        private_key=ExtensionSecretStore.get(str(connection.get("private_key_ref") or "")),
        passphrase=ExtensionSecretStore.get(str(connection.get("passphrase_ref") or "")),
    )
    return DeviceTarget(
        host=str(device.get("host") or ""),
        port=int(connection.get("port") or (23 if connection.get("protocol") == "telnet" else 22)),
        protocol=str(connection.get("protocol") or "ssh"),
        vendor=str(device.get("vendor") or "generic"),
        name=str(device.get("name") or ""),
        source_address=resolve_source_address(
            str(device.get("host") or ""),
            str(connection.get("source_address") or ""),
        ),
        expected_fingerprint=str(connection.get("host_key_fingerprint") or ""),
        credential=credential,
    )


def test_connection(
    workspace_id: str,
    connection_id: str,
    *,
    accept_host_key: bool = False,
    read: bool = False,
    commands: list[str] | None = None,
    facts: list[str] | None = None,
    timeout: int = 15,
    session_scope: str = "",
    configuration_skill_id: str = "",
) -> dict[str, Any]:
    execution_started = time.monotonic()
    with _connection_lock(workspace_id):
        record = get_connection(workspace_id, connection_id, include_secret=True)
        if not record:
            return {"ok": False, "error": "connection_not_found"}
        probe_id = uuid.uuid4().hex
        record["probe_id"] = probe_id
        _store(workspace_id).save("connections", connection_id, record)
    target: DeviceTarget | None = None
    try:
        target = _connection_target(workspace_id, record)
        if commands is not None and facts:
            raise ValueError("commands_and_facts_are_mutually_exclusive")
        normalized_facts = _normalize_semantic_facts(facts) if facts else []
        selected = commands if (read or configuration_skill_id) and not normalized_facts else []
        if configuration_skill_id:
            if read or normalized_facts:
                raise ValueError("configuration_cannot_use_read_or_templates")
            selected = normalize_configuration_commands(selected, target.vendor)
        elif read and not normalized_facts:
            selected = normalize_read_only_commands(selected, target.vendor)
        root = _store(workspace_id).root().resolve()
        endpoint = hashlib.sha256(f"{target.host}:{target.port}".encode()).hexdigest()
        session_options = {}
        if session_scope:
            # root includes principal identity. Revision and target prevent
            # credential/config edits or changed routing from reusing a socket.
            identity = [str(root), session_scope, connection_id, record.get("revision"),
                        target.host, target.port, target.source_address, target.expected_fingerprint]
            session_options["session_key"] = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        with FileLock(root / ".cli-locks" / (endpoint + ".lock"), timeout=timeout):
            latest = get_connection(workspace_id, connection_id, include_secret=True)
            if not latest or latest.get("revision") != record.get("revision"):
                raise ValueError("connection_changed_before_execution")
            if configuration_skill_id:
                # Re-read authority after queueing and before opening a socket.
                skill = get_skill(workspace_id, configuration_skill_id)
                if not configuration_allowed(skill, connection_id):
                    raise ValueError("device_execution_not_allowed_by_skill")
                session_options = {"configure": True}
            remaining = timeout - (time.monotonic() - execution_started)
            if remaining <= 0:
                raise TimeoutError("device_execution_budget_exhausted")
            result = probe_target(
                target, commands=selected, facts=normalized_facts,
                accept_host_key=accept_host_key, read=read, timeout=remaining, **session_options,
            )
    except (ValueError, RuntimeError, OSError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)[:300] or "connection_setup_failed"}
    fingerprint = str(result.get("fingerprint") or "")
    if fingerprint and accept_host_key and result.get("ok"):
        record["host_key_fingerprint"] = fingerprint
    profile = result.get("device_profile") if isinstance(result.get("device_profile"), dict) else {}
    connection_observed = bool(result.get("ok") or result.get("command_results"))
    if profile and connection_observed:
        record.update({
            "driver_id": str(profile.get("driver_id") or ""),
            "detected_vendor": str(profile.get("vendor") or ""),
            "os_family": str(profile.get("os_family") or ""),
            "semantic_facts": list(profile.get("semantic_facts") or []),
            "profile_detected_from": str(profile.get("detected_from") or ""),
            "profile_updated_at": now_iso(),
        })
    record.update({
        "status": "connected" if connection_observed else ("trust_required" if result.get("requires_host_key_acceptance") else "failed"),
        "last_tested_at": now_iso(),
        "last_error": "" if result.get("ok") else str(result.get("error") or "connection_test_failed")[:300],
        "latency_ms": int(result.get("duration_ms") or 0),
        "effective_source_address": target.source_address if target else str(record.get("effective_source_address") or ""),
        "updated_at": now_iso(),
    })
    with _connection_lock(workspace_id):
        current = get_connection(workspace_id, connection_id, include_secret=True)
        if not current:
            if configuration_skill_id:
                return {**result, "connection": None, "observation_superseded": True}
            return {"ok": False, "status": "failed", "error": "connection_deleted_during_test", "connection": None}
        if current.get("revision") != record.get("revision"):
            if configuration_skill_id:
                return {**result, "connection": _public_connection(current), "observation_superseded": True}
            return {"ok": False, "status": "failed", "error": "connection_changed_during_test", "connection": _public_connection(current)}
        if current.get("probe_id") != probe_id:
            # A newer probe owns the displayed status, but evidence from this
            # unchanged endpoint remains valid for its requesting tool call.
            return {**result, "connection": _public_connection(current), "observation_superseded": True}
        observation_keys = (
            "status", "last_tested_at", "last_error", "latency_ms", "effective_source_address",
            "updated_at", "host_key_fingerprint", "driver_id", "detected_vendor", "os_family",
            "semantic_facts", "profile_detected_from", "profile_updated_at",
        )
        current.update({key: record[key] for key in observation_keys if key in record})
        _store(workspace_id).save("connections", connection_id, current)
        result["connection"] = _public_connection(current)
    return result


def _normalize_semantic_facts(facts: list[str] | tuple[str, ...] | None) -> list[str]:
    if not isinstance(facts, (list, tuple)) or any(not isinstance(item, str) for item in facts):
        raise ValueError("facts must be an array of semantic fact names")
    normalized = list(dict.fromkeys(item.strip() for item in facts if item.strip()))
    if not normalized or len(normalized) > 10:
        raise ValueError("facts must contain 1 to 10 semantic fact names")
    unsupported = [item for item in normalized if item not in SEMANTIC_FACTS]
    if unsupported:
        raise ValueError(f"unsupported_semantic_fact:{unsupported[0]}")
    return normalized


def _save_connection_unlocked(workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    device_id = str(payload.get("device_id") or "").strip()
    if not get_device(workspace_id, device_id):
        raise ValueError("device_not_found")
    protocol = str(payload.get("protocol") or "ssh").strip().lower()
    if protocol not in {"ssh", "telnet"}:
        raise ValueError("protocol must be ssh or telnet")
    try:
        port = int(payload.get("port") or (23 if protocol == "telnet" else 22))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid port") from exc
    if not 1 <= port <= 65535:
        raise ValueError("invalid port")
    requested_id = str(payload.get("connection_id") or "").strip()
    if requested_id:
        connection_id = requested_id
    else:
        matches = [
            item for item in _raw_connections(workspace_id)
            if _connection_identity(item) == (device_id, protocol, port)
        ]
        connection_id = (
            str(_canonical_connection(matches, _referenced_connection_ids(workspace_id)).get("connection_id") or "")
            if matches else _id("connection")
        )
    existing = get_connection(workspace_id, connection_id, include_secret=True) or {}
    if requested_id and not existing:
        raise ValueError("connection_not_found")
    if existing and str(existing.get("device_id") or "") != device_id:
        raise ValueError("connection_device_is_immutable")
    if any(
        item.get("connection_id") != connection_id
        and _connection_identity(item) == (device_id, protocol, port)
        for item in _raw_connections(workspace_id)
    ):
        raise ValueError("connection_endpoint_already_exists")
    auth_method = str(payload.get("auth_method") or existing.get("auth_method") or ("none" if protocol == "telnet" else "password")).lower()
    if auth_method not in ({"none", "password"} if protocol == "telnet" else {"password", "private_key"}):
        raise ValueError("invalid auth method for protocol")
    username = str(payload.get("username") if "username" in payload else existing.get("username") or "").strip()
    source_address = str(payload.get("source_address") if "source_address" in payload else existing.get("source_address") or "").strip()
    if source_address:
        try:
            ipaddress.ip_address(source_address)
        except ValueError as exc:
            raise ValueError("source_address must be a local IP address") from exc
    if protocol == "ssh" and not username:
        raise ValueError("username is required for ssh")
    secrets = ExtensionSecretStore(EXTENSION_ID, workspace_id)
    password_ref = str(existing.get("password_ref") or "")
    private_key_ref = str(existing.get("private_key_ref") or "")
    passphrase_ref = str(existing.get("passphrase_ref") or "")
    # Reject incomplete auth changes before touching any stored secret.
    if auth_method == "password" and not (password_ref or payload.get("password")):
        raise ValueError("password is required for password authentication")
    if auth_method == "private_key" and not (private_key_ref or payload.get("private_key")):
        raise ValueError("private key is required for private-key authentication")
    if payload.get("password"):
        password_ref = secrets.set(f"connection_{connection_id}_password", str(payload["password"]))
    if payload.get("private_key"):
        private_key_ref = secrets.set(f"connection_{connection_id}_key", str(payload["private_key"]))
    if payload.get("passphrase"):
        passphrase_ref = secrets.set(f"connection_{connection_id}_passphrase", str(payload["passphrase"]))
    if auth_method == "none":
        for reference in (password_ref, private_key_ref, passphrase_ref):
            if reference:
                ExtensionSecretStore.delete(reference)
        password_ref = private_key_ref = passphrase_ref = ""
    elif auth_method == "password":
        for reference in (private_key_ref, passphrase_ref):
            if reference:
                ExtensionSecretStore.delete(reference)
        private_key_ref = passphrase_ref = ""
    else:
        if password_ref:
            ExtensionSecretStore.delete(password_ref)
        password_ref = ""
    record = {
        "connection_id": connection_id,
        "revision": uuid.uuid4().hex,
        "device_id": device_id,
        "name": str(payload.get("name") or existing.get("name") or protocol.upper()).strip()[:80],
        "protocol": protocol,
        "port": port,
        "username": username,
        "source_address": source_address,
        "auth_method": auth_method,
        "password_ref": password_ref,
        "private_key_ref": private_key_ref,
        "passphrase_ref": passphrase_ref,
        "host_key_fingerprint": str(payload.get("host_key_fingerprint") or (
            existing.get("host_key_fingerprint") if existing and _connection_identity(existing) == (device_id, protocol, port) else ""
        ) or ""),
        "status": "untested",
        "last_tested_at": str(existing.get("last_tested_at") or ""),
        "last_error": "",
        "created_at": str(existing.get("created_at") or now_iso()),
        "updated_at": now_iso(),
    }
    _store(workspace_id).save("connections", connection_id, record)
    return _public_connection(record)


def save_connection(workspace_id: str, payload: dict[str, Any], *, auto_test: bool = True) -> dict[str, Any]:
    """Create or update a connection by its stable endpoint identity.

    A device may have distinct SSH/Telnet endpoints or ports, but repeated
    submissions for the same ``device + protocol + port`` update the existing
    logical connection instead of creating ambiguous duplicate credentials.
    """
    with _connection_lock(workspace_id):
        record = _save_connection_unlocked(workspace_id, payload)
    if auto_test:
        result = test_connection(workspace_id, str(record.get("connection_id") or ""))
        if result.get("error") in {"connection_not_found", "connection_deleted_during_test", "connection_changed_during_test"}:
            raise ValueError(str(result["error"]))
        return result["connection"]
    return record


def _delete_connection_record(workspace_id: str, connection_id: str) -> bool:
    record = get_connection(workspace_id, connection_id, include_secret=True)
    if not record:
        return False
    secret_keys = ("password_ref", "private_key_ref", "passphrase_ref")
    retained_refs = {
        str(item[key]) for item in _raw_connections(workspace_id)
        if item.get("connection_id") != connection_id
        for key in secret_keys if item.get(key)
    }
    for key in secret_keys:
        if record.get(key) and str(record[key]) not in retained_refs:
            ExtensionSecretStore.delete(str(record[key]))
    return _store(workspace_id).delete("connections", connection_id)


def _save_or_delete_depleted_skill(workspace_id: str, skill: dict[str, Any]) -> None:
    if not skill.get("device_ids") or not skill.get("connection_ids"):
        _store(workspace_id).delete("skills", str(skill.get("skill_id") or ""))
        return
    save_skill(workspace_id, skill)


@_connection_transaction
def delete_connection(workspace_id: str, connection_id: str) -> bool:
    if not _delete_connection_record(workspace_id, connection_id):
        return False
    for skill in list_skills(workspace_id):
        if connection_id in set(skill.get("connection_ids") or []):
            skill["connection_ids"] = [item for item in skill.get("connection_ids") or [] if item != connection_id]
            _save_or_delete_depleted_skill(workspace_id, skill)
    return True


def _with_skill_base_capability(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy Skill records to the default device-execution contract."""
    return {
        **{key: value for key, value in record.items() if key != "capabilities"},
        "allowed_tool_ids": list(dict.fromkeys([
        *(record.get("allowed_tool_ids") or []), SKILL_BASE_TOOL_ID,
        "network.operations.context_read",
        ])),
    }


def list_skills(workspace_id: str, *, enabled_only: bool = False) -> list[dict[str, Any]]:
    records = _store(workspace_id).list("skills", limit=500)
    return [_with_skill_base_capability(item) for item in records if not enabled_only or bool(item.get("enabled", True))]


def get_skill(workspace_id: str, skill_id: str) -> dict[str, Any] | None:
    record = _store(workspace_id).get("skills", skill_id)
    return _with_skill_base_capability(record) if record else None


def configuration_allowed(skill: dict[str, Any] | None, connection_id: str) -> bool:
    return bool(
        skill and skill.get("enabled", True)
        and "network.operations.device.manage" in (skill.get("allowed_tool_ids") or [])
        and connection_id in (skill.get("connection_ids") or [])
    )


@_connection_transaction
def save_skill(workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ValueError("skill name is required and must be at most 80 characters")
    skill_id = str(payload.get("skill_id") or _id("skill"))
    existing = get_skill(workspace_id, skill_id) or {}
    device_ids = list(dict.fromkeys(str(item).strip() for item in (payload.get("device_ids") or []) if str(item).strip()))
    connection_ids = list(dict.fromkeys(str(item).strip() for item in (payload.get("connection_ids") or []) if str(item).strip()))
    if not device_ids or not connection_ids:
        raise ValueError("skill requires at least one device and one configured connection")
    if any(not get_device(workspace_id, item) for item in device_ids):
        raise ValueError("skill contains unknown device")
    connections = [get_connection(workspace_id, item) for item in connection_ids]
    if any(not item for item in connections):
        raise ValueError("skill contains unknown connection")
    if any(str(item.get("device_id") or "") not in set(device_ids) for item in connections if item):
        raise ValueError("skill connection is not owned by a selected device")
    raw_tool_ids = payload.get("allowed_tool_ids", sorted(SKILL_TOOL_IDS))
    if not isinstance(raw_tool_ids, list):
        raise ValueError("skill tools must be an array")
    allowed_tool_ids = list(dict.fromkeys(
        str(item).strip()
        for item in raw_tool_ids
        if str(item).strip()
    ))
    if any(item not in SKILL_TOOL_IDS for item in allowed_tool_ids):
        raise ValueError("skill contains unsupported tool")
    allowed_tool_ids = _with_skill_base_capability({"allowed_tool_ids": allowed_tool_ids})["allowed_tool_ids"]
    default_script_id = str(payload.get("default_script_id") or "").strip()
    if default_script_id:
        _resolve_script(workspace_id, default_script_id)
    record = {
        "skill_id": skill_id,
        "name": name,
        "description": str(payload.get("description") or "").strip()[:500],
        "enabled": bool(payload.get("enabled", existing.get("enabled", True))),
        "device_ids": device_ids,
        "connection_ids": connection_ids,
        "allowed_tool_ids": allowed_tool_ids,
        "default_script_id": default_script_id,
        "instructions": str(payload.get("instructions") or "").strip()[:2000],
        "created_at": str(existing.get("created_at") or now_iso()),
        "updated_at": now_iso(),
    }
    _store(workspace_id).save("skills", skill_id, record)
    return record


@_connection_transaction
def delete_skill(workspace_id: str, skill_id: str) -> bool:
    return _store(workspace_id).delete("skills", skill_id)


def resolve_workbench_selection(workspace_id: str, selection: dict[str, Any]) -> dict[str, Any]:
    """Resolve authorization and saved metadata only; never contact selected devices."""
    if not isinstance(selection, dict):
        raise ValueError("invalid_workbench_skill_selection")
    with _connection_lock(workspace_id):
        skill_id = str(selection.get("skill_id") or "").strip()
        skill = get_skill(workspace_id, skill_id)
        if not skill or not skill.get("enabled", True):
            raise ValueError("workbench_skill_not_available")
        allowed_devices = set(skill.get("device_ids") or [])
        raw_resources = selection.get("resource_ids") if "resource_ids" in selection else selection.get("device_ids")
        if raw_resources is not None and not isinstance(raw_resources, list):
            raise ValueError("workbench_skill_resources_must_be_an_array")
        if isinstance(raw_resources, list) and len(raw_resources) > 100:
            raise ValueError("workbench_skill_resource_limit_exceeded")
        selected = list(dict.fromkeys(
            str(item).strip()
            for item in (raw_resources or [])
            if str(item).strip()
        ))
        if raw_resources is None:
            selected = list(skill.get("device_ids") or [])
        if not selected or not set(selected).issubset(allowed_devices):
            raise ValueError("workbench_skill_device_forbidden")
        devices = [get_device(workspace_id, item) for item in selected]
        connection_allowlist = set(skill.get("connection_ids") or [])
        visible_connections = {
            str(item.get("connection_id") or ""): item
            for item in list_connections(workspace_id)
            if item.get("connection_id") in connection_allowlist and item.get("device_id") in selected
        }
        connections = [
            visible_connections[connection_id]
            for connection_id in skill.get("connection_ids") or []
            if connection_id in visible_connections
        ]
        if not connections:
            raise ValueError("workbench_skill_has_no_configured_connection")
    return {
        "skill_id": skill_id,
        "skill_name": str(skill.get("name") or ""),
        "instructions": str(skill.get("instructions") or ""),
        "allowed_tool_ids": list(skill.get("allowed_tool_ids") or []),
        "device_ids": selected,
        "connection_ids": [str(item.get("connection_id") or "") for item in connections],
        "connection_policy": "on_demand",
        "devices": [{"device_id": item.get("device_id"), "name": item.get("name"), "host": item.get("host"), "vendor": item.get("vendor")} for item in devices if item],
        "connections": [{
            "connection_id": item.get("connection_id"),
            "device_id": item.get("device_id"),
            "protocol": item.get("protocol"),
            "port": item.get("port"),
            "last_observed_status": str(item.get("status") or "untested"),
            "last_tested_at": str(item.get("last_tested_at") or ""),
            "current_reachability": "not_checked",
            "driver_id": item.get("driver_id"),
            "detected_vendor": item.get("detected_vendor"),
            "os_family": item.get("os_family"),
            "semantic_facts": list(item.get("semantic_facts") or []),
            "profile_detected_from": item.get("profile_detected_from"),
        } for item in connections],
        "semantic_catalog": semantic_catalog(),
        "operational_context": operational_context(
            workspace_id,
            connection_ids=[str(item.get("connection_id") or "") for item in connections],
        ),
        "network_runtime_version": "network.cli.v3",
        "source": "server_validated_extension_context",
    }


def workbench_skill_catalog(workspace_id: str) -> list[dict[str, Any]]:
    """Project network Skills into the domain-neutral workbench catalog."""
    devices = {str(item.get("device_id") or ""): item for item in list_devices(workspace_id)}
    connections = list_connections(workspace_id)
    catalog: list[dict[str, Any]] = []
    for skill in list_skills(workspace_id, enabled_only=True):
        allowed_connections = set(skill.get("connection_ids") or [])
        configured_devices = {
            str(item.get("device_id") or "")
            for item in connections
            if item.get("connection_id") in allowed_connections
        }
        resources = [
            {
                "resource_id": device_id,
                "name": str(devices[device_id].get("name") or device_id),
                "description": str(devices[device_id].get("host") or ""),
                "kind": "network_device",
            }
            for device_id in skill.get("device_ids") or []
            if device_id in devices and device_id in configured_devices
        ]
        if resources:
            catalog.append({
                "skill_id": str(skill.get("skill_id") or ""),
                "name": str(skill.get("name") or ""),
                "description": str(skill.get("description") or ""),
                "resources": resources,
                "default_resource_ids": [item["resource_id"] for item in resources],
                "selection_mode": "multiple",
            })
    return catalog


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


def _valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host))


def commands_for(asset: dict[str, Any], commands: list[str] | None = None, script: dict[str, Any] | None = None) -> list[str]:
    vendor = str(asset.get("vendor") or "generic").lower()
    if script:
        vendors = set(script.get("vendors") or [])
        if vendor not in vendors:
            raise ValueError(f"script_not_supported_for_vendor:{vendor}")
        selected = script.get("commands") or []
    else:
        selected = commands
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


def collect_connection(asset: dict[str, Any], commands: list[str] | None, *, timeout: int = 15,
                       facts: list[str] | None = None, session_scope: str = "") -> dict[str, Any]:
    """Return the complete execution envelope, not just a lossy text mapping."""
    if asset.get("connection_id"):
        result = test_connection(
            str(asset.get("workspace_id") or ""),
            str(asset.get("connection_id") or ""),
            commands=commands,
            read=True,
            timeout=timeout,
            facts=facts,
            session_scope=session_scope,
        )
        return result
    return probe_target(_target_for(asset), commands=commands, facts=facts, read=True, timeout=timeout)


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


def _inspection_connections(workspace_id: str, connection_ids: list[str] | None) -> list[dict[str, Any]]:
    if not isinstance(connection_ids, list) or not connection_ids:
        raise ValueError("connection_ids must be a non-empty array")
    normalized = [str(item).strip() for item in connection_ids]
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise ValueError("connection_ids must contain unique non-empty strings")
    targets: list[dict[str, Any]] = []
    for connection_id in normalized:
        connection = get_connection(workspace_id, connection_id)
        if not connection:
            raise ValueError(f"inspection_connection_not_found:{connection_id}")
        device = get_device(workspace_id, str(connection.get("device_id") or ""))
        if not device:
            raise ValueError(f"inspection_connection_device_not_found:{connection_id}")
        targets.append({
            **device,
            "connection_id": connection_id,
            "workspace_id": workspace_id,
            "protocol": connection.get("protocol"),
            "port": connection.get("port"),
        })
    return targets


def _inspection_target_id(target: dict[str, Any]) -> str:
    identifier = str(target.get("connection_id") or target.get("asset_id") or "").strip()
    if not identifier:
        raise ValueError("inspection_target_id_missing")
    return identifier


def _safe_inspection_target(target: dict[str, Any]) -> dict[str, Any]:
    if target.get("connection_id"):
        return {
            key: target.get(key)
            for key in (
                "connection_id", "device_id", "name", "host", "vendor",
                "device_type", "region_id", "protocol", "port",
            )
            if key in target
        }
    return _safe_asset(target)


def _command_plan(
    commands: list[str] | None,
    script: dict[str, Any] | None,
    facts: list[str] | None = None,
) -> dict[str, Any]:
    if script:
        return {"mode": "script", "script": _script_safe(script)}
    if facts:
        return {"mode": "semantic_facts", "facts": _normalize_semantic_facts(facts)}
    if commands is not None:
        return {"mode": "inline_commands", "commands": list(commands)}
    raise ValueError("explicit_commands_facts_or_script_required")


def _restore_command_plan(task: dict[str, Any]) -> tuple[list[str] | None, dict[str, Any] | None, list[str] | None]:
    plan = task.get("command_plan")
    if not isinstance(plan, dict):
        raise ValueError("inspection_command_plan_missing")
    mode = str(plan.get("mode") or "")
    if mode == "script":
        script = plan.get("script")
        if not isinstance(script, dict) or not script.get("script_id"):
            raise ValueError("inspection_script_snapshot_invalid")
        return None, dict(script), None
    if mode == "semantic_facts":
        facts = plan.get("facts")
        if not isinstance(facts, list):
            raise ValueError("inspection_semantic_facts_invalid")
        return None, None, _normalize_semantic_facts(facts)
    if mode == "inline_commands":
        commands = plan.get("commands")
        if not isinstance(commands, list):
            raise ValueError("inspection_inline_commands_invalid")
        return list(commands), None, None
    raise ValueError("inspection_command_plan_invalid")


def _build_inspection_task(
    targets: list[dict[str, Any]],
    commands: list[str] | None,
    script: dict[str, Any] | None,
    *,
    facts: list[str] | None = None,
    job_id: str = "",
) -> dict[str, Any]:
    if sum((commands is not None, script is not None, bool(facts))) != 1:
        raise ValueError("exactly_one_of_commands_facts_or_script_required")
    for target in targets:
        if facts:
            # Validate vocabulary now, but defer vendor command selection to
            # the live session after runtime driver detection.
            _normalize_semantic_facts(facts)
        else:
            commands_for(target, commands, script)
    task_id = _id("inspection")
    is_connection_task = all(bool(item.get("connection_id")) for item in targets)
    target_ids = [_inspection_target_id(item) for item in targets]
    task = {
        "task_id": task_id, "job_id": job_id, "status": "queued",
        "target_kind": "connection" if is_connection_task else "asset",
        # Results remain attributable after an intentional hard delete. These
        # snapshots contain identity and routing metadata, never credentials.
        "target_snapshots": {
            identifier: _safe_inspection_target(item)
            for identifier, item in zip(target_ids, targets)
        },
        "total": len(targets), "completed": 0, "succeeded": 0, "partial": 0, "failed": 0,
        "results": {}, "artifact_id": "",
        "command_plan": _command_plan(commands, script, facts),
        "script": _script_safe(script) if script else {
            "script_id": "semantic-facts" if facts else "inline-commands",
            "name": "语义事实采集" if facts else "临时只读命令",
            "commands": list(commands or []),
            "facts": list(facts or []),
        },
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    if is_connection_task:
        task["connection_ids"] = target_ids
        task["device_ids"] = [str(item.get("device_id") or "") for item in targets]
    else:
        task["asset_ids"] = target_ids
        # Internal legacy tasks keep their historical field without leaking it
        # into the registered-connection path.
        task["asset_snapshots"] = dict(task["target_snapshots"])
    return task


def _new_inspection_task(workspace_id: str, asset_ids: list[str] | None, commands: list[str] | None, script_id: str, *, job_id: str = "") -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    assets = _inspection_assets(workspace_id, asset_ids)
    script = _resolve_script(workspace_id, script_id)
    task = _build_inspection_task(assets, commands, script, job_id=job_id)
    return task, assets, script


def _new_connection_inspection_task(
    workspace_id: str,
    connection_ids: list[str] | None,
    commands: list[str] | None,
    script_id: str,
    *,
    facts: list[str] | None = None,
    job_id: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    if commands is not None and facts:
        raise ValueError("commands_and_facts_are_mutually_exclusive")
    targets = _inspection_connections(workspace_id, connection_ids)
    script = _resolve_script(workspace_id, script_id)
    if script and facts:
        raise ValueError("script_and_facts_are_mutually_exclusive")
    task = _build_inspection_task(targets, commands, script, facts=facts, job_id=job_id)
    return task, targets, script


class _DurableCancellation:
    """Cancellation probe backed by the canonical durable job record."""
    def __init__(self, workspace_id: str, job_id: str):
        self.workspace_id = workspace_id
        self.job_id = job_id
    def is_set(self) -> bool:
        from jobs.store import get_job
        job = get_job(self.workspace_id, self.job_id)
        return bool(job and job.cancel_requested)


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


def enqueue_connection_inspection(
    workspace_id: str,
    connection_ids: list[str] | None,
    commands: list[str] | None = None,
    script_id: str = "",
    *,
    facts: list[str] | None = None,
    created_by: str = "user",
) -> dict[str, Any]:
    """Create a durable inspection; each target reconnects and fails independently."""
    task, _targets, _script = _new_connection_inspection_task(
        workspace_id, connection_ids, commands, script_id, facts=facts
    )
    return _enqueue_prepared_inspection(workspace_id, task, created_by=created_by)


def execute_queued_inspection(workspace_id: str, task_id: str, job_id: str) -> dict[str, Any]:
    task = get_inspection(workspace_id, task_id)
    if not task:
        raise ValueError("inspection_not_found")
    assets = (
        _inspection_connections(workspace_id, list(task.get("connection_ids") or []))
        if task.get("connection_ids") else
        _inspection_assets(workspace_id, list(task.get("asset_ids") or []))
    )
    commands, script, facts = _restore_command_plan(task)
    cancel = _DurableCancellation(workspace_id, job_id)
    _execute_inspection(workspace_id, task_id, assets, commands, collect_connection, cancel, script, facts)
    return get_inspection(workspace_id, task_id) or task


def _execute_inspection(
    workspace_id: str,
    task_id: str,
    targets: list[dict[str, Any]],
    commands: list[str] | None,
    collector: Callable,
    cancel: Any,
    script: dict[str, Any] | None = None,
    facts: list[str] | None = None,
) -> None:
    store = _store(workspace_id)
    task = store.get("inspections", task_id) or {}
    if cancel.is_set():
        task.update({"status": "cancelled", "finished_at": now_iso(), "updated_at": now_iso()})
        store.save("inspections", task_id, task)
        return
    task.update({"status": "running", "started_at": now_iso(), "updated_at": now_iso()})
    store.save("inspections", task_id, task)
    def run_one(target: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        from core.tools.context import bind_runtime_cancel_check, reset_runtime_cancel_check
        target_id = _inspection_target_id(target)
        if cancel.is_set():
            return target_id, {"status": "cancelled", "name": target["name"]}
        started = time.monotonic()
        cancel_token = bind_runtime_cancel_check(cancel.is_set)
        try:
            selected = None if facts else commands_for(target, commands, script)
            live = collector(target, selected, facts=facts, session_scope=task_id)
            if not live.get("ok"):
                raise RuntimeError(str(live.get("error") or "device connection failed"))
            raw = {str(key): str(value) for key, value in (live.get("output") or {}).items()}
            selected = [str(item.get("command") or "") for item in (live.get("command_results") or []) if item.get("command")]
            target_status = "succeeded" if live.get("read_ok") else "partial"
            diagnostics = [
                {key: item.get(key) for key in (
                    "command", "fact", "complete", "pages", "encoding",
                    "error_code", "device_error", "truncated", "duration_ms",
                    "dispatch_status",
                )}
                for item in (live.get("command_results") or [])
            ]
            normalized = json.dumps(raw, ensure_ascii=False, sort_keys=True)
            return target_id, {
                "status": target_status, "name": target["name"], "host": target["host"],
                "commands": selected, "output_hash": hashlib.sha256(normalized.encode()).hexdigest(),
                "facts": live.get("facts"),
                "command_results": diagnostics,
                "_raw_output": raw, "duration_ms": int((time.monotonic() - started) * 1000),
            }
        except Exception as exc:
            return target_id, {
                "status": "failed", "name": target["name"], "host": target["host"],
                "error": str(exc)[:300], "duration_ms": int((time.monotonic() - started) * 1000),
            }
        finally:
            reset_runtime_cancel_check(cancel_token)
    workers = min(5, max(1, len(targets)))
    raw_outputs: dict[str, dict[str, str]] = {}
    with ContextThreadPoolExecutor(max_workers=workers, thread_name_prefix="network-inspection") as pool:
        futures = [pool.submit(run_one, target) for target in targets]
        for future in as_completed(futures):
            target_id, result = future.result()
            raw = result.pop("_raw_output", None)
            if isinstance(raw, dict): raw_outputs[target_id] = raw
            task["results"][target_id] = result
            task["completed"] += 1
            task["succeeded"] += int(result["status"] == "succeeded")
            task["partial"] = int(task.get("partial") or 0) + int(result["status"] == "partial")
            task["failed"] += int(result["status"] == "failed")
            task["updated_at"] = now_iso()
            store.save("inspections", task_id, task)
    task["status"] = (
        "cancelled" if cancel.is_set()
        else "succeeded" if task["failed"] == 0 and int(task.get("partial") or 0) == 0
        else "partial" if task["succeeded"] > 0 or int(task.get("partial") or 0) > 0
        else "failed"
    )
    task["finished_at"] = now_iso()
    task["updated_at"] = now_iso()
    try:
        task["artifact_id"] = _save_evidence_artifact(workspace_id, task, raw_outputs)
        task["findings"] = _derive_findings(workspace_id, task, raw_outputs)
        task["finding_count"] = len(task["findings"])
        observation = record_inspection_observation(workspace_id, task)
        task["observation_id"] = observation["observation_id"]
        task["candidate_reference_id"] = str(observation.get("candidate_reference_id") or "")
        store.save("inspections", task_id, task)
    except Exception:
        task.update({"status": "failed", "error": "inspection_evidence_persist_failed", "finished_at": now_iso(), "updated_at": now_iso()})
        store.save("inspections", task_id, task)
        raise


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


def _finding_id(target_id: str, category: str, rule_id: str) -> str:
    digest = hashlib.sha256(f"{target_id}|{category}|{rule_id}".encode()).hexdigest()[:20]
    return f"finding_{digest}"


def _finding_view(record: dict[str, Any]) -> dict[str, Any]:
    """Project a finding without leaking command output or encrypted secrets."""
    allowed = (
        "finding_id", "target_id", "connection_id", "device_id", "target_name", "target_host",
        "asset_id", "asset_name", "asset_host", "category", "rule_id",
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
    target_id = _inspection_target_id(asset)
    finding_id = _finding_id(target_id, category, rule_id)
    previous = store.get("findings", finding_id) or {}
    previous_status = str(previous.get("status") or "")
    status = "open" if previous_status in {"", "resolved"} else previous_status
    occurrences = int(previous.get("occurrences") or 0) + 1
    record = {
        **previous,
        "finding_id": finding_id,
        "target_id": target_id,
        "target_name": asset.get("name", ""),
        "target_host": asset.get("host", ""),
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
    if asset.get("connection_id"):
        record["connection_id"] = str(asset.get("connection_id") or "")
        record["device_id"] = str(asset.get("device_id") or "")
        for legacy_key in ("asset_id", "asset_name", "asset_host"):
            record.pop(legacy_key, None)
    else:
        record["asset_id"] = target_id
        record["asset_name"] = asset.get("name", "")
        record["asset_host"] = asset.get("host", "")
    store.save("findings", finding_id, record)
    return _finding_view(record)


def _derive_findings(workspace_id: str, task: dict[str, Any], raw_outputs: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """Turn a completed read-only inspection into stable, traceable findings."""
    is_connection_task = task.get("target_kind") == "connection" or bool(task.get("connection_ids"))
    current_targets = (
        {}
        if is_connection_task else
        {item["asset_id"]: item for item in list_assets(workspace_id)}
    )
    snapshots = task.get("target_snapshots") if isinstance(task.get("target_snapshots"), dict) else {}
    if not snapshots and isinstance(task.get("asset_snapshots"), dict):
        snapshots = task["asset_snapshots"]
    checks = list((task.get("script") or {}).get("checks") or [])
    baseline = next((item for item in list_baselines(workspace_id) if item.get("current") and item.get("confirmed")), None)
    findings: list[dict[str, Any]] = []
    for target_id, result in sorted((task.get("results") or {}).items()):
        asset = current_targets.get(target_id) or snapshots.get(target_id)
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
        raw = raw_outputs.get(target_id) or {}
        joined_output = "\n".join(f"{command}\n{output}" for command, output in sorted(raw.items()))
        for check in checks:
            if re.search(str(check["pattern"]), joined_output, re.IGNORECASE):
                findings.append(_upsert_finding(
                    workspace_id, asset=asset, category="inspection_rule", rule_id=str(check["check_id"]),
                    title=str(check["name"]), description=str(check["description"]), severity=str(check["severity"]),
                    task=task, evidence={**evidence, "check_id": check["check_id"], "check_version": int((task.get("script") or {}).get("version") or 0)},
                ))
        before = (baseline or {}).get("devices", {}).get(target_id) if baseline else None
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


def list_inspections(workspace_id: str) -> list[dict[str, Any]]:
    return _store(workspace_id).list("inspections", limit=200)


def get_inspection(workspace_id: str, task_id: str) -> dict[str, Any] | None:
    return _store(workspace_id).get("inspections", task_id)


def retry_inspection(workspace_id: str, task_id: str) -> dict[str, Any]:
    task = get_inspection(workspace_id, task_id)
    if not task or task.get("status") not in {"failed", "cancelled", "partial"}:
        raise ValueError("retryable inspection task is required")
    commands, script, facts = _restore_command_plan(task)
    is_connection_task = bool(task.get("connection_ids"))
    assets = (
        _inspection_connections(workspace_id, list(task.get("connection_ids") or []))
        if is_connection_task else
        _inspection_assets(workspace_id, list(task.get("asset_ids") or []))
    )
    next_task = _build_inspection_task(assets, commands, script, facts=facts)
    retried = _enqueue_prepared_inspection(
        workspace_id,
        next_task,
        created_by="retry",
    )
    retried["retry_of_task_id"] = task_id
    _store(workspace_id).save("inspections", retried["task_id"], retried)
    return retried


def reconcile_network_state() -> int:
    """Explicit startup maintenance: migrate endpoints and reconcile durable jobs.

    Reads never trigger migrations. Each principal/workspace is migrated under
    the same transaction lock used by connection and Skill mutations.
    """
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
                reconcile_duplicate_connections(workspace_id)
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
    # Historical tasks without a durable job have no live worker to cancel.
    task.update({"status": "cancelled", "finished_at": now_iso()})
    task["cancel_requested"] = True
    task["updated_at"] = now_iso()
    _store(workspace_id).save("inspections", task_id, task)
    return True


def list_baselines(workspace_id: str) -> list[dict[str, Any]]:
    """Read confirmed references while preserving existing stored baselines."""
    references = [
        {
            **item,
            "baseline_id": item["reference_id"],
            "confirmed": item["state"] == "confirmed",
            "devices": dict(item.get("snapshot") or {}),
        }
        for item in list_references(workspace_id)
        if item.get("state") == "confirmed"
    ]
    known = {str(item.get("baseline_id") or "") for item in references}
    legacy = [
        item for item in _store(workspace_id).list("baselines", limit=200)
        if str(item.get("baseline_id") or "") not in known
    ]
    return [*references, *legacy]


@_connection_transaction
def record_command_experience(
    workspace_id: str,
    connection_id: str,
    output: dict[str, Any],
) -> list[dict[str, Any]]:
    """Remember read syntax outcomes as hints, never as an executable plan."""
    profile = output.get("device_profile") if isinstance(output.get("device_profile"), dict) else {}
    driver_id = str(profile.get("driver_id") or "unknown")
    recorded: list[dict[str, Any]] = []
    for item in output.get("command_results") or []:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "").strip()
        if not command:
            continue
        status = "accepted" if item.get("complete") and not item.get("error_code") and not item.get("truncated") else "rejected"
        key = hashlib.sha256(f"{connection_id}|{driver_id}|{command}".encode()).hexdigest()[:24]
        previous = _store(workspace_id).get("command_experience", key) or {}
        record = {
            "experience_id": key,
            "connection_id": connection_id,
            "driver_id": driver_id,
            "command": command,
            "status": status,
            "error_code": str(item.get("error_code") or ""),
            "device_error": str(item.get("device_error") or "")[:200],
            "observations": int(previous.get("observations") or 0) + 1,
            "last_observed_at": now_iso(),
            "advisory_only": True,
        }
        _store(workspace_id).save("command_experience", key, record)
        recorded.append(record)
    return recorded


def list_command_experience(
    workspace_id: str,
    *,
    connection_ids: list[str] | None = None,
    limit: int = 80,
) -> list[dict[str, Any]]:
    allowed = None if connection_ids is None else {str(item) for item in connection_ids if str(item)}
    records = _store(workspace_id).list("command_experience", limit=max(1, min(limit, 500)))
    if allowed is not None:
        records = [item for item in records if str(item.get("connection_id") or "") in allowed]
    records.sort(key=lambda item: str(item.get("last_observed_at") or ""), reverse=True)
    return records[:limit]


def record_inspection_observation(workspace_id: str, task: dict[str, Any]) -> dict[str, Any]:
    """Persist one time-bound observation; never declare that it is normal."""
    from core.runtime_engine.context_contract import normalize_observation_descriptor, normalize_reference_descriptor

    observation_id = _id("observation")
    target_ids = sorted(str(item) for item in (task.get("results") or {}).keys())
    snapshot = {
        target_id: {
            "status": str((task.get("results") or {}).get(target_id, {}).get("status") or "unknown"),
            "output_hash": str((task.get("results") or {}).get(target_id, {}).get("output_hash") or ""),
        }
        for target_id in target_ids
    }
    status = str(task.get("status") or "unknown")
    completeness = "complete" if status == "succeeded" else "partial" if status == "partial" else "failed" if status in {"failed", "cancelled"} else "unknown"
    scope_key = hashlib.sha256("|".join(target_ids).encode()).hexdigest()[:20]
    observation = normalize_observation_descriptor({
        "observation_id": observation_id,
        "source_kind": "network_inspection",
        "source_id": str(task.get("task_id") or ""),
        "artifact_id": str(task.get("artifact_id") or ""),
        "observed_at": str(task.get("finished_at") or now_iso()),
        "completeness": completeness,
        "scope_key": scope_key,
        "target_ids": target_ids,
        "snapshot": snapshot,
        "created_at": now_iso(),
    })
    _store(workspace_id).save("observations", observation_id, observation)
    candidate_id = ""
    if completeness in {"complete", "partial"}:
        candidate_id = _id("reference")
        candidate = normalize_reference_descriptor({
            "reference_id": candidate_id,
            "name": f"巡检候选参考 {str(task.get('task_id') or '')[-8:]}",
            "state": "candidate",
            "authority": "observed",
            "current": False,
            "scope_key": scope_key,
            "target_ids": target_ids,
            "source_observation_ids": [observation_id],
            "snapshot": snapshot,
            "completeness": completeness,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        })
        _store(workspace_id).save("references", candidate_id, candidate)
        observation["candidate_reference_id"] = candidate_id
        _store(workspace_id).save("observations", observation_id, observation)
    return observation


def list_observations(workspace_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    records = _store(workspace_id).list("observations", limit=max(1, min(limit, 500)))
    records.sort(key=lambda item: str(item.get("observed_at") or ""), reverse=True)
    return records[:limit]


def list_references(workspace_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    records = _store(workspace_id).list("references", limit=max(1, min(limit, 500)))
    records.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return records[:limit]


@_connection_transaction
def delete_observation(workspace_id: str, observation_id: str) -> dict[str, Any]:
    """Hard-delete an observation and every reference that depends on it.

    A reference without its source observation is invalid evidence.  Deleting
    one therefore removes those dependent reference records in the same
    workspace transaction; inspection task history and artifacts are retained
    because they are separate execution/audit records.
    """
    store = _store(workspace_id)
    if not store.get("observations", observation_id):
        raise ValueError("observation_not_found")
    deleted_references = 0
    for reference in list_references(workspace_id, limit=INTERNAL_SCAN_LIMIT):
        source_ids = {str(item) for item in reference.get("source_observation_ids") or []}
        if observation_id in source_ids and store.delete("references", str(reference["reference_id"])):
            deleted_references += 1
    store.delete("observations", observation_id)
    return {
        "deleted": True,
        "observation_id": observation_id,
        "deleted_dependent_references": deleted_references,
    }


@_connection_transaction
def delete_reference(workspace_id: str, reference_id: str) -> bool:
    """Hard-delete a user-visible operational reference record."""
    return _store(workspace_id).delete("references", reference_id)


@_connection_transaction
def delete_command_experience(workspace_id: str, experience_id: str) -> bool:
    """Hard-delete one advisory command-feedback record."""
    return _store(workspace_id).delete("command_experience", experience_id)


@_connection_transaction
def transition_reference(workspace_id: str, reference_id: str, action: str) -> dict[str, Any]:
    """Confirm or invalidate a candidate through an explicit human action."""
    from core.runtime_engine.context_contract import normalize_reference_descriptor

    record = _store(workspace_id).get("references", reference_id)
    if not record:
        raise ValueError("reference_not_found")
    action = str(action or "").lower()
    if action == "confirm":
        if record.get("state") != "candidate":
            raise ValueError("only_candidate_reference_can_be_confirmed")
        if record.get("completeness") != "complete":
            raise ValueError("complete_observation_required_for_confirmation")
        for current in list_references(workspace_id, limit=500):
            if current.get("state") == "confirmed" and current.get("current") and current.get("scope_key") == record.get("scope_key"):
                current.update({"state": "superseded", "current": False, "updated_at": now_iso(), "superseded_by": reference_id})
                _store(workspace_id).save("references", current["reference_id"], current)
        record.update({"state": "confirmed", "authority": "user_confirmed", "current": True, "confirmed_at": now_iso(), "updated_at": now_iso()})
    elif action == "invalidate":
        if record.get("state") not in {"candidate", "confirmed"}:
            raise ValueError("reference_cannot_be_invalidated")
        record.update({"state": "invalidated", "current": False, "invalidated_at": now_iso(), "updated_at": now_iso()})
    else:
        raise ValueError("reference_action_must_be_confirm_or_invalidate")
    normalized = normalize_reference_descriptor(record)
    _store(workspace_id).save("references", reference_id, normalized)
    return normalized


def operational_context(
    workspace_id: str,
    *,
    connection_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Return bounded, source-labelled context without performing network IO."""
    allowed = None if connection_ids is None else {str(item) for item in connection_ids if str(item)}
    observations = list_observations(workspace_id, limit=24)
    references = list_references(workspace_id, limit=40)
    if allowed is not None:
        observations = [item for item in observations if set(item.get("target_ids") or []).intersection(allowed)]
        references = [item for item in references if set(item.get("target_ids") or []).intersection(allowed)]
    def bounded_record(item: dict[str, Any]) -> dict[str, Any]:
        target_ids = list(item.get("target_ids") or [])
        snapshot = item.get("snapshot") if isinstance(item.get("snapshot"), dict) else {}
        return {
            key: value for key, value in item.items()
            if key not in {"snapshot", "target_ids"}
        } | {
            "target_ids": target_ids[:20],
            "omitted_target_count": max(0, len(target_ids) - 20),
            "snapshot": {target_id: snapshot.get(target_id) for target_id in target_ids[:20] if target_id in snapshot},
        }

    command_experience = list_command_experience(
        workspace_id,
        connection_ids=None if allowed is None else list(allowed),
        limit=40,
    )
    return {
        "observations": [bounded_record(item) for item in observations[:12]],
        "references": [bounded_record(item) for item in references[:20]],
        "command_experience": command_experience,
        "sources": [
            {"source_id": "live_cli", "kind": "live_observation", "available": True, "authority": "observed"},
            {"source_id": "inspection_history", "kind": "historical_observation", "available": bool(observations), "authority": "observed"},
            {"source_id": "confirmed_reference", "kind": "comparison_reference", "available": any(item.get("state") == "confirmed" and item.get("current") for item in references), "authority": "user_confirmed"},
            {"source_id": "command_experience", "kind": "syntax_feedback", "available": bool(command_experience), "authority": "observed", "advisory_only": True},
        ],
        "reference_rule": "observations_describe_a_point_in_time; only_current_user_confirmed_references_describe_expected_state",
        "first_observation_rule": "never_assume_normal",
    }


def inspection_evidence_summary(workspace_id: str, task_id: str) -> dict[str, Any]:
    """Return a safe evidence index without disclosing raw SSH output."""
    task = get_inspection(workspace_id, task_id)
    if not task:
        raise ValueError("inspection_not_found")
    is_connection_task = task.get("target_kind") == "connection" or bool(task.get("connection_ids"))
    snapshots = task.get("target_snapshots") if isinstance(task.get("target_snapshots"), dict) else {}
    devices = []
    for target_id, result in sorted((task.get("results") or {}).items()):
        snapshot = snapshots.get(target_id) if isinstance(snapshots.get(target_id), dict) else {}
        item = {
            "name": result.get("name", ""),
            "host": result.get("host", ""),
            "status": result.get("status", ""),
            "command_count": len(result.get("commands") or []),
            "output_hash": result.get("output_hash", ""),
            "duration_ms": result.get("duration_ms", 0),
            "error": result.get("error", ""),
        }
        if is_connection_task:
            item["connection_id"] = target_id
            item["device_id"] = str(snapshot.get("device_id") or "")
            item["protocol"] = str(snapshot.get("protocol") or "")
        else:
            item["asset_id"] = target_id
        devices.append(item)
    return {
        "ok": True,
        "task_id": task_id,
        "artifact_id": task.get("artifact_id", ""),
        "artifact_sensitivity": "secret",
        "devices": devices,
    }
