"""One-pass, task-level memory reflection and consolidation."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from storage.redaction import redact_text

_log = logging.getLogger(__name__)
_LOCK_GUARD = threading.Lock()
_REFLECTION_LOCKS: dict[tuple[str, str], threading.Lock] = {}

VALID_TYPES = {"core_rule", "semantic_fact", "episodic_case", "procedural_rule"}


def consolidate_experiences(
    *,
    workspace_id: str,
    session_id: str,
    task_id: str,
) -> dict[str, Any]:
    key = (workspace_id, session_id)
    with _LOCK_GUARD:
        lock = _REFLECTION_LOCKS.setdefault(key, threading.Lock())
    with lock:
        return _consolidate_locked(
            workspace_id=workspace_id,
            session_id=session_id,
            task_id=task_id,
        )


def _consolidate_locked(
    *,
    workspace_id: str,
    session_id: str,
    task_id: str,
) -> dict[str, Any]:
    from agent.runtime.memory_write.event_log import mark_experiences_processed, pending_experiences
    from storage.memory_governance import MemoryStore, is_auto_memory_enabled

    if not is_auto_memory_enabled(workspace_id):
        return {"ok": True, "status": "disabled", "processed": 0}
    events = pending_experiences(workspace_id, session_id, limit=12)
    if not events:
        return {"ok": True, "status": "empty", "processed": 0}

    store = MemoryStore()
    query = " ".join(str(row.get("user_input") or "") for row in events)[-2000:]
    existing = [row for row in store.search(workspace_id, query, limit=12) if row.get("status") == "active"]
    proposals = _reflect(events, existing)
    if proposals is None:
        return {"ok": False, "status": "retry_pending", "processed": 0}
    results = [_apply(proposal, workspace_id, session_id, task_id, events, store) for proposal in proposals[:6]]
    mark_experiences_processed(workspace_id, session_id, [str(row.get("event_id") or "") for row in events])
    return {"ok": True, "status": "processed", "processed": len(events), "results": results}


def should_consolidate(events: list[dict[str, Any]]) -> bool:
    """Reflect at a completed operational task or after four accumulated turns."""
    if len(events) >= 4:
        return True
    latest = events[-1] if events else {}
    return bool(latest.get("tool_calls"))


def _reflect(events: list[dict[str, Any]], existing: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    try:
        from agent.llm.runtime import invoke_llm
        from agent.llm.schemas import LLMMessage
        from prompts.loader import render_prompt

        system = render_prompt("memory_consolidation", {}, "").text
        payload = {
            "experiences": [_safe_event(row) for row in events],
            "existing_memories": [_safe_existing(row) for row in existing],
        }
        response = invoke_llm(
            task="memory_consolidation",
            messages=[
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
            ],
            # Reasoning-capable providers account internal reasoning against
            # max_tokens.  A small cap can finish with a closed <think> block
            # but no JSON answer, leaving the durable journal forever pending.
            config_override={"temperature": 0.0, "max_tokens": 6000},
            extra={
                "stream_to_user": False,
                "stream_scope": "internal",
                "request_metadata": {
                    "memory_stage": "task_reflection",
                    "event_count": len(events),
                },
            },
        )
        if response.error:
            _log.warning("memory consolidation failed: %s", response.error)
            return None
        return _parse_operations(response.content or "")
    except Exception:
        _log.warning("memory consolidation error", exc_info=True)
        return None


def _apply(proposal, workspace_id, session_id, task_id, events, store):
    from storage.memory_governance import MemoryRecord, MemoryWriteGate, expire_memory

    action = proposal["action"]
    target = str(proposal.get("target_memory_id") or "")
    if action == "ignore":
        return {"ok": True, "status": "ignored"}
    if action == "expire" and target:
        return expire_memory(workspace_id, target)

    evidence_ids = set(proposal.get("evidence_event_ids") or [])
    evidence_events = [row for row in events if row.get("event_id") in evidence_ids]
    has_verified_tool = any(call.get("ok") for row in evidence_events for call in row.get("tool_calls") or [])
    memory_type = proposal["memory_type"]
    authority = "verified_tool" if has_verified_tool else "agent_inference"
    authority_rank = 70 if has_verified_tool else 30
    status = "active" if has_verified_tool and proposal["score"] >= 4 else "pending"
    record = MemoryRecord(
        workspace_id=workspace_id,
        session_id=session_id,
        task_id=task_id,
        scope=proposal.get("scope", "workspace"),
        memory_type=memory_type,
        status=status,
        source="agent_suggestion",
        source_ref=target,
        content=proposal["content"],
        summary=proposal["summary"],
        confidence=proposal["confidence"],
        citations=[{"event_id": item} for item in evidence_ids],
        created_by="memory_consolidator",
        metadata={
            "memory_key": proposal.get("memory_key"),
            "authority": authority,
            "authority_rank": authority_rank,
            "llm_score": proposal["score"],
            "llm_keep": proposal["score"] >= 3,
            "llm_summary": proposal["summary"],
            "extraction_reason": proposal.get("reason"),
            "evidence_source": "experience_journal",
            "evidence_event_ids": list(evidence_ids),
            "consolidation_origin": "task_reflection",
            "generation_origin": "task_reflection",
            "supersedes_memory_id": target if action == "supersede" else "",
        },
    )
    result = MemoryWriteGate(store).write(record)
    if result.get("ok") and result.get("status") == "active" and action == "supersede" and target:
        old = store.get(workspace_id, target)
        if old and old.status == "active":
            old.status = "expired"
            old.metadata["superseded_by"] = result.get("memory_id")
            store._save(old)
    return result


def _parse_operations(raw: str) -> list[dict[str, Any]] | None:
    from agent.llm.runtime import sanitize_provider_output

    text, _ = sanitize_provider_output(str(raw or ""))
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        text = "\n".join(lines)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    result = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "ignore").lower()
        memory_type = str(item.get("memory_type") or "")
        content = str(item.get("content") or "").strip()[:1200]
        if action not in {"create", "supersede", "expire", "ignore"}:
            continue
        if action not in {"expire", "ignore"} and (memory_type not in VALID_TYPES or not content):
            continue
        try:
            score = max(1, min(int(item.get("score", 1)), 5))
            confidence = max(0.0, min(float(item.get("confidence", 0.5)), 1.0))
        except (TypeError, ValueError):
            continue
        result.append({
            "action": action,
            "target_memory_id": str(item.get("target_memory_id") or "")[:64],
            "memory_type": memory_type,
            "scope": "workspace" if str(item.get("scope") or "workspace") != "global" else "global",
            "memory_key": str(item.get("memory_key") or "")[:160],
            "content": content,
            "summary": str(item.get("summary") or content)[:200],
            "confidence": confidence,
            "score": score,
            "reason": str(item.get("reason") or "")[:200],
            "evidence_event_ids": [str(v)[:64] for v in list(item.get("evidence_event_ids") or [])[:12]],
        })
    return result[:6]


def _safe_event(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": row.get("event_id"),
        "task_id": row.get("task_id"),
        "task_ok": row.get("task_ok"),
        "user_input": _safe_snippet(row.get("user_input"), 1200),
        "assistant_response": _safe_snippet(row.get("assistant_response"), 1800),
        "tool_calls": row.get("tool_calls", [])[:12],
    }


def _safe_existing(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "memory_id": row.get("memory_id"),
        "memory_type": row.get("memory_type"),
        "scope": row.get("scope"),
        "content": _safe_snippet(row.get("content"), 500),
        "summary": _safe_snippet(row.get("summary"), 200),
        "memory_key": (row.get("metadata") or {}).get("memory_key"),
        "authority": (row.get("metadata") or {}).get("authority"),
    }


def _safe_snippet(value: Any, limit: int) -> str:
    return redact_text(str(value or "").replace("\x00", ""))[: max(1, int(limit))]
