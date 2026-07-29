"""Memory Governance — canonical schema, gate, retrieval, and conflict lifecycle."""

from __future__ import annotations
import json, time as _time, hashlib, logging, re, uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional, Literal
from storage.paths import get_workspace_root
from storage.atomic_io import atomic_write_json
from storage.time_utils import from_iso, now_iso, to_iso
from storage.redaction import contains_secret as storage_contains_secret
from storage.redaction import redact_dict, redact_text


def _ws_root() -> Path:
    return get_workspace_root()

Scope = Literal["global","workspace","session","task"]
MemoryType = Literal[
    "core_rule","semantic_fact","episodic_case","procedural_rule",
    "profile","knowledge_note",
]
MemoryStatus = Literal["pending","active","rejected","expired","conflict"]
MemorySource = Literal[
    "user","tool","file","manual_confirm","agent_suggestion","subagent",
    "llm_tool","task","action","user_signal",
]

_REDACT_KEYS = {
    "password", "passwd", "pwd", "token", "api_key", "apikey",
    "secret", "credential", "authorization", "auth",
}
_VALID_SCOPES = {"global", "workspace", "session", "task"}
SUPPORTED_MEMORY_TYPES = frozenset({
    "core_rule", "semantic_fact", "episodic_case", "procedural_rule",
    "profile", "knowledge_note",
})
_VALID_MEMORY_TYPES = SUPPORTED_MEMORY_TYPES
_MEMORY_ID_RE = re.compile(r"^mem-[a-f0-9]{12}$")

MemoryProjectionHook = Callable[["MemoryRecord"], None]
MemoryDeleteHook = Callable[[str, str], None]
MemoryRankHook = Callable[[str, list[dict], int], list[dict]]
MemoryEventHook = Callable[[str, "MemoryRecord", str], None]

_projection_hook: MemoryProjectionHook | None = None
_delete_hook: MemoryDeleteHook | None = None
_rank_hook: MemoryRankHook | None = None
_event_hook: MemoryEventHook | None = None


def configure_memory_hooks(
    *,
    projection: MemoryProjectionHook | None = None,
    delete_projection: MemoryDeleteHook | None = None,
    rank: MemoryRankHook | None = None,
    event: MemoryEventHook | None = None,
) -> None:
    """Register upper-layer services without making storage import them."""
    global _projection_hook, _delete_hook, _rank_hook, _event_hook
    if projection is not None:
        _projection_hook = projection
    if delete_projection is not None:
        _delete_hook = delete_projection
    if rank is not None:
        _rank_hook = rank
    if event is not None:
        _event_hook = event

def _now(): return now_iso()
def _mid(): return f"mem-{uuid.uuid4().hex[:12]}"

@dataclass
class MemoryRecord:
    memory_id: str = field(default_factory=_mid)
    workspace_id: str = ""
    session_id: str = ""
    task_id: str = ""
    scope: Scope = "workspace"
    memory_type: MemoryType = "knowledge_note"
    status: MemoryStatus = "pending"
    source: MemorySource = "agent_suggestion"
    source_ref: str = ""
    content: str = ""
    summary: str = ""
    confidence: float = 0.5
    ttl_seconds: Optional[int] = None
    expires_at: str = ""
    citations: list = field(default_factory=list)
    conflict_group: str = ""
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_used_at: str = ""
    redacted: bool = True
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        n = _now()
        if not self.created_at: self.created_at = n
        if not self.updated_at: self.updated_at = n
        if self.ttl_seconds and not self.expires_at:
            self.expires_at = to_iso(_time.time() + self.ttl_seconds)

    def is_retrievable(self) -> bool:
        if self.status != "active": return False
        if self.memory_type not in _VALID_MEMORY_TYPES: return False
        if self.expires_at:
            try:
                if from_iso(self.expires_at) < _time.time():
                    return False
            except (TypeError, ValueError):
                return False
        return True

    def to_dict(self) -> dict: return asdict(self)
    @classmethod
    def from_dict(cls, d: dict): return cls(**{k:v for k,v in d.items() if k in cls.__dataclass_fields__})


class MemoryStore:
    """Persist memory records per workspace."""

    def __init__(self):
        pass

    def _validated_ws_id(self, ws_id: str) -> str:
        from storage.ids import validate_workspace_id
        return validate_workspace_id(ws_id)

    def _dir(self, ws_id: str) -> Path:
        return _ws_root() / self._validated_ws_id(ws_id) / "memory"

    def _path(self, ws_id: str, memory_id: str) -> Path:
        memory_id = str(memory_id or "")
        if not _MEMORY_ID_RE.fullmatch(memory_id):
            raise ValueError("invalid_memory_id")
        return self._dir(ws_id) / f"{memory_id}.json"

    def _save(self, record: MemoryRecord):
        """Internal write — gate checks are enforced by MemoryWriteGate."""
        record.workspace_id = self._validated_ws_id(record.workspace_id)
        record.content = _redact(record.content)
        record.summary = _redact(record.summary)
        record.source_ref = _redact(record.source_ref)
        record.citations = _redact_structured(list(record.citations or []))
        record.metadata = _redact_structured(dict(record.metadata or {}))
        d = self._dir(record.workspace_id); d.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._path(record.workspace_id, record.memory_id), record.to_dict())

        if _projection_hook is not None:
            try:
                _projection_hook(record)
            except Exception as e:
                logging.getLogger("memory_governance._save").warning(
                    "memory projection failed for %s: %s",
                    record.memory_id, e,
                )
        else:
            self.default_projection_upsert(record)

    def projection_item(self, record: MemoryRecord) -> dict:
        return {
            "item_type": "memory_hit",
            "item_id": f"mh_{record.memory_id}",
            "workspace_id": record.workspace_id,
            "source": "memory_governance",
            "title": record.summary[:200] if record.summary else record.content[:200],
            "summary": record.summary[:500] if record.summary else record.content[:500],
            "content": record.content[:2000],
            "memory_id": record.memory_id,
            "memory_type": record.memory_type,
            "confidence": record.confidence,
            "scope": record.scope,
            "session_id": record.session_id,
            "task_id": record.task_id,
            "expires_at": record.expires_at,
            "tags": [],
            "status": record.status,
            "memory_status": record.status,
            "confirmation_status": "confirmed",
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "memory_key": str((record.metadata or {}).get("memory_key") or ""),
            "authority": str((record.metadata or {}).get("authority") or ""),
            "authority_rank": int((record.metadata or {}).get("authority_rank") or 0),
        }

    def default_projection_upsert(self, record: MemoryRecord) -> None:
        """Persist a storage-owned projection for environments without hooks."""
        from storage.records import append_jsonl

        item_id = f"mh_{record.memory_id}"
        if record.is_retrievable():
            append_jsonl(record.workspace_id, ("context", "items.jsonl"), self.projection_item(record))
            return
        append_jsonl(record.workspace_id, ("context", "items.jsonl"), {
            "item_id": item_id,
            "deleted": True,
            "deleted_at": _now(),
        })

    def delete_projection(self, ws_id: str, memory_id: str) -> None:
        if _delete_hook is not None:
            try:
                _delete_hook(ws_id, memory_id)
                return
            except Exception as exc:
                logging.getLogger("memory_governance.delete").warning(
                    "memory projection hook delete failed for %s: %s", memory_id, exc,
                )
        try:
            from storage.records import mutate_jsonl

            item_id = f"mh_{memory_id}"

            def remove(rows):
                kept = [row for row in rows if row.get("item_id") != item_id]
                return kept, None

            mutate_jsonl(ws_id, ("context", "items.jsonl"), remove)
        except Exception:
            logging.getLogger("memory_governance._save").warning(
                "memory projection delete failed for %s", memory_id, exc_info=True,
            )

    def delete_file(self, ws_id: str, memory_id: str) -> bool:
        """Physically delete a memory record file."""
        ws_id = self._validated_ws_id(ws_id)
        record = self.get(ws_id, memory_id)
        try:
            p = self._path(ws_id, memory_id)
        except ValueError:
            return False
        if p.exists():
            p.unlink()
            self.delete_projection(ws_id, memory_id)
            return True
        return False

    def get(self, ws_id: str, memory_id: str) -> Optional[MemoryRecord]:
        try:
            p = self._path(ws_id, memory_id)
        except ValueError:
            return None
        if not p.exists(): return None
        try: return MemoryRecord.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception: return None

    def list_all(self, ws_id: str) -> list[MemoryRecord]:
        d = self._dir(ws_id)
        if not d.exists(): return []
        recs = []
        for f in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try: recs.append(MemoryRecord.from_dict(json.loads(f.read_text(encoding="utf-8"))))
            except Exception: continue
        return recs

    def list_by_status(self, ws_id: str, status: MemoryStatus) -> list[MemoryRecord]:
        return [r for r in self.list_all(ws_id) if r.status == status]

    def list_retrievable(self, ws_id: str, scope: Scope = "workspace",
                         session_id: str = "", memory_type: str = "",
                         limit: int = 100) -> list[dict]:
        all_recs = self.list_all(ws_id)
        results = []
        for r in all_recs:
            if not r.is_retrievable(): continue
            if r.scope == "global": pass
            elif r.scope == "workspace" and r.workspace_id != ws_id: continue
            elif r.scope == "session" and r.session_id != session_id: continue
            elif r.scope == "task":
                if not session_id or r.session_id != session_id: continue
            if memory_type and r.memory_type != memory_type: continue
            results.append(r)
            if len(results) >= limit:
                break
        return [r.to_dict() for r in results]

    def search(self, ws_id: str, query: str, limit: int = 10) -> list[dict]:
        """Search all lifecycle records for the memory-management surface."""
        ws_id = self._validated_ws_id(ws_id)
        limit = max(1, min(int(limit), 100))
        records = [record.to_dict() for record in self.list_all(ws_id)]
        if not str(query or "").strip():
            return records[:limit]
        if _rank_hook is not None:
            return _rank_hook(str(query), records, limit)
        return _rank_records(str(query), records, limit)

    def find_conflicts(self, record: MemoryRecord) -> list[MemoryRecord]:
        """Find records with the same structured semantic key."""
        memory_key = str((record.metadata or {}).get("memory_key") or "").strip()
        if not memory_key:
            return []
        existing = self.list_all(record.workspace_id)
        conflicts = []
        for r in existing:
            if r.memory_id == record.memory_id: continue
            if r.scope != record.scope: continue
            if r.memory_type != record.memory_type: continue
            if record.scope == "session" and r.session_id != record.session_id: continue
            if record.scope == "task" and r.task_id != record.task_id: continue
            if r.status not in ("active", "pending"): continue
            existing_key = str((r.metadata or {}).get("memory_key") or "").strip()
            if existing_key == memory_key:
                conflicts.append(r)
        return conflicts


# ── Write Gate ──

class MemoryWriteGate:
    """All memory writes must go through this gate."""

    def __init__(self, store: MemoryStore = None):
        self.store = store or MemoryStore()

    def write(self, candidate: MemoryRecord) -> dict:
        """Apply the single layered memory safety and authority policy."""
        # 1. Workspace required
        if not candidate.workspace_id:
            return {"ok": False, "status": "rejected", "memory_id": "",
                    "rejected": True, "error": "workspace_id is required"}
        try:
            from storage.ids import validate_workspace_id
            candidate.workspace_id = validate_workspace_id(candidate.workspace_id)
        except Exception:
            return {"ok": False, "status": "rejected", "memory_id": candidate.memory_id,
                    "rejected": True, "error": "invalid_workspace_id"}
        if candidate.scope not in _VALID_SCOPES:
            return {"ok": False, "status": "rejected", "memory_id": candidate.memory_id,
                    "rejected": True, "error": "invalid_memory_scope"}
        if candidate.memory_type not in _VALID_MEMORY_TYPES:
            return {"ok": False, "status": "rejected", "memory_id": candidate.memory_id,
                    "rejected": True, "error": "invalid_memory_type"}
        if candidate.scope == "session" and not candidate.session_id:
            return {"ok": False, "status": "rejected", "memory_id": candidate.memory_id,
                    "rejected": True, "error": "session_id_required"}
        if candidate.scope == "task" and not candidate.task_id:
            return {"ok": False, "status": "rejected", "memory_id": candidate.memory_id,
                    "rejected": True, "error": "task_id_required"}

        # 2. Secret rejection on original content before redaction; otherwise
        # redaction can hide the exact pattern from the detector.
        persistable_payload = {
            "content": candidate.content,
            "summary": candidate.summary,
            "source_ref": candidate.source_ref,
            "citations": candidate.citations,
            "metadata": candidate.metadata,
        }
        if _contains_secret_pattern(persistable_payload):
            return {"ok": False, "status": "rejected", "memory_id": candidate.memory_id,
                    "rejected": True, "error": "content contains secret-like patterns, rejected"}
        if _is_low_value_memory(candidate):
            candidate.status = "rejected"
            candidate.redacted = True
            self.store._save(candidate)
            return {"ok": False, "status": "rejected", "memory_id": candidate.memory_id,
                    "rejected": True, "error": "low_value_memory"}

        # 3. Redaction
        candidate.content = _redact(candidate.content)
        candidate.summary = _redact(candidate.summary)

        # Agent and subagent claims are proposals until the selected gate makes
        # an explicit decision. Confidence alone is never proof.
        is_subagent = candidate.created_by == "subagent" or candidate.source == "subagent"
        is_agent_generated = candidate.source in ("agent_suggestion", "subagent") or is_subagent
        if is_agent_generated:
            candidate.status = "pending"

        # Explicit user rules and manual knowledge are authoritative at write time.
        _auto_confirm_types = {"core_rule", "knowledge_note", "profile"}
        _auto_sources = {"user", "manual_confirm"}
        if (
            candidate.status == "pending"
            and candidate.memory_type in _auto_confirm_types
            and candidate.confidence >= 0.5
            and candidate.source in _auto_sources
        ):
            candidate.status = "active"

        warnings: list[dict] = []

        # The task-level consolidator already made the single semantic decision.
        # This gate only validates its cached score and evidence authority; it
        # never makes a second LLM call.
        if is_agent_generated:
            accepted, skipped = _validate_consolidation_decision(candidate)
            if accepted is None:
                candidate.status = "pending"
                warnings.extend(skipped)
            elif not accepted:
                reason = skipped[0].get("reason", "consolidation_rejected") if skipped else "consolidation_rejected"
                candidate.status = "rejected"
                candidate.redacted = True
                self.store._save(candidate)
                return {"ok": False, "status": candidate.status, "memory_id": candidate.memory_id,
                        "rejected": True, "error": reason,
                        "warnings": skipped}
            else:
                score = int(candidate.metadata.get("llm_score", 0) or 0)
                auto_safe_types = {"semantic_fact", "episodic_case", "procedural_rule"}
                authority = str(candidate.metadata.get("authority") or "")
                if (
                    is_subagent
                    or score < 4
                    or candidate.memory_type not in auto_safe_types
                    or authority != "verified_tool"
                ):
                    candidate.status = "pending"
                else:
                    candidate.status = "active"
                warnings.extend(skipped)

        # 8. Conflict detection
        conflicts = self.store.find_conflicts(candidate)
        if conflicts:
            duplicates = [
                existing for existing in conflicts
                if _text_similarity(
                    existing.content or existing.summary,
                    candidate.content or candidate.summary,
                ) >= 0.9
            ]
            if duplicates:
                existing = duplicates[0]
                return {
                    "ok": True,
                    "status": existing.status,
                    "memory_id": existing.memory_id,
                    "rejected": False,
                    "duplicate": True,
                    "duplicate_of": existing.memory_id,
                }
            if candidate.source in {"user", "manual_confirm"} and candidate.memory_type == "core_rule":
                previous = sorted(conflicts, key=lambda item: item.updated_at, reverse=True)[0]
                previous.status = "expired"
                previous.updated_at = _now()
                previous.metadata["superseded_by"] = candidate.memory_id
                self.store._save(previous)
                candidate.metadata["supersedes_memory_id"] = previous.memory_id
                conflicts = []
            active_conflicts = [c for c in conflicts if c.status == "active"]
            if active_conflicts:
                group = f"cg-{uuid.uuid4().hex[:12]}"
                candidate.status = "conflict"
                candidate.conflict_group = group
                candidate.metadata["conflict_memory_ids"] = [c.memory_id for c in active_conflicts]
                for existing in active_conflicts:
                    existing.conflict_group = group
                    existing.updated_at = _now()
                    self.store._save(existing)

        # 9. Persist
        candidate.redacted = True
        self.store._save(candidate)
        result = {"ok": True, "status": candidate.status, "memory_id": candidate.memory_id,
                  "rejected": False, "conflict": candidate.status == "conflict"}
        if warnings:
            result["warnings"] = warnings
        return result


# ── Promotion ──

def confirm_memory(ws_id: str, memory_id: str) -> dict:
    store = MemoryStore()
    rec = store.get(ws_id, memory_id)
    if not rec: return {"ok": False, "error": "not found"}
    if rec.status not in ("pending", "conflict"):
        return {"ok": False, "error": f"cannot confirm status {rec.status}"}
    # Resolve conflicts: expire conflicting active memories in same group
    if rec.conflict_group:
        for r in store.list_all(ws_id):
            if r.conflict_group == rec.conflict_group and r.memory_id != rec.memory_id and r.status == "active":
                r.status = "expired"; store._save(r)
    rec.status = "active"; rec.updated_at = _now()
    store._save(rec)
    _emit_event(ws_id, rec, "memory_confirmed")
    return {"ok": True, "status": "active"}

def reject_memory(ws_id: str, memory_id: str) -> dict:
    store = MemoryStore()
    rec = store.get(ws_id, memory_id)
    if not rec: return {"ok": False, "error": "not found"}
    rec.status = "rejected"; rec.updated_at = _now()
    store._save(rec)
    _emit_event(ws_id, rec, "memory_rejected")
    return {"ok": True, "status": "rejected"}

def expire_memory(ws_id: str, memory_id: str) -> dict:
    store = MemoryStore()
    rec = store.get(ws_id, memory_id)
    if not rec: return {"ok": False, "error": "not found"}
    rec.status = "expired"; rec.updated_at = _now()
    store._save(rec)
    _emit_event(ws_id, rec, "memory_expired")
    return {"ok": True, "status": "expired"}


# ── Helpers ──

def _redact(text: str) -> str:
    return redact_text(str(text or "")).replace("[REDACTED_SECRET]", "[REDACTED]")


def _redact_structured(value: Any) -> Any:
    if isinstance(value, dict):
        return _normalize_redaction_mask(redact_dict(value))
    if isinstance(value, list):
        return [_redact_structured(item) for item in value]
    if isinstance(value, str):
        return _redact(value)
    return value


def _normalize_redaction_mask(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_redaction_mask(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_redaction_mask(item) for item in value]
    if value == "[REDACTED_SECRET]":
        return "[REDACTED]"
    if isinstance(value, str):
        return value.replace("[REDACTED_SECRET]", "[REDACTED]")
    return value

def _obfuscate_kv(text: str, key: str) -> str:
    import re
    return re.sub(rf'({key}\s*[=:]\s*)(\S+)', r'\1[REDACTED]', text, flags=re.I)

def _contains_secret_pattern(data) -> bool:
    return _structured_contains_secret(data)


def _structured_contains_secret(data: Any) -> bool:
    if isinstance(data, dict):
        for key, value in data.items():
            field = str(key).lower().replace("-", "_")
            if field in _REDACT_KEYS or field.endswith((
                "_password", "_passwd", "_pwd", "_token", "_api_key",
                "_secret", "_credential", "_authorization",
            )):
                return True
            if _structured_contains_secret(value):
                return True
        return False
    if isinstance(data, list):
        return any(_structured_contains_secret(item) for item in data)
    return storage_contains_secret(str(data or ""))


def _is_low_value_memory(record: MemoryRecord) -> bool:
    # Check summary and content independently — concatenating them can
    # hide generic content (e.g. "completed" + "completed." → "completed completed.").
    generic_words = {"", "started", "completed", "ok", "true", "false", "success", "failed", "done", "finish", "finished"}
    content = (record.content or "").strip()
    if not content:
        return True
    for text in (record.content, record.summary):
        text = (text or "").strip().lower().rstrip(".。!！?？,，;；")
        if not text:
            continue
        if text in generic_words:
            return True
        # Check after common separators (e.g. "workspace.file: completed" → "completed")
        for sep in (": ", ":", " — ", " - "):
            if sep in text:
                after = text.split(sep, 1)[1].strip()
                if after in generic_words:
                    return True
                after_first = after.split()[0] if after else ""
                if after_first in {"completed", "started", "finished", "success", "failed", "ok", "done", "running", "executed"} and len(after) <= len(after_first) + 8:
                    return True
    full = " ".join([record.summary or "", record.content or ""]).strip().lower()
    if record.memory_type == "episodic_case":
        import re
        generic_completion_patterns = [
            r"task\s+'?[\w\-]+'?\s+completed\s+successfully",
            r"task completed successfully",
            r"result:\s*search completed successfully",
        ]
        if any(re.search(p, full) for p in generic_completion_patterns):
            return True
    if len(full) < 12 and record.source in ("agent_suggestion", "task", "subagent"):
        return True
    return False

def _validate_consolidation_decision(record: MemoryRecord) -> tuple[Optional[bool], list[dict]]:
    """Validate the task consolidator's cached semantic decision."""
    if record.metadata and isinstance(record.metadata, dict):
        cached_score = record.metadata.get("llm_score")
        if cached_score is not None:
            record.metadata["llm_score"] = int(cached_score)
            cached_keep = record.metadata.get("llm_keep", True)
            if cached_keep and int(cached_score) >= 3:
                cached_summary = record.metadata.get("llm_summary", "")
                if cached_summary:
                    record.summary = str(cached_summary)[:200]
                return True, []
            return False, [{"reason": f"llm_score_too_low ({cached_score})"}]
    return None, [{"reason": "consolidation_decision_missing"}]

def _text_similarity(a: str, b: str) -> float:
    def _tokens(text: str) -> set[str]:
        import re
        normalized = (text or "").lower().strip()
        if not normalized:
            return set()
        words = set(re.findall(r"[a-z0-9_./:-]+", normalized))
        cjk = re.findall(r"[\u4e00-\u9fff]", normalized)
        if cjk:
            words.update(cjk)
            words.update("".join(cjk[i:i + 2]) for i in range(len(cjk) - 1))
        if not words and normalized:
            compact = re.sub(r"\s+", "", normalized)
            words.update(compact[i:i + 3] for i in range(max(1, len(compact) - 2)))
        return {w for w in words if w}

    a_words = _tokens(a)
    b_words = _tokens(b)
    if not a_words or not b_words:
        return 0
    overlap = len(a_words & b_words)
    jaccard = overlap / len(a_words | b_words)
    containment = overlap / min(len(a_words), len(b_words))
    return max(jaccard, containment)

def _emit_event(ws_id: str, rec: MemoryRecord, event_type: str):
    if _event_hook is None:
        return
    try:
        _event_hook(ws_id, rec, event_type)
    except Exception:
        logging.getLogger("memory_governance.events").debug(
            "memory lifecycle event append failed", exc_info=True,
        )


def _rank_records(query: str, records: list[dict], limit: int) -> list[dict]:
    terms = _tokens(query)
    if not terms:
        return records[:limit]

    def score(record: dict) -> tuple[int, str]:
        text = " ".join(str(record.get(key, "")) for key in ("summary", "content", "memory_type"))
        record_terms = _tokens(text)
        return (len(terms & record_terms), str(record.get("updated_at") or record.get("created_at") or ""))

    ranked = sorted(records, key=score, reverse=True)
    return [record for record in ranked if score(record)[0] > 0][:limit]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_./:-]+|[\u4e00-\u9fff]", str(text or "").lower()))


def is_auto_memory_enabled(workspace_id: str) -> bool:
    try:
        from storage.workspace_store import get_workspace_state

        return get_workspace_state(workspace_id).get("memory_enabled", True) is not False
    except Exception:
        logging.getLogger("memory_governance.settings").debug(
            "auto memory enabled lookup failed", exc_info=True,
        )
        return True
