"""Session-scoped generic task state for the SSOT runtime.

This module owns durable task lifecycle facts for ordinary multi-step tasks.
It deliberately does not execute tools, call models, or infer hidden reasoning.
The SSOT runtime is its only writer and projects only server-derived state into
QueryLoop trusted context.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agent.runtime.task_relation_policy import classify_task_relation
from storage.atomic_io import atomic_write_json
from storage.locking import FileLock
from storage.records import append_jsonl, read_jsonl, workspace_record_dir, workspace_record_file

_SCHEMA = "runtime.task_state.v1"
_EVENT_SCHEMA = "runtime.task_event.v1"
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_GENERIC_CONTINUATION_RE = re.compile(
    r"^(?:请)?\s*(?:继续|接着|下一步|然后|继续完成|继续处理|恢复|再查|再验证|再分析|再试)\b",
    re.IGNORECASE,
)
_TASK_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_TASK_RESUMABLE = frozenset({"active", "completed", "replan_required", "waiting_user", "waiting_approval", "interrupted"})
_MAX_CONSECUTIVE_REPLAN_ATTEMPTS = 2


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(workspace_id: str, session_id: str) -> Path:
    _validate_session_id(session_id)
    return workspace_record_file(workspace_id, "sessions", session_id, "task_state.json")


def _event_parts(session_id: str) -> tuple[str, ...]:
    _validate_session_id(session_id)
    return ("sessions", session_id, "task_events.jsonl")


def _validate_session_id(session_id: str) -> str:
    text = str(session_id or "").strip()
    if not _SESSION_ID_RE.fullmatch(text):
        raise ValueError("invalid_task_state_session_id")
    return text


def _read_unlocked(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict) or value.get("schema") != _SCHEMA:
        return {}
    if not isinstance(value.get("task"), dict):
        return {}
    return value


def load_task_state(workspace_id: str, session_id: str) -> dict[str, Any]:
    """Load the latest session task snapshot under its state lock."""
    path = _state_path(workspace_id, session_id)
    with FileLock(path.with_suffix(".lock")):
        return deepcopy(_read_unlocked(path))


def list_task_events(workspace_id: str, session_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Return bounded immutable task lifecycle events for audit and recovery."""
    return read_jsonl(workspace_id, _event_parts(session_id))[-max(1, min(int(limit or 100), 500)) :]


def begin_task_state(
    *,
    workspace_id: str,
    session_id: str,
    run_id: str,
    user_input: str,
    continuation_contract: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Persist a pre-execution checkpoint without running a model or tool.

    The checkpoint records only server-owned lifecycle facts.  Its revision is
    returned to the caller and becomes the only valid terminal CAS base.  A
    process restart can therefore mark an in-flight run interrupted instead of
    guessing whether unfinished tools executed.
    """
    if not str(run_id or "").strip():
        return None
    path = _state_path(workspace_id, session_id)
    with FileLock(path.with_suffix(".lock")):
        previous = _read_unlocked(path)
        previous_task = previous.get("task") if isinstance(previous.get("task"), dict) else None
        revision = int(previous.get("revision") or 0)
        if continuation_contract:
            if (
                not previous_task
                or str(previous_task.get("task_id") or "") != str(continuation_contract.get("task_id") or "")
                or revision != int(continuation_contract.get("base_revision") or 0)
            ):
                return None
            task = deepcopy(previous_task)
            task["relationship"] = dict(continuation_contract.get("relationship") or {})
            task["plan_revision"] = int(task.get("plan_revision") or 0) + 1
            task["active_from_status"] = str(task.get("status") or "")
        else:
            task = _new_task(run_id, user_input)
        now = _now_iso()
        task["source_run_id"] = run_id
        task["last_run_id"] = run_id
        task["status"] = "active"
        task["next_action"] = "run_query_loop"
        task["updated_at"] = now
        next_revision = revision + 1
        task["revision"] = next_revision
        record = {
            "schema": _SCHEMA,
            "workspace_id": workspace_id,
            "session_id": session_id,
            "revision": next_revision,
            "task": task,
            "updated_at": now,
        }
        event = {
            "schema": _EVENT_SCHEMA,
            "event_id": f"evt_{hashlib.sha256(f'{task.get('task_id', '')}:{next_revision}:{run_id}:started'.encode()).hexdigest()[:20]}",
            "event_type": "task_started",
            "task_id": str(task.get("task_id") or ""),
            "revision": next_revision,
            "run_id": run_id,
            "at": now,
            "status": "active",
            "relationship": _relationship_kind(task.get("relationship")),
            "tool_count": 0,
            "successful_tool_count": 0,
            "assertion_status": _assertion_status(task.get("assertions")),
            "next_action": "run_query_loop",
            "run_ok": False,
            "execution_outcome": "in_progress",
        }
        append_jsonl(workspace_id, _event_parts(session_id), event)
        atomic_write_json(path, record)
        return _contract_from_state(record, task, recovery_status=str(task.get("active_from_status") or ""))


def checkpoint_task_state_execution(
    *,
    workspace_id: str,
    session_id: str,
    run_id: str,
    contract: dict[str, Any],
    phase: str,
    manifest: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    """Persist server-generated tool intent/result facts during one active turn."""
    if phase not in {"prepared", "settled"}:
        raise ValueError("invalid_task_state_checkpoint_phase")
    path = _state_path(workspace_id, session_id)
    entries = [dict(item) for item in manifest if isinstance(item, dict)]
    with FileLock(path.with_suffix(".lock")):
        current = _read_unlocked(path)
        task = current.get("task") if isinstance(current.get("task"), dict) else None
        if (
            not task
            or str(task.get("task_id") or "") != str(contract.get("task_id") or "")
            or int(current.get("revision") or 0) != int(contract.get("base_revision") or 0)
            or str(task.get("status") or "") != "active"
            or str(task.get("source_run_id") or "") != str(run_id or "")
        ):
            return None
        task = deepcopy(task)
        pending = _bounded_key_list(task.get("pending_mutation_keys"))
        if phase == "prepared":
            for item in entries:
                if bool(item.get("side_effecting")):
                    key = _bounded_text(item.get("call_key") or "", 640)
                    if key:
                        pending.append(key)
        else:
            settled = { _bounded_text(item.get("call_key") or "", 640) for item in entries }
            pending = [key for key in pending if key not in settled]
            task["completed_mutation_keys"] = _merge_completed_mutation_keys(
                _as_list(task.get("completed_mutation_keys")), entries
            )
            task["failed_replan_call_keys"] = _merge_failed_replan_call_keys(
                _as_list(task.get("failed_replan_call_keys")), entries
            )
        task["pending_mutation_keys"] = list(dict.fromkeys(pending))[-96:]
        revision = int(current.get("revision") or 0) + 1
        now = _now_iso()
        task["revision"] = revision
        task["updated_at"] = now
        record = {
            "schema": _SCHEMA, "workspace_id": workspace_id, "session_id": session_id,
            "revision": revision, "task": task, "updated_at": now,
        }
        event = {
            "schema": _EVENT_SCHEMA,
            "event_id": f"evt_{hashlib.sha256(f'{task.get('task_id', '')}:{revision}:{run_id}:{phase}'.encode()).hexdigest()[:20]}",
            "event_type": "task_tool_prepared" if phase == "prepared" else "task_tool_settled",
            "task_id": str(task.get("task_id") or ""), "revision": revision, "run_id": run_id,
            "at": now, "status": "active", "relationship": _relationship_kind(task.get("relationship")),
            "tool_count": len(entries), "successful_tool_count": sum(1 for item in entries if bool(item.get("ok"))),
            "assertion_status": _assertion_status(task.get("assertions")), "next_action": "run_query_loop",
            "run_ok": False, "execution_outcome": "in_progress",
        }
        append_jsonl(workspace_id, _event_parts(session_id), event)
        atomic_write_json(path, record)
        return _contract_from_state(record, task, recovery_status=str(task.get("active_from_status") or ""))


def _bounded_key_list(value: Any) -> list[str]:
    return list(dict.fromkeys(
        _bounded_text(item, 640) for item in _as_list(value)
        if isinstance(item, str) and _bounded_text(item, 640)
    ))[-96:]


def reconcile_active_task_states(workspace_id: str) -> dict[str, int]:
    """Mark only durable in-flight TaskStates interrupted after service start.

    This function never dispatches a model or tool.  Explicit user continuation
    must re-enter AgentApp and QueryLoop, where the normal policy, approval and
    TaskState CAS fences remain authoritative.
    """
    sessions_dir = workspace_record_dir(workspace_id, "sessions", create=False)
    if not sessions_dir.is_dir():
        return {"interrupted": 0, "skipped": 0}
    interrupted = 0
    skipped = 0
    for child in sessions_dir.iterdir():
        if not child.is_dir():
            continue
        session_id = child.name
        try:
            _validate_session_id(session_id)
        except ValueError:
            skipped += 1
            continue
        path = _state_path(workspace_id, session_id)
        with FileLock(path.with_suffix(".lock")):
            state = _read_unlocked(path)
            task = state.get("task") if isinstance(state.get("task"), dict) else None
            if not task or str(task.get("status") or "") != "active":
                continue
            now = _now_iso()
            revision = int(state.get("revision") or 0) + 1
            task = deepcopy(task)
            pending_mutations = _bounded_key_list(task.get("pending_mutation_keys"))
            task["status"] = "waiting_user" if pending_mutations else "interrupted"
            task["next_action"] = (
                "resolve_unknown_mutation_outcome" if pending_mutations
                else "resume_after_service_restart"
            )
            task["interrupted_reason"] = "service_restart"
            task["updated_at"] = now
            task["revision"] = revision
            record = {
                "schema": _SCHEMA,
                "workspace_id": workspace_id,
                "session_id": session_id,
                "revision": revision,
                "task": task,
                "updated_at": now,
            }
            event = {
                "schema": _EVENT_SCHEMA,
                "event_id": f"evt_{hashlib.sha256(f'{task.get('task_id', '')}:{revision}:{task.get('source_run_id', '')}:interrupted'.encode()).hexdigest()[:20]}",
                "event_type": "task_interrupted",
                "task_id": str(task.get("task_id") or ""),
                "revision": revision,
                "run_id": str(task.get("source_run_id") or ""),
                "at": now,
                "status": str(task.get("status") or "interrupted"),
                "relationship": _relationship_kind(task.get("relationship")),
                "tool_count": 0,
                "successful_tool_count": 0,
                "assertion_status": _assertion_status(task.get("assertions")),
                "next_action": str(task.get("next_action") or "resume_after_service_restart"),
                "run_ok": False,
                "execution_outcome": "unknown" if pending_mutations else "interrupted",
            }
            append_jsonl(workspace_id, _event_parts(session_id), event)
            atomic_write_json(path, record)
            interrupted += 1
    return {"interrupted": interrupted, "skipped": skipped}


def resolve_task_state(
    *,
    workspace_id: str,
    session_id: str,
    user_input: str,
    messages: Iterable[dict[str, Any]],
    approval_parent_run_id: str = "",
) -> dict[str, Any] | None:
    """Resolve a resumable generic task using a recent complete exchange guard.

    This accepts only an explicit server-classified relationship or a bounded
    generic continuation command. A different/new topic never inherits state.
    """
    state = load_task_state(workspace_id, session_id)
    task = state.get("task") if isinstance(state.get("task"), dict) else None
    if not task or str(task.get("status") or "") not in _TASK_RESUMABLE:
        return None
    approval_parent_run_id = str(approval_parent_run_id or "").strip()
    is_approval_resume = (
        str(task.get("status") or "") == "waiting_approval"
        and bool(approval_parent_run_id)
        and approval_parent_run_id == str(task.get("source_run_id") or "")
    )
    relation = {"kind": "approval_resume"} if is_approval_resume else _continuation_relation(user_input)
    if relation is None:
        return None
    if not is_approval_resume:
        latest_user, latest_assistant = _latest_complete_exchange(messages)
        if str(task.get("status") or "") == "interrupted":
            # An in-flight checkpoint may legitimately have no assistant half.
            # Resume only when the last durable user request is exactly the
            # interrupted run, never from an arbitrary new topic.
            last_user = next(
                (
                    item for item in reversed(list(messages))
                    if isinstance(item, dict) and str(item.get("role") or "") == "user"
                ),
                None,
            )
            if not isinstance(last_user, dict) or str(last_user.get("run_id") or "") != str(task.get("source_run_id") or ""):
                return None
        else:
            if not latest_user or not latest_assistant:
                return None
            if str(latest_assistant.get("run_id") or "") != str(task.get("source_run_id") or ""):
                return None
    return _contract_from_state(state, task, relationship=relation)


def _contract_from_state(
    state: dict[str, Any],
    task: dict[str, Any],
    *,
    recovery_status: str = "",
    relationship: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": _SCHEMA,
        "task_id": str(task.get("task_id") or ""),
        "base_revision": int(state.get("revision") or 0),
        "relationship": dict(relationship if isinstance(relationship, dict) else (task.get("relationship") or {})),
        "status": str(task.get("status") or ""),
        "recovery_status": str(recovery_status or task.get("active_from_status") or ""),
        "plan_revision": int(task.get("plan_revision") or 0),
        "replan_attempts": int(task.get("replan_attempts") or 0),
        "evidence_count": len(_as_list(task.get("evidence_refs"))),
        "unknown_count": len(_as_list(task.get("unknowns"))),
        "assertion_status": _assertion_status(task.get("assertions")),
        "next_action": str(task.get("next_action") or ""),
        "source_run_id": str(task.get("source_run_id") or ""),
        "failure": _contract_failure(task.get("failure")),
        "completed_mutation_keys": _completed_mutation_keys(task),
        "failed_replan_call_keys": _failed_replan_call_keys(task),
        "pending_mutation_keys": _bounded_key_list(task.get("pending_mutation_keys")),
    }


def _new_task(run_id: str, user_input: str) -> dict[str, Any]:
    now = _now_iso()
    return {
        "task_id": _task_id(run_id, user_input),
        "objective": _bounded_text(user_input, 1200),
        "constraints": [],
        "relationship": {"kind": "initial"},
        "plan_revision": 1,
        "replan_attempts": 0,
        "nodes": [],
        "evidence_refs": [],
        "unknowns": [],
        "assertions": {},
        "failure": {},
        "created_at": now,
    }


def render_task_state_guidance(contract: dict[str, Any]) -> str:
    """Render only mechanical server-owned continuation facts for QueryLoop."""
    lines = [
        "Server-derived generic task state. It authorizes no tool and contains no historic user prose or model reasoning.",
        f"task_id={contract.get('task_id', '')}",
        f"base_revision={int(contract.get('base_revision') or 0)}",
        f"relationship={_relationship_kind(contract.get('relationship'))}",
        f"prior_status={contract.get('status', '')}",
        f"recovery_status={contract.get('recovery_status', '')}",
        f"plan_revision={int(contract.get('plan_revision') or 0)}",
        f"replan_attempts={int(contract.get('replan_attempts') or 0)}",
        f"evidence_count={int(contract.get('evidence_count') or 0)}",
        f"unknown_count={int(contract.get('unknown_count') or 0)}",
        f"assertion_status={contract.get('assertion_status', '')}",
    ]
    next_action = str(contract.get("next_action") or "").strip()
    if next_action:
        lines.append(f"next_action={next_action[:180]}")
    failure = dict(contract.get("failure") or {})
    if failure:
        lines.append(
            "failure=" + _bounded_text(
                f"{failure.get('classification') or 'runtime_failure'}; failed_nodes={int(failure.get('failed_node_count') or 0)}",
                220,
            )
        )
    completed_mutations = _as_list(contract.get("completed_mutation_keys"))
    if completed_mutations:
        lines.append(f"completed_side_effect_count={len(completed_mutations)}")
        lines.append("Completed side-effecting calls are execution-fenced by the server; do not propose an identical replay.")
    pending_mutations = _as_list(contract.get("pending_mutation_keys"))
    if pending_mutations:
        lines.append(f"unknown_mutation_outcome_count={len(pending_mutations)}")
        lines.append("A prior side-effecting call may have been interrupted; do not propose any mutation until its outcome is independently verified.")
    failed_replan_keys = _as_list(contract.get("failed_replan_call_keys"))
    if failed_replan_keys:
        lines.append(f"failed_replan_step_count={len(failed_replan_keys)}")
        lines.append("The server rejects an identical replay of a prior failed replan step; select a materially different, policy-valid recovery step.")
    prior_status = str(contract.get("status") or "")
    recovery_status = str(contract.get("recovery_status") or "")
    if prior_status == "replan_required" or recovery_status == "replan_required":
        lines.append("Replan from the recorded failure and verified evidence. Select a different, policy-valid observation or recovery step; do not merely retry the same failed plan.")
    elif prior_status == "waiting_approval":
        lines.append("This is an approved continuation. Continue only through the server-issued approval grant and preserve prior task evidence.")
    elif _relationship_kind(contract.get("relationship")) in {"resume", "approval_resume"}:
        lines.append("Resume the existing task. Preserve verified evidence and do not repeat completed side-effecting actions.")
    return "\n".join(lines)


def commit_task_state(
    *,
    workspace_id: str,
    session_id: str,
    run_id: str,
    user_input: str,
    final_response: str,
    run_ok: bool,
    runtime_metadata: dict[str, Any] | None,
    tool_calls: Iterable[dict[str, Any]] | None,
    continuation_contract: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Commit one canonical QueryLoop terminal projection using revision CAS.

    The function accepts only facts already produced by QueryLoop. It never
    invokes a model or a tool, so state evolution cannot form an execution path.
    """
    if not str(run_id or "").strip():
        return None
    path = _state_path(workspace_id, session_id)
    metadata = dict(runtime_metadata or {})
    calls = [dict(item) for item in (tool_calls or []) if isinstance(item, dict)]
    with FileLock(path.with_suffix(".lock")):
        previous = _read_unlocked(path)
        previous_task = previous.get("task") if isinstance(previous.get("task"), dict) else None
        revision = int(previous.get("revision") or 0)
        if continuation_contract:
            if (
                not previous_task
                or str(previous_task.get("task_id") or "") != str(continuation_contract.get("task_id") or "")
                or revision != int(continuation_contract.get("base_revision") or 0)
            ):
                return None
        task = _evolve_task(
            previous_task=previous_task,
            user_input=user_input,
            run_id=run_id,
            run_ok=bool(run_ok),
            metadata=metadata,
            tool_calls=calls,
            continuation_contract=continuation_contract,
        )
        if not task:
            return None
        next_revision = revision + 1
        task["revision"] = next_revision
        task["updated_at"] = _now_iso()
        event = _event_from_transition(
            task=task,
            run_id=run_id,
            run_ok=bool(run_ok),
            metadata=metadata,
            tool_calls=calls,
            continuation_contract=continuation_contract,
            revision=next_revision,
        )
        record = {
            "schema": _SCHEMA,
            "workspace_id": workspace_id,
            "session_id": session_id,
            "revision": next_revision,
            "task": task,
            "updated_at": task["updated_at"],
        }
        # Append the immutable fact before publishing its matching snapshot.
        append_jsonl(workspace_id, _event_parts(session_id), event)
        atomic_write_json(path, record)
        return deepcopy(record)


def _evolve_task(
    *,
    previous_task: dict[str, Any] | None,
    user_input: str,
    run_id: str,
    run_ok: bool,
    metadata: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    continuation_contract: dict[str, Any] | None,
) -> dict[str, Any]:
    now = _now_iso()
    if continuation_contract and previous_task:
        task = deepcopy(previous_task)
        relationship = dict(continuation_contract.get("relationship") or {})
        task["relationship"] = relationship
        task["plan_revision"] = int(task.get("plan_revision") or 0) + 1
    else:
        task = _new_task(run_id, user_input)
    task["source_run_id"] = run_id
    task["last_run_id"] = run_id
    task["nodes"] = _merge_nodes(_as_list(task.get("nodes")), tool_calls)
    task["completed_mutation_keys"] = _merge_completed_mutation_keys(
        _as_list(task.get("completed_mutation_keys")),
        metadata.get("task_state_execution_manifest"),
    )
    task["failed_replan_call_keys"] = _merge_failed_replan_call_keys(
        _as_list(task.get("failed_replan_call_keys")),
        metadata.get("task_state_execution_manifest"),
    )
    task["evidence_refs"] = _merge_evidence(_as_list(task.get("evidence_refs")), metadata.get("evidence"), tool_calls)
    task["unknowns"] = _unknowns_from_metadata(metadata)
    task["assertions"] = _assertions_from_metadata(metadata)
    task["failure"] = _failure_from_metadata(metadata, run_ok, tool_calls)
    if _replan_requested(task, metadata):
        prior_attempts = int(previous_task.get("replan_attempts") or 0) if previous_task else 0
        prior_replan = (
            str(previous_task.get("status") or "") == "replan_required"
            or str(previous_task.get("active_from_status") or "") == "replan_required"
        ) if previous_task else False
        task["replan_attempts"] = prior_attempts + 1 if prior_replan else 1
    elif not previous_task or str(previous_task.get("status") or "") != "replan_required":
        task["replan_attempts"] = 0
    task["status"], task["next_action"] = _derive_status(task, metadata, run_ok)
    task.pop("active_from_status", None)
    return task


def _task_id(run_id: str, user_input: str) -> str:
    digest = hashlib.sha256(f"{run_id}\n{user_input}".encode("utf-8")).hexdigest()[:20]
    return f"tsk_{digest}"


def _merge_nodes(existing: list[dict[str, Any]], tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes = [dict(item) for item in existing if isinstance(item, dict)][-96:]
    seen = {str(item.get("node_id") or "") for item in nodes}
    for index, call in enumerate(tool_calls):
        call_id = str(call.get("call_id") or call.get("id") or "")
        tool_id = str(call.get("tool_id") or call.get("tool") or call.get("name") or "tool")
        key = call_id or hashlib.sha256(f"{tool_id}:{index}:{json.dumps(call.get('arguments') or {}, sort_keys=True, default=str)}".encode()).hexdigest()[:16]
        node_id = f"tool:{key}"
        node = {
            "node_id": node_id,
            "kind": "tool",
            "tool_id": tool_id[:160],
            "call_id": call_id[:160],
            "status": "succeeded" if bool(call.get("ok")) else "failed",
            "side_effecting": bool(call.get("side_effecting") or call.get("mutation")),
            "result_ref": _bounded_text(call.get("result_ref") or call.get("summary") or call.get("error") or "", 240),
        }
        if node_id in seen:
            for position, current in enumerate(nodes):
                if current.get("node_id") == node_id:
                    nodes[position] = node
                    break
        else:
            nodes.append(node)
            seen.add(node_id)
    return nodes[-128:]


def _merge_evidence(existing: list[dict[str, Any]], evidence: Any, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = [dict(item) for item in existing if isinstance(item, dict)][-96:]
    seen = {json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) for item in merged}
    candidates: list[dict[str, Any]] = []
    if isinstance(evidence, dict):
        for item in _as_list(evidence.get("items") or evidence.get("evidence")):
            if isinstance(item, dict):
                candidates.append({
                    "evidence_id": _bounded_text(item.get("evidence_id") or item.get("id") or "", 160),
                    "kind": _bounded_text(item.get("kind") or "tool", 80),
                    "source": _bounded_text(item.get("source") or item.get("tool") or "", 160),
                })
    for call in tool_calls:
        if not bool(call.get("ok")):
            continue
        source = _bounded_text(call.get("tool_id") or call.get("tool") or call.get("name") or "tool", 160)
        result_ref = _bounded_text(call.get("result_ref") or call.get("summary") or "", 240)
        candidates.append({"evidence_id": _bounded_text(call.get("call_id") or call.get("id") or "", 160), "kind": "tool_result", "source": source, "result_ref": result_ref})
    for item in candidates:
        fingerprint = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if fingerprint not in seen:
            merged.append(item)
            seen.add(fingerprint)
    return merged[-128:]


def _unknowns_from_metadata(metadata: dict[str, Any]) -> list[dict[str, str]]:
    unknown = metadata.get("unknown_outcome")
    if isinstance(unknown, dict) and unknown:
        return [{"kind": _bounded_text(unknown.get("kind") or "unknown_outcome", 80), "reason": _bounded_text(unknown.get("reason") or unknown.get("message") or "", 240)}]
    cognitive = metadata.get("cognitive")
    if isinstance(cognitive, dict) and int(cognitive.get("blocking_unknown_count") or 0) > 0:
        return [{"kind": "blocking_unknown", "reason": "runtime_reported_blocking_unknown"}]
    return []


def _assertions_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    assertions = metadata.get("goal_assertions")
    if isinstance(assertions, dict):
        return {
            "required": bool(assertions.get("required")),
            "status": _bounded_text(assertions.get("status") or "not_required", 80),
            "failed": [_bounded_text(item, 160) for item in _as_list(assertions.get("failed") or assertions.get("issues"))[:12]],
        }
    return {"required": False, "status": "not_required", "failed": []}


def _failure_from_metadata(metadata: dict[str, Any], run_ok: bool, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [call for call in tool_calls if not bool(call.get("ok"))]
    execution = _bounded_text(metadata.get("execution_outcome") or "", 80)
    if run_ok and not failed and execution not in {"failed", "partial", "unknown"}:
        return {}
    return {
        "classification": "tool_failure" if failed else (execution or "runtime_failure"),
        "failed_node_count": len(failed),
        "retryable": bool(failed) and not bool(metadata.get("unknown_outcome")),
    }


def _derive_status(task: dict[str, Any], metadata: dict[str, Any], run_ok: bool) -> tuple[str, str]:
    assertions = dict(task.get("assertions") or {})
    failure = dict(task.get("failure") or {})
    unknowns = _as_list(task.get("unknowns"))
    cognitive = metadata.get("cognitive") if isinstance(metadata.get("cognitive"), dict) else {}
    decision = str(cognitive.get("outcome") or "")
    runtime_errors = [str(item).strip().lower() for item in _as_list(metadata.get("runtime_errors"))]
    if "cancelled_by_user" in runtime_errors:
        return "cancelled", "cancelled_by_user"
    if _bounded_key_list(task.get("pending_mutation_keys")):
        return "waiting_user", "resolve_unknown_mutation_outcome"
    if bool(metadata.get("approval_required")):
        return "waiting_approval", "resume_after_approval"
    if str(metadata.get("execution_outcome") or "") == "unknown" or unknowns:
        return "waiting_user", "resolve_blocking_unknown"
    if _replan_requested(task, metadata):
        if int(task.get("replan_attempts") or 0) >= _MAX_CONSECUTIVE_REPLAN_ATTEMPTS:
            return "failed", "replan_budget_exhausted"
        return "replan_required", "propose_alternative_plan"
    if not run_ok:
        return "failed", "task_failed"
    if bool(assertions.get("required")) and str(assertions.get("status") or "") != "passed":
        return "replan_required", "satisfy_goal_assertions"
    return "completed", "await_user_or_continuation"


def _event_from_transition(
    *,
    task: dict[str, Any],
    run_id: str,
    run_ok: bool,
    metadata: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    continuation_contract: dict[str, Any] | None,
    revision: int,
) -> dict[str, Any]:
    status = str(task.get("status") or "")
    event_type = {
        "completed": "task_completed",
        "replan_required": "replan_required",
        "waiting_user": "task_waiting_user",
        "waiting_approval": "task_waiting_approval",
        "failed": "task_failed",
        "cancelled": "task_cancelled",
        "interrupted": "task_interrupted",
    }.get(status, "task_updated")
    return {
        "schema": _EVENT_SCHEMA,
        "event_id": f"evt_{hashlib.sha256(f'{task.get("task_id", "")}:{revision}:{run_id}'.encode()).hexdigest()[:20]}",
        "event_type": event_type,
        "task_id": str(task.get("task_id") or ""),
        "revision": revision,
        "run_id": run_id,
        "at": _now_iso(),
        "status": status,
        "relationship": _relationship_kind((continuation_contract or {}).get("relationship") or task.get("relationship")),
        "tool_count": len(tool_calls),
        "successful_tool_count": sum(1 for call in tool_calls if bool(call.get("ok"))),
        "assertion_status": _assertion_status(task.get("assertions")),
        "next_action": str(task.get("next_action") or ""),
        "run_ok": bool(run_ok),
        "execution_outcome": _bounded_text(metadata.get("execution_outcome") or "", 80),
    }


def _replan_requested(task: dict[str, Any], metadata: dict[str, Any]) -> bool:
    failure = dict(task.get("failure") or {})
    cognitive = metadata.get("cognitive") if isinstance(metadata.get("cognitive"), dict) else {}
    return str(cognitive.get("outcome") or "") == "continue_replan" or bool(
        failure and failure.get("retryable")
    )


def _contract_failure(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "classification": _bounded_text(value.get("classification") or "", 80),
        "failed_node_count": max(0, int(value.get("failed_node_count") or 0)),
        "retryable": bool(value.get("retryable")),
    }


def _completed_mutation_keys(task: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(
        _bounded_text(item, 640)
        for item in _as_list(task.get("completed_mutation_keys"))
        if isinstance(item, str) and _bounded_text(item, 640)
    ))[-96:]


def _merge_completed_mutation_keys(existing: list[Any], manifest: Any) -> list[str]:
    keys = [
        _bounded_text(item, 640)
        for item in existing
        if isinstance(item, str) and _bounded_text(item, 640)
    ]
    if isinstance(manifest, list):
        for item in manifest:
            if not isinstance(item, dict):
                continue
            if not bool(item.get("ok")) or not bool(item.get("side_effecting")):
                continue
            key = _bounded_text(item.get("call_key") or "", 640)
            if key:
                keys.append(key)
    return list(dict.fromkeys(keys))[-96:]


def _failed_replan_call_keys(task: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(
        _bounded_text(item, 640)
        for item in _as_list(task.get("failed_replan_call_keys"))
        if isinstance(item, str) and _bounded_text(item, 640)
    ))[-96:]


def _merge_failed_replan_call_keys(existing: list[Any], manifest: Any) -> list[str]:
    """Persist exact failed call identities for the next replan gate.

    The list is server-generated from the QueryLoop execution manifest, never
    from request metadata.  It forbids blind replay; a different call remains
    eligible for normal policy validation and execution.
    """
    keys = [
        _bounded_text(item, 640)
        for item in existing
        if isinstance(item, str) and _bounded_text(item, 640)
    ]
    if isinstance(manifest, list):
        for item in manifest:
            if not isinstance(item, dict) or bool(item.get("ok")):
                continue
            key = _bounded_text(item.get("call_key") or "", 640)
            if key:
                keys.append(key)
    return list(dict.fromkeys(keys))[-96:]


def _continuation_relation(user_input: str) -> dict[str, Any] | None:
    relation = classify_task_relation(str(user_input or ""))
    if isinstance(relation, dict):
        return dict(relation)
    if _GENERIC_CONTINUATION_RE.search(str(user_input or "").strip()):
        return {"kind": "resume"}
    return None


def _latest_complete_exchange(messages: Iterable[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    ordered = [dict(item) for item in messages if isinstance(item, dict)]
    if len(ordered) < 2:
        return None, None
    for index in range(len(ordered) - 1, 0, -1):
        assistant = ordered[index]
        user = ordered[index - 1]
        if str(user.get("role") or "") == "user" and str(assistant.get("role") or "") == "assistant":
            return user, assistant
    return None, None


def _relationship_kind(value: Any) -> str:
    return str((value or {}).get("kind") or "initial") if isinstance(value, dict) else "initial"


def _assertion_status(value: Any) -> str:
    return _bounded_text((value or {}).get("status") or "not_required", 80) if isinstance(value, dict) else "not_required"


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]
