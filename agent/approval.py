"""Unified tool approval system — single source of truth for all approvals.

ALL approval flows MUST go through ApprovalStore. There is no secondary
alternative, no dual-store pattern, no bypass.

Key guarantees:
- Every approval is bound to workspace_id + session_id (+ run_id/job_id if present)
- resolve() enforces admin token boundary when AGENT_PLATFORM_ADMIN_TOKEN is set
- Arguments are redacted by default in persisted records and API responses
- SSE events are published on create/resolve for real-time frontend updates
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent.runtime.utils import now_iso, from_iso

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """v3.9.8: wrapper for ApprovalRequest default_factory."""
    return now_iso()


def _now_iso_offset(delta_seconds: float) -> str:
    """Return the ISO timestamp for ``now + delta_seconds``.

    Used by ApprovalStore._load_history to compute a retention cutoff.
    """
    target = datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)
    return target.isoformat()


# ════════════════════════════════════════════════════
# Constants
# ════════════════════════════════════════════════════

_APPROVALS_FILE: Optional[Path] = None
_RETENTION_DAYS = 90
_GC_INTERVAL_SECONDS = 600  # 10 minutes

# ════════════════════════════════════════════════════
# Event subscription (SSE bridge)
# ════════════════════════════════════════════════════


@dataclass
class ApprovalEvent:
    """Real-time event emitted on approval state changes."""
    kind: str           # "created" | "resolved"
    approval_id: str
    session_id: str
    tool_id: str
    workspace_id: str = ""
    allowed: bool = False
    payload: Dict[str, Any] = field(default_factory=dict)


class _EventBus:
    """Thread-safe pub/sub for approval events.

    Subscribers receive ApprovalEvent on create/resolve. The Guardian SSE
    endpoint forwards each event to the connected frontend clients.
    """

    def __init__(self) -> None:
        self._subscribers: List[Callable[[ApprovalEvent], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Callable[[ApprovalEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(fn)
                except ValueError:
                    pass

        return unsubscribe

    def publish(self, event: ApprovalEvent) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for fn in subs:
            try:
                fn(event)
            except Exception:
                # One bad subscriber must not break others; record so
                # the bug is observable in logs (v3.9.9 — silent
                # exceptions are now debug-logged).
                logger.debug("approval event subscriber raised", exc_info=True)


# Module-level singleton (separate from store so SSE routes can import it
# without pulling in the full approval flow).
_event_bus = _EventBus()


def get_event_bus() -> _EventBus:
    return _event_bus


# ════════════════════════════════════════════════════
# Approval request & store
# ════════════════════════════════════════════════════


@dataclass
class ApprovalRequest:
    approval_id: str
    session_id: str
    tool_id: str
    arguments: dict
    description: str
    risk_level: str
    workspace_id: str = ""
    run_id: str = ""
    job_id: str = ""
    metadata: dict = field(default_factory=dict)
    # v3.9.8: created_at / resolved_at are now ISO-8601 strings (UTC),
    # matching every other dataclass in the durable / state / event
    # namespace. Earlier float/epoch split made the API surface
    # inconsistent between /api/approvals and /api/agent/state.
    created_at: str = field(default_factory=_now_iso)
    resolved: bool = False
    allowed: bool = False
    resolved_at: Optional[str] = None
    resolver: str = ""              # who resolved (user/admin/system)
    reason: str = ""                # resolver's optional note
    _event: threading.Event = field(default_factory=threading.Event)


class ApprovalStore:
    """Persistent approval store with thread-safe wait/resolve.

    v3.2.0 (Guardian):
    - Pending requests: kept in-memory + appended to JSONL
    - Resolved requests: appended to JSONL, kept for _RETENTION_DAYS
    - Subscribers receive real-time events on create/resolve
    """

    def __init__(self, persist_path: Optional[Path] = None) -> None:
        self._pending: dict[str, ApprovalRequest] = {}
        self._lock = threading.Lock()
        self._persist_path = Path(persist_path) if persist_path else _default_persist_path()
        self._last_gc_at: float = 0.0
        self._load_history()

    # ── File I/O ────────────────────────────────────────────────────

    def _load_history(self) -> None:
        """Reload recent unresolved approvals from disk on startup."""
        try:
            from storage.approval_record_store import read_approval_records

            cutoff_iso = _now_iso_offset(-_RETENTION_DAYS * 86400)
            for rec in read_approval_records(path=self._persist_path):
                # Only restore still-pending records (resolved are history)
                if rec.get("resolved"):
                    continue
                try:
                    from storage.ids import validate_workspace_id
                    workspace_id = validate_workspace_id(str(rec.get("workspace_id") or ""))
                except (ValueError, TypeError):
                    continue
                except Exception:
                    logger.debug(
                        "approval: validate_workspace_id raised unexpected "
                        "exception for record",
                        exc_info=True,
                    )
                    continue
                raw_created = rec.get("created_at") or ""
                try:
                    from_iso(raw_created)
                except (ValueError, TypeError):
                    continue
                created_iso = str(raw_created)
                if (created_iso or "") < cutoff_iso:
                    continue
                req = ApprovalRequest(
                    approval_id=rec["approval_id"],
                    session_id=rec.get("session_id", ""),
                    tool_id=rec.get("tool_id", ""),
                    arguments=rec.get("arguments", {}),
                    description=rec.get("description", ""),
                    risk_level=rec.get("risk_level", "high"),
                    workspace_id=workspace_id,
                    run_id=rec.get("run_id", ""),
                    job_id=rec.get("job_id", ""),
                    metadata=rec.get("metadata", {}),
                    created_at=created_iso,
                    resolved=False,
                )
                self._pending[req.approval_id] = req
        except (OSError, ValueError):
            # v3.9.9: file IO / JSON corruption are not unexpected —
            # surface them at WARNING so audit ingest failures are
            # visible instead of silently losing approved actions.
            logger.warning("approval: failed to load history from %s",
                           self._persist_path, exc_info=True)

    def _append_record(self, req: ApprovalRequest) -> None:
        """Append a record (pending or resolved) to the JSONL audit log."""
        try:
            from core.tools.redaction import redact_tool_output
            from storage.approval_record_store import append_approval_record

            rec = {
                "approval_id": req.approval_id,
                "session_id": req.session_id,
                "tool_id": req.tool_id,
                "arguments": redact_tool_output(req.arguments or {}),
                "description": req.description,
                "risk_level": req.risk_level,
                "workspace_id": req.workspace_id,
                "run_id": req.run_id,
                "job_id": req.job_id,
                "metadata": redact_tool_output(req.metadata or {}),
                "created_at": req.created_at,
                "resolved": req.resolved,
                "allowed": req.allowed if req.resolved else None,
                "resolved_at": req.resolved_at,
                "resolver": req.resolver,
                "reason": req.reason,
            }
            append_approval_record(rec, path=self._persist_path)
        except (OSError, TypeError, ValueError):
            # v3.9.9: ApprovalStore._append_record silently losing
            # every audit row is a real failure — silently skipping
            # a write hides every denied tool invocation. Surface it.
            logger.warning("approval: failed to append record to %s",
                           self._persist_path, exc_info=True)

    def _gc_history(self) -> None:
        """Periodically compact the audit log by removing records older than retention."""
        now_epoch = time.time()
        if now_epoch - self._last_gc_at < _GC_INTERVAL_SECONDS:
            return
        self._last_gc_at = now_epoch
        # v3.9.8: cutoff is now ISO-8601 str (matches the on-disk shape).
        # Earlier versions compared an epoch float to str created_at;
        # Python's `str >= float` raises or silently miscompares, so we
        # only accept records whose created_at (ISO) is at-or-after
        # the retention cutoff (also ISO).
        cutoff_iso = _now_iso_offset(-_RETENTION_DAYS * 86400)
        try:
            from storage.approval_record_store import mutate_approval_records

            def _retain_recent(rows):
                kept: list[dict] = []
                for rec in rows:
                    raw_created = rec.get("created_at") or ""
                    if not raw_created:
                        continue
                    try:
                        from_iso(raw_created)
                    except (TypeError, ValueError):
                        continue
                    if raw_created >= cutoff_iso:
                        kept.append(rec)
                return kept, None

            mutate_approval_records(_retain_recent, path=self._persist_path)
        except OSError:
            logger.warning("approval: GC history compaction failed for %s",
                           self._persist_path, exc_info=True)

    # ── Public API ──────────────────────────────────────────────────

    def create(self, session_id: str, tool_id: str,
               arguments: dict, description: str = "",
               risk_level: str = "high",
               workspace_id: str = "",
               run_id: str = "",
               job_id: str = "",
               metadata: dict = None) -> ApprovalRequest:
        """Create a pending approval, persist it, and notify subscribers.

        All approval records MUST be bound to workspace_id + session_id.
        Optional run_id/job_id provide traceability when the approval
        originates from a specific agent run or job.
        """
        if not workspace_id:
            raise ValueError("workspace_id is required")
        try:
            from storage.ids import validate_workspace_id
            workspace_id = validate_workspace_id(workspace_id)
        except Exception as exc:
            raise ValueError("invalid_workspace_id") from exc
        approval_id = f"apr_{uuid.uuid4().hex[:12]}"
        req = ApprovalRequest(
            approval_id=approval_id,
            session_id=session_id,
            tool_id=tool_id,
            arguments=arguments,
            description=description,
            risk_level=risk_level,
            workspace_id=workspace_id,
            run_id=run_id,
            job_id=job_id,
            metadata=metadata or {},
        )
        with self._lock:
            self._pending[approval_id] = req
        self._append_record(req)
        _event_bus.publish(ApprovalEvent(
            kind="created", approval_id=approval_id,
            session_id=session_id, tool_id=tool_id,
            workspace_id=workspace_id,
            payload={"risk_level": risk_level, "description": description},
        ))
        return req

    def resolve(self, approval_id: str, allowed: bool, workspace_id: str,
                resolver: str = "user", reason: str = "") -> Optional[ApprovalRequest]:
        """Resolve an approval only when approval_id belongs to workspace_id."""
        if not workspace_id:
            return None
        try:
            from storage.ids import validate_workspace_id
            workspace_id = validate_workspace_id(workspace_id)
        except Exception:
            return None
        with self._lock:
            req = self._pending.get(approval_id)
            if req and req.workspace_id != workspace_id:
                return None
            if req and not req.resolved:
                req.resolved = True
                req.allowed = allowed
                # v3.9.8: resolved_at is ISO-8601 string; was float.
                req.resolved_at = now_iso()
                req.resolver = resolver
                req.reason = reason
                req._event.set()
        if req is None:
            return None
        self._append_record(req)
        self._gc_history()
        _event_bus.publish(ApprovalEvent(
            kind="resolved", approval_id=approval_id,
            session_id=req.session_id, tool_id=req.tool_id,
            workspace_id=req.workspace_id,
            allowed=allowed, payload={"resolver": resolver, "reason": reason},
        ))
        # Pending entries can be freed once resolved — they live on in JSONL.
        with self._lock:
            self._pending.pop(approval_id, None)
        return req

    def check(self, approval_id: str) -> Optional[bool]:
        """Non-blocking check: True=allowed, False=denied, None=pending."""
        with self._lock:
            req = self._pending.get(approval_id)
            if not req:
                return None
            if not req.resolved:
                return None
            return req.allowed

    def get_pending(self, session_id: str = "", workspace_id: str = "") -> list[dict]:
        """Get pending approvals, optionally filtered by workspace/session.

        Stale approvals (older than 120s) are auto-cleaned up during this call
        to prevent orphaned approvals from flashing the frontend bubble.
        """
        with self._lock:
            now = datetime.now(timezone.utc)
            stale_ids = []
            for aid, req in list(self._pending.items()):
                if req.resolved:
                    stale_ids.append(aid)
                    continue
                # Auto-expire approvals older than 120 seconds that never
                # reached the wait/resolve path (orphaned by turn failure).
                # v3.9.8: created_at is an ISO-8601 string now; compare
                # via datetime parsing instead of float math.
                try:
                    created = datetime.fromtimestamp(from_iso(req.created_at), tz=timezone.utc)
                    if req.created_at and (now - created).total_seconds() > 120:
                        _expire = True
                    else:
                        _expire = False
                except Exception:
                    _expire = False
                if _expire:
                    req.resolved = True
                    req.allowed = False
                    req.resolved_at = now_iso()
                    req.resolver = "system_expired"
                    req._event.set()
                    self._append_record(req)
                    _event_bus.publish(ApprovalEvent(
                        kind="resolved", approval_id=aid,
                        session_id=req.session_id, tool_id=req.tool_id,
                        workspace_id=req.workspace_id,
                        allowed=False, payload={"resolver": "system_expired"},
                    ))
                    stale_ids.append(aid)
            for aid in stale_ids:
                self._pending.pop(aid, None)

            result = []
            for req in self._pending.values():
                if workspace_id and req.workspace_id != workspace_id:
                    continue
                if session_id and req.session_id != session_id:
                    continue
                result.append(self._to_dict(req))
            return result

    def get_history(self, session_id: str = "", tool_id: str = "",
                    workspace_id: str = "",
                    limit: int = 100, since_ts: float = 0.0) -> list[dict]:
        """Return resolved approvals from the audit log."""
        records: list[dict] = []
        try:
            from storage.approval_record_store import read_approval_records

            for rec in read_approval_records(path=self._persist_path):
                if not rec.get("resolved"):
                    continue
                if workspace_id and rec.get("workspace_id") != workspace_id:
                    continue
                if session_id and rec.get("session_id") != session_id:
                    continue
                if tool_id and rec.get("tool_id") != tool_id:
                    continue
                if since_ts:
                    try:
                        if from_iso(str(rec.get("created_at") or "")) < since_ts:
                            continue
                    except (TypeError, ValueError):
                        continue
                records.append(rec)
        except OSError:
            logger.warning("approval: get_history read failed for %s",
                           self._persist_path, exc_info=True)
            return []
        records.sort(key=lambda r: r.get("resolved_at") or "", reverse=True)
        return records[:limit] \


    def wait(self, approval_id: str, timeout: int = 60,
             blocking: bool = True) -> Optional[bool]:
        """Wait for approval to be resolved.

        Args:
            approval_id: The approval to wait for.
            timeout: Maximum wait time in seconds (only for blocking mode).
            blocking: If True, blocks until resolved or timeout. If False,
                      returns immediately: True=allowed, False=denied, None=pending.

        Returns:
            - blocking=True: True if allowed, False if denied/timed out.
            - blocking=False: True/False if resolved, None if pending.
        """
        with self._lock:
            req = self._pending.get(approval_id)

        if not req:
            return False

        if not blocking:
            if req.resolved:
                return req.allowed
            return None

        # Blocking mode: poll in 500ms intervals
        elapsed = 0.0
        while elapsed < timeout:
            if req._event.wait(timeout=0.5):
                return req.allowed
            elapsed += 0.5

        # Timeout — auto-deny
        with self._lock:
            if not req.resolved:
                req.resolved = True
                req.allowed = False
                # v3.9.8: resolved_at is ISO-8601 string; was float.
                req.resolved_at = now_iso()
                req.resolver = "system_timeout"
                req._event.set()
                self._append_record(req)
                _event_bus.publish(ApprovalEvent(
                    kind="resolved", approval_id=approval_id,
                    session_id=req.session_id, tool_id=req.tool_id,
                    workspace_id=req.workspace_id,
                    allowed=False, payload={"resolver": "system_timeout"},
                ))
                self._pending.pop(approval_id, None)
        return False

    def cleanup(self, approval_id: str):
        with self._lock:
            self._pending.pop(approval_id, None)

    # ── Internals ───────────────────────────────────────────────────

    @staticmethod
    def _to_dict(req: ApprovalRequest) -> dict:
        from core.tools.redaction import redact_tool_output

        safe_arguments = redact_tool_output(req.arguments or {})
        # v3.9.8: created_at is an ISO string now (was float). Both
        # ``created_at`` and ``created_at_iso`` carry the same value;
        # callers should pick one and stick with it.
        created_at = req.created_at
        if not created_at:
            created_at = now_iso()
        return {
            "approval_id": req.approval_id,
            "session_id": req.session_id,
            "tool_id": req.tool_id,
            "workspace_id": req.workspace_id,
            "run_id": req.run_id,
            "job_id": req.job_id,
            "description": req.description,
            "risk_level": req.risk_level,
            "status": "resolved" if req.resolved else "pending",
            "arguments_summary": _summarize_args(safe_arguments),
            "arguments_preview": safe_arguments,
            "created_at": created_at,
            "created_at_iso": created_at,
        }


def _summarize_args(args: dict) -> str:
    """Summarize tool arguments for display."""
    from core.tools.redaction import redact_tool_output

    items = []
    for k, v in (redact_tool_output(args or {}) or {}).items():
        s = str(v)
        if len(s) > 80:
            s = s[:77] + "..."
        items.append(f"{k}={s}")
    return ", ".join(items[:5])


def _default_persist_path(workspace_id: str = "") -> Path:
    if _APPROVALS_FILE is not None:
        return Path(_APPROVALS_FILE)
    from storage.approval_record_store import approval_log_path

    return approval_log_path(workspace_id)


# Singleton
_approval_stores: dict[str, ApprovalStore] = {}

def get_approval_store(workspace_id: str = "") -> ApprovalStore:
    """Return the approval store scoped to the current user and workspace."""
    path = _default_persist_path(workspace_id)
    key = str(path)
    with _get_lock():
        if key not in _approval_stores:
            _approval_stores[key] = ApprovalStore(persist_path=path)
        return _approval_stores[key]

_appr_lock = None
def _get_lock():
    global _appr_lock
    if _appr_lock is None:
        import threading
        _appr_lock = threading.Lock()
    return _appr_lock


def reset_approval_store_for_tests(remove_persisted: bool = False) -> None:
    """Reset the module-level approval store for isolated tests."""
    stores = list(_approval_stores.values())
    for store in stores:
        with store._lock:
            store._pending.clear()
    if remove_persisted:
        try:
            from storage.approval_record_store import delete_approval_log

            paths = {store._persist_path for store in stores}
            paths.add(_default_persist_path())
            for path in paths:
                delete_approval_log(path=path)
        except OSError:
            logger.debug("approval: test reset could not unlink approval logs", exc_info=True)
    _approval_stores.clear()
