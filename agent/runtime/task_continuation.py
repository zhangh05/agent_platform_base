"""Session-scoped structured task continuation state.

This module is the sole owner of task-continuation state.  Conversation messages
remain the durable transcript; this record stores only the bounded, server-derived
contract needed to safely continue, refine, or expand the active deliverable.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from storage.atomic_io import atomic_write_json
from storage.locking import FileLock
from storage.records import workspace_record_file
from storage.redaction import redact_text
from agent.runtime.task_relation_policy import classify_task_relation, render_task_relation_guidance
from core.runtime_engine.enumerated_items import extract_enumerated_items

_SCHEMA = "runtime.task_continuation.v1"
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_ADDITIONAL_RE = re.compile(
    r"^(?:再来|再给|再生成|再写|再列|再补)\s*(?:(?P<count>\d+)\s*)?(?P<unit>条|个|项|份|段|组)?[。.!！?？\s]*$"
)
_DETAIL_RE = re.compile(r"^(?:继续|接着|展开|详细点|再详细|再说说)[。.!！?？\s]*$")
_REFINE_RE = re.compile(r"^(?:改成|改为|不要|只要|换成|调整为).{1,240}$")
_COUNT_RE = re.compile(r"(?<!\d)(?P<count>\d{1,3})\s*(?P<unit>条|个|项|份|段|组)")
_PREFIX_RE = re.compile(r"(?:以|用|使用)\s*[“\"']?(?P<prefix>[A-Za-z][A-Za-z0-9_-]{0,15}-)[”\"']?\s*开头")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(workspace_id: str, session_id: str) -> Path:
    if not _SESSION_ID_RE.fullmatch(str(session_id or "")):
        raise ValueError("invalid_task_continuation_session_id")
    return workspace_record_file(workspace_id, "sessions", session_id, "task_continuation.json")


def _read_unlocked(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict) or value.get("schema") != _SCHEMA:
        return {}
    active_task = value.get("active_task")
    if isinstance(active_task, dict) and not _persisted_delivery_is_user_requested(active_task):
        return {}
    return value


def _persisted_delivery_is_user_requested(active_task: dict[str, Any]) -> bool:
    """Reject legacy contracts inferred only from incidental model numbering."""
    delivery = active_task.get("delivery_contract")
    if not isinstance(delivery, dict):
        return False
    if delivery.get("requested_count") is not None or str(delivery.get("prefix") or ""):
        return True
    request = str(active_task.get("goal") or "")
    return any(marker in request for marker in ("编号", "序号", "每条", "逐条"))


def load_task_continuation(workspace_id: str, session_id: str) -> dict[str, Any]:
    path = _path(workspace_id, session_id)
    with FileLock(path.with_suffix(".lock")):
        return dict(_read_unlocked(path))

def _parse_relation(user_input: str) -> dict[str, Any] | None:
    return classify_task_relation(user_input)
    return None


def _extract_items(text: str) -> list[dict[str, Any]]:
    return [
        {"prefix": item.prefix, "ordinal": item.ordinal}
        for item in extract_enumerated_items(text)
    ]

def _delivery_contract(user_input: str, assistant_response: str) -> dict[str, Any] | None:
    request = str(user_input or "")
    items = _extract_items(assistant_response)
    count_match = _COUNT_RE.search(request)
    expected_count = int(count_match.group("count")) if count_match else None
    unit = count_match.group("unit") if count_match else ""
    requested_prefix = ""
    prefix_match = _PREFIX_RE.search(request)
    if prefix_match:
        requested_prefix = prefix_match.group("prefix")
    if not requested_prefix and items:
        requested_prefix = str(items[0]["prefix"] or "")
    # The contract belongs to the user's requested delivery shape. A model may
    # choose to number ordinary prose or offer several next-step choices; those
    # incidental numbers must never create or replace durable task state.
    requires_numbering = expected_count is not None or bool(requested_prefix) or any(
        marker in request for marker in ("编号", "序号", "每条", "逐条")
    )
    if not requires_numbering and expected_count is None:
        return None
    ordinals = [int(item["ordinal"]) for item in items]
    return {
        "kind": "enumerated_items" if requires_numbering else "bounded_output",
        "unit": unit or "条",
        "requested_count": expected_count,
        "numbered": requires_numbering,
        "prefix": requested_prefix,
        "produced_count": len(items),
        "last_ordinal": max(ordinals) if ordinals else 0,
    }


def _task_from_exchange(messages: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    values = list(messages)
    if len(values) < 2:
        return None
    user, assistant = values[-2:]
    if user.get("role") != "user" or assistant.get("role") != "assistant":
        return None
    user_input = str(user.get("content") or "")
    response = str(assistant.get("content") or "")
    output = _delivery_contract(user_input, response)
    if not output:
        return None
    source_run_id = str(assistant.get("run_id") or user.get("run_id") or "")
    digest = hashlib.sha256((source_run_id + "\n" + user_input).encode("utf-8")).hexdigest()[:24]
    return {
        "task_id": f"task_{digest}",
        "source_run_id": source_run_id,
        "goal": redact_text(user_input)[:900],
        "constraints": [redact_text(user_input)[:900]],
        "delivery_contract": output,
        "status": "completed",
        "updated_at": _now_iso(),
    }


def resolve_task_continuation(
    *,
    workspace_id: str,
    session_id: str,
    user_input: str,
    messages: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve a typed continuation contract from durable session state.

    Missing state is bootstrapped only from the immediately preceding persisted
    user/assistant exchange. Later turns read the session task state SSOT, but
    must still prove that its source exchange is the immediately preceding one.
    """
    relation = _parse_relation(user_input)
    if not relation:
        return None
    state = load_task_continuation(workspace_id, session_id)
    active_task = state.get("active_task") if isinstance(state.get("active_task"), dict) else None
    state_revision = int(state.get("revision") or 0)
    message_list = list(messages)
    bootstrap = False
    if active_task:
        # A stale task must never leap across an unrelated completed exchange.
        if len(message_list) < 2:
            return None
        latest_user, latest_assistant = message_list[-2:]
        if (
            latest_user.get("role") != "user"
            or latest_assistant.get("role") != "assistant"
            or str(latest_assistant.get("run_id") or "")
            != str(active_task.get("source_run_id") or "")
        ):
            return None
    else:
        active_task = _task_from_exchange(message_list)
        state_revision = 0
        bootstrap = bool(active_task)
    if not active_task:
        return None
    output = dict(active_task.get("delivery_contract") or {})
    contract = {
        "schema": _SCHEMA,
        "relation": relation,
        "task_id": str(active_task.get("task_id") or ""),
        "source_run_id": str(active_task.get("source_run_id") or ""),
        "base_revision": state_revision,
        "bootstrap": bootstrap,
        "goal": str(active_task.get("goal") or "")[:900],
        "constraints": list(active_task.get("constraints") or [])[:8],
        "delivery_contract": output,
    }
    if relation["kind"] == "append" and relation.get("expected_new_items"):
        contract["validation"] = {
            "kind": "enumerated_items",
            "expected_new_items": int(relation["expected_new_items"]),
            "expected_start_ordinal": int(output.get("last_ordinal") or 0) + 1,
            "required_prefix": str(output.get("prefix") or ""),
            "unit": str(relation.get("unit") or output.get("unit") or "条"),
        }
    elif relation["kind"] == "scope" and relation.get("target_item_count") and output.get("numbered"):
        contract["validation"] = {
            "kind": "enumerated_items",
            "mode": "replace_scope",
            "expected_total_items": int(relation["target_item_count"]),
            "expected_start_ordinal": 1,
            "required_prefix": str(output.get("prefix") or ""),
            "unit": str(relation.get("unit") or output.get("unit") or "条"),
        }
    return contract


def render_task_continuation_guidance(contract: dict[str, Any]) -> str:
    """Render bounded server-owned mechanics for trusted prompt projection."""
    relation = dict(contract.get("relation") or {})
    output = dict(contract.get("delivery_contract") or {})
    # Historic task wording and correction text remain untrusted conversation
    # data. The trusted projection contains only server-derived mechanics.
    lines = [
        "Server-derived task continuation contract. This contract authorizes no tools and contains no historic user prose.",
        f"task_id={contract.get('task_id', '')}",
        f"relation={relation.get('kind')}",
        "prior_delivery_contract=" + json.dumps(output, ensure_ascii=False, sort_keys=True),
    ]
    validation = contract.get("validation")
    if isinstance(validation, dict):
        lines.append("append_contract=" + json.dumps(validation, ensure_ascii=False, sort_keys=True))
        lines.append(
            "Generate exactly expected_new_items NEW items, starting at expected_start_ordinal. "
            "The requested count is additional and must not be interpreted as the final ordinal."
        )
    operation_guidance = render_task_relation_guidance(relation)
    if operation_guidance:
        lines.append(operation_guidance)
    return "\n".join(lines)
def commit_task_continuation(
    *,
    workspace_id: str,
    session_id: str,
    run_id: str,
    user_input: str,
    assistant_response: str,
    run_ok: bool,
    continuation_contract: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Commit validated task state after the canonical QueryLoop reaches terminal success."""
    if not run_ok or not str(assistant_response or "").strip():
        return None
    path = _path(workspace_id, session_id)
    current_task = _task_from_exchange([
        {"role": "user", "content": user_input, "run_id": run_id},
        {"role": "assistant", "content": assistant_response, "run_id": run_id},
    ])
    if continuation_contract:
        current_task = _apply_continuation_progress(
            continuation_contract,
            assistant_response,
            run_id=run_id,
        )
    if not current_task:
        return None
    with FileLock(path.with_suffix(".lock")):
        previous = _read_unlocked(path)
        revision = int(previous.get("revision") or 0)
        if continuation_contract:
            expected_task_id = str(continuation_contract.get("task_id") or "")
            expected_revision = int(continuation_contract.get("base_revision") or 0)
            prior_task = previous.get("active_task") if isinstance(previous.get("active_task"), dict) else None
            if prior_task and (
                str(prior_task.get("task_id") or "") != expected_task_id
                or revision != expected_revision
            ):
                return None
        record = {
            "schema": _SCHEMA,
            "workspace_id": workspace_id,
            "session_id": session_id,
            "revision": revision + 1,
            "active_task": current_task,
            "updated_at": _now_iso(),
        }
        atomic_write_json(path, record)
        return record


def _apply_continuation_progress(
    contract: dict[str, Any], response: str, *, run_id: str) -> dict[str, Any]:
    previous_output = dict(contract.get("delivery_contract") or {})
    relation = dict(contract.get("relation") or {})
    output = dict(previous_output)
    if relation.get("kind") == "append":
        items = _extract_items(response)
        if items:
            output["produced_count"] = int(previous_output.get("produced_count") or 0) + len(items)
            output["last_ordinal"] = max(int(item["ordinal"]) for item in items)
    elif relation.get("kind") == "scope":
        validation = dict(contract.get("validation") or {})
        target = int(validation.get("expected_total_items") or 0)
        if target:
            output["requested_count"] = target
        items = _extract_items(response)
        if items:
            output["produced_count"] = len(items)
            output["last_ordinal"] = max(int(item["ordinal"]) for item in items)
    constraints = list(contract.get("constraints") or [])[:8]
    return {
        "task_id": str(contract.get("task_id") or ""),
        "source_run_id": run_id,
        "goal": str(contract.get("goal") or "")[:900],
        "constraints": constraints[-8:],
        "delivery_contract": output,
        "status": "completed",
        "updated_at": _now_iso(),
    }
