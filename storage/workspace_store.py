"""Workspace repository.

This module owns workspace discovery and state persistence. Control-plane code
must go through this data-layer boundary instead of filesystem adapters.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from storage.atomic_io import atomic_write_json, atomic_write_text
from storage.paths import ensure_workspace_storage_dirs, get_workspace_root, user_data_root, workspace_catalog_root, workspace_root
from storage.run_record_store import is_run_record_file
from storage.ids import is_valid_workspace_id, validate_workspace_id
from storage.locking import FileLock

_LOG = logging.getLogger(__name__)


def ensure_workspace(ws_id: str = "default") -> str:
    ws_id = validate_workspace_id(ws_id)
    catalog = workspace_catalog_root(ws_id)
    with FileLock(catalog / "sys" / "workspace-init.lock"):
        yaml_path = catalog / "sys" / "workspace.yaml"
        if not yaml_path.exists():
            atomic_write_text(
                yaml_path,
                f"id: {ws_id}\nname: {ws_id}\ncreated: {time.time()}\n",
            )
        state_path = catalog / "sys" / "state.json"
        if not state_path.exists():
            atomic_write_json(state_path, _default_state(ws_id))

    ws = workspace_root(ws_id)
    for dirname in ("runs", "sessions", "sys"):
        (ws / dirname).mkdir(parents=True, exist_ok=True)
    ensure_workspace_storage_dirs(ws_id)

    with FileLock(ws / "sys" / "workspace-init.lock"):
        yaml_path = ws / "sys" / "workspace.yaml"
        if not yaml_path.exists():
            atomic_write_text(
                yaml_path,
                f"id: {ws_id}\nname: {ws_id}\ncreated: {time.time()}\n",
            )

        state_path = ws / "sys" / "state.json"
        if not state_path.exists():
            atomic_write_json(state_path, _default_state(ws_id))

        for index_name in ("files.jsonl", "references.jsonl", "artifacts.jsonl"):
            index_path = ws / "index" / index_name
            if not index_path.exists():
                atomic_write_text(index_path, "")
    return ws_id


def provision_user_storage(username: str, user_id: str, workspace_ids: list[str]) -> None:
    """Create a user's durable root and every workspace they are granted.

    This is called by identity management, not by a first chat request, so a
    newly created account has an auditable filesystem boundary immediately.
    """
    from storage.principal import storage_principal

    root = user_data_root(user_id)
    root.mkdir(parents=True, exist_ok=True)
    profile = root / "profile.json"
    if not profile.exists():
        atomic_write_json(profile, {
            "schema_version": 1,
            "user_id": user_id,
            "username": str(username),
            "created_at": _now_iso(),
        })
    with storage_principal(username):
        for workspace_id in sorted(set(workspace_ids or [])):
            ensure_workspace(workspace_id)


def delete_user_storage(user_id: str) -> None:
    """Remove only the validated data roots belonging to one deleted user."""
    root = user_data_root(user_id)
    if root.exists():
        shutil.rmtree(root)


def get_workspace_state(ws_id: str = "default") -> dict:
    ws_id = validate_workspace_id(ws_id)
    try:
        state = json.loads((workspace_root(ws_id) / "sys" / "state.json").read_text(encoding="utf-8"))
        state["runs_count"] = _count_runs(ws_id)
        state["memory_count"] = _count_memory(ws_id)
        state["artifacts_count"] = _count_artifacts(ws_id)
        return state
    except Exception:
        return {}


def update_workspace_state(ws_id: str, patch: dict) -> dict:
    ws_id = ensure_workspace(ws_id)
    state_path = workspace_root(ws_id) / "sys" / "state.json"
    with FileLock(state_path.with_name("state.lock")):
        state = get_workspace_state(ws_id)
        safe_patch = {}
        for key, value in (patch or {}).items():
            if isinstance(value, str) and len(value) > 500 and key not in ("last_result_summary",):
                safe_patch[key] = value[:500] + "...[truncated]"
            else:
                safe_patch[key] = value
        state.update(safe_patch)
        state["updated_at"] = _now_iso()
        state["runs_count"] = _count_runs(ws_id)
        atomic_write_json(workspace_root(ws_id) / "sys" / "state.json", state)
    _update_workspace_catalog(ws_id, patch)
    return state


def list_workspaces() -> list[dict]:
    workspaces: list[dict] = []
    base = get_workspace_root() / "catalog"
    if not base.is_dir():
        ensure_workspace("default")
    dirs = [path for path in base.iterdir() if path.is_dir() and not path.name.startswith(".")]
    dirs.sort(key=lambda path: (0 if path.name == "default" else 1, _is_test_workspace(path.name), path.name))
    for path in dirs:
        if not is_valid_workspace_id(path.name):
            continue
        ws_id = path.name
        artifacts_count = _count_artifacts(ws_id)
        workspaces.append(
            {
                "workspace_id": ws_id,
                "organization_id": _workspace_organization(ws_id),
                "owner_username": _workspace_owner(ws_id),
                "name": _workspace_display_name(ws_id),
                "created_at": _workspace_created_at(ws_id),
                "is_default": ws_id == "default",
                "runs_count": _count_runs(ws_id),
                "artifacts_count": artifacts_count,
                "memory_count": _count_memory(ws_id),
                "stats": {
                    "session_count": _count_sessions(ws_id),
                    "artifact_count": artifacts_count,
                    "knowledge_source_count": _count_knowledge_sources(ws_id),
                },
            }
        )
    return workspaces


def list_workspace_ids(root: Path | None = None, *, include_system: bool = False) -> list[str]:
    base = root or get_workspace_root() / "catalog"
    ids: list[str] = []
    if base.is_dir():
        for path in base.iterdir():
            if not path.is_dir() or path.name.startswith("_"):
                continue
            try:
                ids.append(validate_workspace_id(path.name))
            except (TypeError, ValueError):
                continue
    # A no-principal maintenance task may still own transient system records.
    # Authenticated user data is discovered by binding each known principal.
    if include_system and root is None and get_workspace_root().is_dir():
        for path in get_workspace_root().iterdir():
            if not path.is_dir() or path.name in {"_runtime", "catalog", "users"}:
                continue
            try:
                ids.append(validate_workspace_id(path.name))
            except (TypeError, ValueError):
                continue
    return sorted(set(ids))


def rename_workspace(old_id: str, new_id: str) -> dict:
    old_id = validate_workspace_id(old_id)
    new_id = validate_workspace_id(new_id)
    old_path = workspace_catalog_root(old_id)
    new_path = workspace_catalog_root(new_id)
    if not old_path.is_dir():
        return {"ok": False, "error": "workspace not found"}
    if new_path.exists():
        return {"ok": False, "error": "target workspace already exists"}
    try:
        user_roots = _user_workspace_paths(old_id)
        if any(path.with_name(new_id).exists() for path in user_roots):
            return {"ok": False, "error": "target workspace already exists"}
        old_path.rename(new_path)
        _rewrite_workspace_identity(new_path, new_id)
        for user_path in user_roots:
            new_user_path = user_path.with_name(new_id)
            user_path.rename(new_user_path)
            _rewrite_workspace_identity(new_user_path, new_id)
        try:
            from backend.core.identity import replace_workspace
            replace_workspace(old_id, new_id)
        except Exception:
            _LOG.warning("workspace identity mapping rename failed", exc_info=True)
        return {"ok": True, "workspace_id": new_id}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def delete_workspace(ws_id: str) -> dict:
    ws_id = validate_workspace_id(ws_id)
    if ws_id == "default":
        return {"ok": False, "error": "cannot delete default workspace"}
    ws_path = workspace_catalog_root(ws_id)
    if not ws_path.is_dir():
        return {"ok": False, "error": "workspace not found"}
    try:
        shutil.rmtree(ws_path)
        for user_path in _user_workspace_paths(ws_id):
            shutil.rmtree(user_path)
        try:
            from backend.core.identity import replace_workspace
            replace_workspace(ws_id)
        except Exception:
            _LOG.warning("workspace identity mapping delete failed", exc_info=True)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def batch_delete_workspaces(ws_ids: list) -> dict:
    deleted = []
    failed = []
    for raw_ws_id in ws_ids:
        try:
            ws_id = validate_workspace_id(raw_ws_id)
        except ValueError:
            failed.append({"id": raw_ws_id, "error": "invalid workspace id"})
            continue
        result = delete_workspace(ws_id)
        if result.get("ok"):
            deleted.append(ws_id)
        else:
            failed.append({"id": ws_id, "error": result.get("error", "delete failed")})
    return {"ok": True, "deleted": deleted, "failed": failed, "total": len(ws_ids)}


def get_workspace_runs(ws_id: str = "default") -> list[dict]:
    ws_id = ensure_workspace(ws_id)
    runs_dir = workspace_root(ws_id) / "runs"
    runs = []
    if runs_dir.is_dir():
        for path in sorted(runs_dir.glob("*.json"), reverse=True):
            if not is_run_record_file(path):
                continue
            try:
                runs.append(json.loads(path.read_text(encoding="utf-8-sig")))
            except Exception:
                _LOG.debug("corrupt run file: %s", path, exc_info=True)
    return runs


def get_run(run_id: str, ws_id: str = "default") -> Optional[dict]:
    ws_id = validate_workspace_id(ws_id)
    path = workspace_root(ws_id) / "runs" / f"{run_id}.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            _LOG.debug("corrupt run file: %s", path, exc_info=True)
    return None


def _default_state(ws_id: str) -> dict:
    return {
        "workspace_id": ws_id,
        "organization_id": "default",
        "name": ws_id,
        "last_run_id": "",
        "last_intent": "",
        "last_result_summary": "",
        "last_result_counts": {},
        "last_manual_review_samples": [],
        "last_unsupported_samples": [],
        "last_audit_summary": {},
        "current_files": [],
        "current_artifacts": [],
        "llm_metadata": {},
        "runs_count": 0,
        "memory_count": 0,
        "artifacts_count": 0,
        "updated_at": "",
    }


def _workspace_organization(ws_id: str) -> str:
    try:
        value = json.loads((workspace_catalog_root(ws_id) / "sys" / "state.json").read_text(encoding="utf-8"))
        return str(value.get("organization_id") or "default")
    except (OSError, json.JSONDecodeError, AttributeError):
        return "default"


def _workspace_owner(ws_id: str) -> str:
    try:
        value = json.loads((workspace_catalog_root(ws_id) / "sys" / "state.json").read_text(encoding="utf-8"))
        return str(value.get("owner_username") or "")
    except (OSError, json.JSONDecodeError, AttributeError):
        return ""


def _count_runs(ws_id: str) -> int:
    ws_id = validate_workspace_id(ws_id)
    runs_dir = workspace_root(ws_id) / "runs"
    if runs_dir.is_dir():
        return len([path for path in runs_dir.glob("*.json") if is_run_record_file(path)])
    return 0


def _count_sessions(ws_id: str) -> int:
    ws_id = validate_workspace_id(ws_id)
    sessions_dir = workspace_root(ws_id) / "sessions"
    if not sessions_dir.is_dir():
        return 0
    count = 0
    for path in sessions_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("status", "active") == "active":
            count += 1
    return count


def _count_artifacts(ws_id: str) -> int:
    ws_id = validate_workspace_id(ws_id)
    path = workspace_root(ws_id) / "index" / "artifacts.jsonl"
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("lifecycle", "active") != "deleted":
            count += 1
    return count


def _count_knowledge_sources(ws_id: str) -> int:
    ws_id = validate_workspace_id(ws_id)
    return _count_context_items(ws_id, "knowledge_source")


def _count_memory(ws_id: str) -> int:
    ws_id = validate_workspace_id(ws_id)
    memory_dir = workspace_root(ws_id) / "memory"
    if memory_dir.is_dir():
        count = 0
        for path in memory_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("status") == "active":
                count += 1
        return count
    return _count_context_items(ws_id, "memory_hit")


def _count_context_items(ws_id: str, item_type: str) -> int:
    context_dir = workspace_root(ws_id) / "context"
    if not context_dir.is_dir():
        return 0
    count = 0
    for path in context_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("item_type") == item_type and data.get("deleted_at", "") == "":
            count += 1
    items_path = context_dir / "items.jsonl"
    if items_path.is_file():
        latest: dict[str, dict] = {}
        for line in items_path.read_text(encoding="utf-8").splitlines():
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("item_type") == item_type:
                latest[str(data.get("item_id", ""))] = data
        count += sum(1 for data in latest.values() if data.get("deleted_at", "") == "")
    return count


def _workspace_display_name(ws_id: str) -> str:
    yaml_path = workspace_catalog_root(ws_id) / "sys" / "workspace.yaml"
    if yaml_path.is_file():
        try:
            for line in yaml_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip("'\"")
                    return name or ws_id
        except Exception:
            pass
    return ws_id


def _workspace_created_at(ws_id: str) -> str:
    yaml_path = workspace_catalog_root(ws_id) / "sys" / "workspace.yaml"
    if yaml_path.is_file():
        try:
            for line in yaml_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("created:"):
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
    return ""


def _rewrite_workspace_identity(path: Path, ws_id: str) -> None:
    yaml_path = path / "sys" / "workspace.yaml"
    if yaml_path.is_file():
        try:
            updated = []
            for line in yaml_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("id: "):
                    updated.append(f"id: {ws_id}")
                elif line.startswith("name: "):
                    updated.append(f"name: {ws_id}")
                else:
                    updated.append(line)
            atomic_write_text(yaml_path, "\n".join(updated) + "\n")
        except Exception:
            pass
    state_path = path / "sys" / "state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["workspace_id"] = ws_id
            state["name"] = ws_id
            atomic_write_json(state_path, state)
        except Exception:
            pass


def _is_test_workspace(ws_id: str) -> int:
    test_markers = ("test", "e2e", "api_contract", "closure_", "ws_")
    return 1 if any(marker in ws_id for marker in test_markers) else 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update_workspace_catalog(ws_id: str, patch: dict) -> None:
    """Keep shared workspace metadata out of any one user's data directory."""
    shared = {key: value for key, value in (patch or {}).items()
              if key in {"organization_id", "owner_username", "name"}}
    if not shared:
        return
    path = workspace_catalog_root(ws_id) / "sys" / "state.json"
    with FileLock(path.with_name("state.lock")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = _default_state(ws_id)
        state.update(shared)
        atomic_write_json(path, state)


def _user_workspace_paths(ws_id: str) -> list[Path]:
    """Return every user-owned data directory for a logical workspace."""
    users_root = get_workspace_root() / "users"
    if not users_root.is_dir():
        return []
    paths: list[Path] = []
    for user_root in users_root.iterdir():
        candidate = user_root / "workspaces" / ws_id
        if candidate.is_dir():
            paths.append(candidate)
    return paths
