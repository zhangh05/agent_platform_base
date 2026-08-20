"""Unified tool approval system — single source of truth for all approvals.

ALL approval flows MUST go through ApprovalStore. There is no secondary
alternative, no dual-store pattern, no bypass.

Key guarantees:
- Every approval is bound to workspace_id + session_id (+ run_id/job_id if present)
- Every approval has a durable expiry and immutable requester identity
- Arguments are redacted by default in persisted records and API responses
- SSE events are published on create/resolve for real-time frontend updates
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent.runtime.utils import from_iso, now_iso

logger = logging.getLogger(__name__)


def _record_approval_metric(status: str, pending_count: int) -> None:
    try:
        from observability.metrics import record_operation, set_operational_gauge
        record_operation("approval", status)
        set_operational_gauge("approval_pending", pending_count)
    except Exception:
        logger.debug("approval metric update failed", exc_info=True)


def _expire_bound_continuation(req: "ApprovalRequest") -> None:
    """Fail closed and release encrypted payloads for expired Agent approvals."""
    continuation_id = str((req.metadata or {}).get("continuation_id") or "")
    if not continuation_id:
        return
    try:
        from agent.runtime.approval_continuation import record_decision

        record_decision(
            workspace_id=req.workspace_id,
            continuation_id=continuation_id,
            approval_id=req.approval_id,
            allowed=False,
        )
    except (FileNotFoundError, RuntimeError, TypeError, ValueError):
        logger.warning(
            "approval: unable to expire continuation approval=%s continuation=%s",
            req.approval_id,
            continuation_id,
            exc_info=True,
        )


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
_DEFAULT_APPROVAL_TTL_SECONDS = 1800
_MIN_APPROVAL_TTL_SECONDS = 60
_MAX_APPROVAL_TTL_SECONDS = 7 * 24 * 60 * 60


def new_approval_id() -> str:
    """Allocate a public approval id before durable aggregate creation."""
    return f"apr_{uuid.uuid4().hex[:12]}"


def approval_ttl_seconds() -> int:
    """Return the single server-authoritative approval lifetime."""
    raw = os.environ.get("LZCORE_APPROVAL_TTL_SECONDS", "").strip()
    try:
        value = int(raw) if raw else _DEFAULT_APPROVAL_TTL_SECONDS
    except ValueError:
        value = _DEFAULT_APPROVAL_TTL_SECONDS
    return max(_MIN_APPROVAL_TTL_SECONDS, min(value, _MAX_APPROVAL_TTL_SECONDS))


def _expires_at_from_created(created_at: str, ttl_seconds: int | None = None) -> str:
    ttl = approval_ttl_seconds() if ttl_seconds is None else ttl_seconds
    created = datetime.fromtimestamp(from_iso(created_at), tz=timezone.utc)
    return (created + timedelta(seconds=ttl)).isoformat()

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
    approval_kind: str = "interactive"
    requester: str = ""
    requester_id: str = ""
    # v3.9.8: created_at / resolved_at are now ISO-8601 strings (UTC),
    # matching every other dataclass in the durable / state / event
    # namespace. Earlier float/epoch split made the API surface
    # inconsistent between /api/approvals and /api/agent/state.
    created_at: str = field(default_factory=_now_iso)
    expires_at: str = ""
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
            latest: dict[str, dict[str, Any]] = {}
            for rec in read_approval_records(path=self._persist_path):
                approval_id = str(rec.get("approval_id") or "")
                if approval_id:
                    latest[approval_id] = rec
            for rec in latest.values():
                # Approval JSONL is an event log. Restore only the latest state
                # for each id or an older pending row can resurrect after its
                # later resolved row is skipped.
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
                    approval_kind=str(rec.get("approval_kind") or "interactive"),
                    requester=str(rec.get("requester") or ""),
                    requester_id=str(rec.get("requester_id") or ""),
                    created_at=created_iso,
                    expires_at=str(rec.get("expires_at") or ""),
                    resolved=False,
                )
                if not req.expires_at:
                    req.expires_at = _expires_at_from_created(req.created_at)
                self._pending[req.approval_id] = req
        except (OSError, ValueError):
            # v3.9.9: file IO / JSON corruption are not unexpected —
            # surface them at WARNING so audit ingest failures are
            # visible instead of silently losing approved actions.
            logger.warning("approval: failed to load history from %s",
                           self._persist_path, exc_info=True)

    @staticmethod
    def _record_for(req: ApprovalRequest) -> dict[str, Any]:
        from core.tools.redaction import redact_tool_output

        return {
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
            "approval_kind": req.approval_kind,
            "requester": req.requester,
            "requester_id": req.requester_id,
            "created_at": req.created_at,
            "expires_at": req.expires_at,
            "resolved": req.resolved,
            "allowed": req.allowed if req.resolved else None,
            "resolved_at": req.resolved_at,
            "resolver": req.resolver,
            "reason": req.reason,
        }

    def _append_record(self, req: ApprovalRequest, *, strict: bool = False) -> None:
        """Append a record (pending or resolved) to the JSONL audit log."""
        try:
            from storage.approval_record_store import append_approval_record
            append_approval_record(self._record_for(req), path=self._persist_path)
        except (OSError, TypeError, ValueError):
            # v3.9.9: ApprovalStore._append_record silently losing
            # every audit row is a real failure — silently skipping
            # a write hides every denied tool invocation. Surface it.
            logger.warning("approval: failed to append record to %s",
                           self._persist_path, exc_info=True)
            if strict:
                raise

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
        except (OSError, ValueError):
            logger.warning("approval: GC history compaction failed for %s",
                           self._persist_path, exc_info=True)

    # ── Public API ──────────────────────────────────────────────────

    def create(self, session_id: str, tool_id: str,
               arguments: dict, description: str = "",
               risk_level: str = "high",
               workspace_id: str = "",
               run_id: str = "",
               job_id: str = "",
               metadata: dict = None,
               approval_kind: str = "interactive",
               requester: str = "",
               requester_id: str = "",
               ttl_seconds: int | None = None) -> ApprovalRequest:
        """Create a pending approval, persist it, and notify subscribers.

        All approval records MUST be bound to workspace_id + session_id.
        Optional run_id/job_id provide traceability when the approval
        originates from a specific agent run or job.
        """
        return self.create_batch([{
            "session_id": session_id,
            "tool_id": tool_id,
            "arguments": arguments,
            "description": description,
            "risk_level": risk_level,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "job_id": job_id,
            "metadata": metadata,
            "approval_kind": approval_kind,
            "requester": requester,
            "requester_id": requester_id,
            "ttl_seconds": ttl_seconds,
        }])[0]

    def create_batch(self, specs: list[dict[str, Any]]) -> list[ApprovalRequest]:
        """Persist and publish a complete approval batch without partial visibility.

        A caller may preallocate each ``approval_id`` so another durable
        aggregate can bind final ids before this transaction commits. The
        batch is written atomically and is only then exposed in memory/SSE.
        """
        if not specs:
            raise ValueError("approval_batch_is_empty")
        requests: list[ApprovalRequest] = []
        seen_ids: set[str] = set()
        for spec in specs:
            workspace_id = str(spec.get("workspace_id") or "")
            if not workspace_id:
                raise ValueError("workspace_id is required")
            try:
                from storage.ids import validate_workspace_id
                workspace_id = validate_workspace_id(workspace_id)
            except Exception as exc:
                raise ValueError("invalid_workspace_id") from exc
            approval_id = str(spec.get("approval_id") or new_approval_id())
            if not re.fullmatch(r"apr_[0-9a-f]{12}", approval_id) or approval_id in seen_ids:
                raise ValueError("invalid_or_duplicate_approval_id")
            seen_ids.add(approval_id)
            requester = str(spec.get("requester") or "")
            requester_id = str(spec.get("requester_id") or "")
            if not requester:
                from storage.principal import current_storage_principal
                requester = current_storage_principal()
            if requester and not requester_id:
                try:
                    from storage.principal import principal_storage_key
                    requester_id = principal_storage_key(requester)
                except (OSError, TypeError, ValueError):
                    requester_id = ""
            raw_ttl = spec.get("ttl_seconds")
            ttl = approval_ttl_seconds() if raw_ttl is None else max(
                _MIN_APPROVAL_TTL_SECONDS,
                min(int(raw_ttl), _MAX_APPROVAL_TTL_SECONDS),
            )
            created_at = now_iso()
            requests.append(ApprovalRequest(
                approval_id=approval_id,
                session_id=str(spec.get("session_id") or ""),
                tool_id=str(spec.get("tool_id") or ""),
                arguments=dict(spec.get("arguments") or {}),
                description=str(spec.get("description") or ""),
                risk_level=str(spec.get("risk_level") or "high"),
                workspace_id=workspace_id,
                run_id=str(spec.get("run_id") or ""),
                job_id=str(spec.get("job_id") or ""),
                metadata=dict(spec.get("metadata") or {}),
                approval_kind=str(spec.get("approval_kind") or "interactive"),
                requester=requester,
                requester_id=requester_id,
                created_at=created_at,
                expires_at=_expires_at_from_created(created_at, ttl),
            ))

        with self._lock:
            if any(req.approval_id in self._pending for req in requests):
                raise RuntimeError("approval_id_already_pending")
        from storage.approval_record_store import append_approval_records
        append_approval_records(
            [self._record_for(req) for req in requests], path=self._persist_path
        )
        with self._lock:
            for req in requests:
                self._pending[req.approval_id] = req
            pending_count = len(self._pending)
        for req in requests:
            _event_bus.publish(ApprovalEvent(
                kind="created", approval_id=req.approval_id,
                session_id=req.session_id, tool_id=req.tool_id,
                workspace_id=req.workspace_id,
                payload={
                    "risk_level": req.risk_level,
                    "description": req.description,
                    "approval_kind": req.approval_kind,
                    "expires_at": req.expires_at,
                },
            ))
            _record_approval_metric("created", pending_count)
        return requests

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
                try:
                    expired = bool(req.expires_at and time.time() >= from_iso(req.expires_at))
                except (TypeError, ValueError):
                    expired = False
                if expired:
                    allowed = False
                    resolver = "system_expired"
                    reason = "approval_ttl_expired"
                resolved_at = now_iso()
                persisted = replace(
                    req,
                    resolved=True,
                    allowed=allowed,
                    resolved_at=resolved_at,
                    resolver=resolver,
                    reason=reason,
                )
                # A decision is not accepted until its audit row is durable.
                # Keeping the in-memory request pending lets the caller retry a
                # transient storage failure instead of losing the approval.
                self._append_record(persisted, strict=True)
                req.resolved = True
                req.allowed = allowed
                req.resolved_at = resolved_at
                req.resolver = resolver
                req.reason = reason
                req._event.set()
                self._pending.pop(approval_id, None)
                pending_count = len(self._pending)
        if req is None:
            return None
        self._gc_history()
        _event_bus.publish(ApprovalEvent(
            kind="resolved", approval_id=approval_id,
            session_id=req.session_id, tool_id=req.tool_id,
            workspace_id=req.workspace_id,
            allowed=allowed, payload={"resolver": resolver, "reason": reason},
        ))
        metric_status = "approved" if allowed else (
            "expired" if resolver == "system_expired" else "rejected"
        )
        if resolver == "system_expired":
            _expire_bound_continuation(req)
        _record_approval_metric(metric_status, pending_count)
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

    def get_pending_request(self, approval_id: str, workspace_id: str) -> Optional[ApprovalRequest]:
        """Return a pending request only when it belongs to the workspace."""
        with self._lock:
            req = self._pending.get(approval_id)
            if req is None or req.resolved or req.workspace_id != workspace_id:
                return None
            return req

    def remaining_seconds(self, approval_id: str) -> float:
        """Return the durable time remaining for a pending approval."""
        with self._lock:
            req = self._pending.get(approval_id)
        if req is None:
            return 0.0
        try:
            return max(0.0, from_iso(req.expires_at) - time.time())
        except (TypeError, ValueError):
            return float(approval_ttl_seconds())

    def get_pending(self, session_id: str = "", workspace_id: str = "") -> list[dict]:
        """Get pending approvals, optionally filtered by workspace/session.

        Expiry is based solely on each record's durable ``expires_at`` value.
        Polling cannot shorten or invent an approval lifetime.
        """
        now = datetime.now(timezone.utc)
        with self._lock:
            expired_ids: list[tuple[str, str]] = []
            for aid, req in self._pending.items():
                try:
                    expires = datetime.fromtimestamp(from_iso(req.expires_at), tz=timezone.utc)
                    expired = bool(req.expires_at and now >= expires)
                except (TypeError, ValueError):
                    expired = False
                if expired:
                    expired_ids.append((aid, req.workspace_id))
        for aid, request_workspace_id in expired_ids:
            self.resolve(
                aid,
                False,
                request_workspace_id,
                resolver="system_expired",
                reason="approval_ttl_expired",
            )

        with self._lock:
            result = []
            for req in self._pending.values():
                if workspace_id and req.workspace_id != workspace_id:
                    continue
                if session_id and req.session_id != session_id:
                    continue
                result.append(self._to_dict(req))
            pending_count = len(self._pending)
        if not expired_ids:
            try:
                from observability.metrics import set_operational_gauge
                set_operational_gauge("approval_pending", pending_count)
            except Exception:
                logger.debug("approval pending gauge update failed", exc_info=True)
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
        return records[:limit]

    def validate_resolved_approval(
        self,
        approval_id: str,
        *,
        workspace_id: str,
        tool_id: str,
        arguments: dict,
        run_id: str = "",
        metadata: dict | None = None,
    ) -> bool:
        """Validate an allowed approval against the exact action binding.

        Approval ids are user-visible references, not bearer capabilities.  A
        caller may only resume an action when the durable resolved record is
        allowed and still matches workspace, run, tool, arguments and the
        caller-supplied binding metadata.
        """
        if not approval_id or not workspace_id or not tool_id:
            return False
        try:
            from core.tools.redaction import redact_tool_output
            from storage.approval_record_store import read_approval_records

            expected_arguments = redact_tool_output(arguments or {})
            expected_metadata = redact_tool_output(metadata or {})
            records = read_approval_records(path=self._persist_path)
        except (OSError, TypeError, ValueError):
            logger.warning(
                "approval validation failed approval_id=%s workspace_id=%s",
                approval_id,
                workspace_id,
                exc_info=True,
            )
            return False

        for record in reversed(records):
            if record.get("approval_id") != approval_id:
                continue
            if not record.get("resolved") or not record.get("allowed"):
                return False
            if record.get("workspace_id") != workspace_id:
                return False
            if record.get("tool_id") != tool_id:
                return False
            if str(record.get("run_id") or "") != str(run_id or ""):
                return False
            stored_arguments = record.get("arguments") or {}
            if not isinstance(stored_arguments, dict) or stored_arguments != expected_arguments:
                return False
            stored_metadata = record.get("metadata") or {}
            return all(stored_metadata.get(key) == value for key, value in expected_metadata.items())
        return False


    def wait(self, approval_id: str, timeout: float | None = None,
             blocking: bool = True) -> Optional[bool]:
        """Wait for approval to be resolved.

        Args:
            approval_id: The approval to wait for.
            timeout: Optional caller wait ceiling. It never resolves or rejects
                     the durable approval by itself.
            blocking: If True, blocks until resolved or timeout. If False,
                      returns immediately: True=allowed, False=denied, None=pending.

        Returns:
            - blocking=True: True/False if resolved, None if the waiter elapsed.
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

        if timeout is None:
            try:
                timeout = max(0.0, from_iso(req.expires_at) - time.time())
            except (TypeError, ValueError):
                timeout = float(approval_ttl_seconds())

        # Blocking mode: poll in 500ms intervals. The caller may stop waiting,
        # but only explicit resolution or durable expiry changes the decision.
        elapsed = 0.0
        while elapsed < timeout:
            if req._event.wait(timeout=0.5):
                return req.allowed
            elapsed += 0.5

        try:
            expired = bool(req.expires_at and time.time() >= from_iso(req.expires_at))
        except (TypeError, ValueError):
            expired = False
        if expired:
            resolved = self.resolve(
                approval_id,
                False,
                workspace_id=req.workspace_id,
                resolver="system_expired",
                reason="approval_ttl_expired",
            )
            return False if resolved is not None else None
        return None

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
            "expires_at": req.expires_at,
            "approval_kind": req.approval_kind,
            "requester": req.requester,
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
