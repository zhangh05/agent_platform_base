# agent/runtime/durable/store.py
"""TaskStateStore — persistence and query.

Storage: workspaces/<ws>/durable/tasks/<id>.json, events/<id>.events.json, checkpoints/<tid>/<cid>.json
"""
from __future__ import annotations
import json
import logging
from typing import Optional
from .models import TaskState, RuntimeEvent, RuntimeCheckpoint
from agent.runtime.utils import now_iso
from storage.ids import validate_checkpoint_id, validate_task_id
from storage.durable_task_store import (
    append_task_event,
    list_checkpoint_records,
    list_task_events,
    list_task_records,
    read_task_record,
    save_checkpoint_record,
    save_task_record,
)

logger = logging.getLogger(__name__)

_REDACT_KEYS = {"password","token","api_key","secret","credential","private_key","access_key","auth","authorization","x-api-token","x-admin-token"}

def _redact(obj, max_len=256):
    if isinstance(obj, dict): return {k: "[REDACTED]" if k.lower() in _REDACT_KEYS else _redact(v) for k,v in obj.items()}
    if isinstance(obj, list): return [_redact(v) for v in obj]
    if isinstance(obj, str) and len(obj) > max_len: return obj[:max_len] + "..."
    return obj

# ── Task CRUD ──
def save_task(task: TaskState):
    task.updated_at = now_iso()
    save_task_record(task.workspace_id, task.task_id, task.to_dict())

def get_task(ws_id: str, task_id: str) -> Optional[TaskState]:
    data = read_task_record(ws_id, task_id)
    if not data: return None
    try: return TaskState.from_dict(data)
    except Exception: return None

def list_tasks(ws_id: str, session_id="", limit=50) -> list[TaskState]:
    """List tasks. P1-22: full dir scan + sort, no cache — pagination at app level."""
    if limit <= 0: return []
    tasks = []
    scan_limit = 5000 if session_id else limit
    for data in list_task_records(ws_id, scan_limit):
        try:
            t = TaskState.from_dict(data)
            if session_id and t.session_id != session_id: continue
            tasks.append(t)
            if len(tasks) >= limit: break
        except (OSError, ValueError, json.JSONDecodeError):
            # v3.9.9: a malformed TaskState file should not silently
            # skip — log it. (Continue reading remaining files.)
            logger.debug("durable: skip corrupt task record", exc_info=True)
    return tasks

# ── Events ──
def append_event(evt: RuntimeEvent):
    validate_task_id(evt.task_id)
    evt.created_at = now_iso()
    evt.payload_redacted = _redact(evt.payload_redacted)
    try:
        append_task_event(evt.workspace_id, evt.task_id, evt.__dict__)
    except OSError:
        # v3.9.9: was bare ``except Exception: pass`` — losing the
        # entire event log silently hides every tool error, every
        # approval decision, every retry. Surface it at WARNING so
        # the admin sees disk pressure.
        logger.warning("durable: append_event write failed for %s",
                       evt.task_id, exc_info=True)

def get_events(ws_id, task_id, limit=100) -> list[dict]:
    task_id = validate_task_id(task_id)
    return list_task_events(ws_id, task_id, limit)

# ── Checkpoints ──
def save_checkpoint(cp: RuntimeCheckpoint):
    validate_task_id(cp.task_id)
    validate_checkpoint_id(cp.checkpoint_id)
    cp.created_at = now_iso()
    cp.state_snapshot = _redact(cp.state_snapshot)
    if cp.pending_action: cp.pending_action = _redact(cp.pending_action)
    save_checkpoint_record(cp.workspace_id, cp.task_id, cp.checkpoint_id, cp.__dict__)

def get_checkpoints(ws_id, task_id) -> list[dict]:
    return list_checkpoint_records(ws_id, task_id)
