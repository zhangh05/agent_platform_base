"""Fail-closed coordination for durable approval continuations."""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

from observability.metrics import record_operation, set_operational_gauge
from storage.locking import FileLock
from storage.records import workspace_record_file

_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_STOP = threading.Event()
_FAILURE_COUNT = 0


def _interval_seconds() -> int:
    try:
        value = int(os.environ.get("LZCORE_CONTINUATION_RECONCILE_SECONDS", "30"))
    except ValueError:
        value = 30
    return max(10, min(value, 3600))


def _with_leader_lease(workspace_id: str, callback):
    """Use a filesystem process lock in file mode; Redis lease in distributed mode."""
    redis_url = str(os.environ.get("LZCORE_REDIS_URL") or "").strip()
    if redis_url:
        try:
            import redis

            from storage.principal import (
                current_storage_principal,
                principal_storage_key,
            )

            client = redis.from_url(redis_url)
            principal = current_storage_principal()
            principal_key = principal_storage_key(principal) if principal else "system"
            key = f"lzcore:continuation-reconciler:{principal_key}:{workspace_id}"
            token = os.urandom(16).hex()
            if not client.set(key, token, nx=True, ex=max(15, _interval_seconds() * 2)):
                return None
            try:
                return callback()
            finally:
                # Compare-and-delete must be atomic. A GET followed by DELETE
                # can erase a successor's lease if this lease expires between
                # those two commands.
                client.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('del', KEYS[1]) else return 0 end",
                    1,
                    key,
                    token,
                )
        except Exception:  # noqa: BLE001 - optional distributed lease must fail closed
            record_operation("continuation_reconcile", "lease_failed")
            return None
    lock_path = workspace_record_file(workspace_id, "approvals", ".continuation-reconciler.lock")
    with FileLock(lock_path, timeout=0.0):
        return callback()


def reconcile_workspace(workspace_id: str) -> dict[str, int]:
    """Reconcile durable decisions and stale states without executing tools."""
    def _run() -> dict[str, int]:
        from agent.approval import get_approval_store
        from agent.runtime.approval_continuation import (
            list_continuations,
            maintain_continuations,
            reconcile_decisions_from_guardian,
        )
        store = get_approval_store(workspace_id)
        repaired = reconcile_decisions_from_guardian(
            workspace_id,
            store.get_history(workspace_id=workspace_id, limit=5000),
        )
        maintained = maintain_continuations(workspace_id, force=True)
        public_by_state = {
            name: list_continuations(workspace_id, status=name, limit=5000)
            for name in ("pending", "ready", "claimed", "dispatching", "stalled", "expired")
        }
        states = {name: len(items) for name, items in public_by_state.items()}
        oldest_age = 0.0
        now = datetime.now(timezone.utc)
        for item in public_by_state["pending"] + public_by_state["ready"]:
            try:
                created = datetime.fromisoformat(str(item.get("created_at") or "").replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                oldest_age = max(oldest_age, (now - created).total_seconds())
            except (TypeError, ValueError):
                continue
        record_operation("continuation_reconcile", "succeeded")
        return {
            **repaired,
            **maintained,
            **states,
            "oldest_pending_age_seconds": int(max(0.0, oldest_age)),
        }
    result = _with_leader_lease(workspace_id, _run)
    return result or {"skipped": 1}


def reconcile_all_workspaces() -> dict[str, dict[str, int]]:
    from backend.core.identity import get_user
    from storage.principal import (
        known_storage_principals,
        principal_storage_key,
        storage_principal,
    )
    from storage.workspace_store import list_workspace_ids

    global _FAILURE_COUNT
    outcomes: dict[str, dict[str, int]] = {}
    started = time.monotonic()
    totals = {
        name: 0
        for name in ("pending", "ready", "claimed", "dispatching", "stalled", "expired")
    }
    oldest_pending_age = 0
    decision_mismatches = 0
    all_workspace_ids = list_workspace_ids(include_system=False)
    principals = known_storage_principals()
    # Authentication-disabled local deployments persist under the system
    # workspace root. Once principals exist, only their isolated roots are
    # eligible and no legacy/system data is merged into user state.
    for principal in principals or [""]:
        identity = get_user(principal)
        workspace_ids = (
            list(identity.get("workspace_ids") or [])
            if isinstance(identity, dict)
            else all_workspace_ids
        )
        with storage_principal(principal):
            principal_key = principal_storage_key(principal) if principal else "system"
            for workspace_id in sorted(set(workspace_ids)):
                try:
                    outcome = reconcile_workspace(workspace_id)
                    outcomes[f"{principal_key}:{workspace_id}"] = outcome
                    for name in totals:
                        totals[name] += int(outcome.get(name) or 0)
                    oldest_pending_age = max(
                        oldest_pending_age,
                        int(outcome.get("oldest_pending_age_seconds") or 0),
                    )
                    decision_mismatches += int(outcome.get("decision_mismatch") or 0)
                except Exception:  # noqa: BLE001 - isolate one scope and continue reconciliation
                    _FAILURE_COUNT += 1
                    record_operation("continuation_reconcile", "failed")
    for name, value in totals.items():
        set_operational_gauge(f"continuation_{name}", value)
    set_operational_gauge("continuation_oldest_pending_age_seconds", oldest_pending_age)
    set_operational_gauge("continuation_decision_mismatch", decision_mismatches)
    set_operational_gauge("continuation_reconciliation_lag_seconds", time.monotonic() - started)
    set_operational_gauge("continuation_reconciliation_failure_count", _FAILURE_COUNT)
    return outcomes


def start_continuation_reconciler() -> None:
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP.clear()
        def _loop() -> None:
            while not _STOP.wait(_interval_seconds()):
                reconcile_all_workspaces()
        _THREAD = threading.Thread(target=_loop, name="continuation-reconciler", daemon=True)
        _THREAD.start()


def stop_continuation_reconciler() -> None:
    _STOP.set()
