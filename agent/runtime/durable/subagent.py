# agent/runtime/durable/subagent.py
"""Phase 9: Subagent Runtime — isolated worker profiles, tasks, and execution."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal
import re
import threading
import uuid, time as _time
from agent.runtime.utils import now_iso

# ── Re-export for convenience ──
# Domain-neutral profiles are the single source of truth for built-in subagent types.

def _now(): return now_iso()
def _sid(): return f"sub-{uuid.uuid4().hex[:8]}"

# ── Profiles ──

@dataclass
class SubagentProfile:
    profile_id: str
    name: str
    role: str = ""
    description: str = ""
    allowed_tools: list = field(default_factory=list)       # explicit tool_id list
    allowed_action_classes: list = field(default_factory=list)  # read/write/execute/...
    max_steps: int = 5
    max_runtime_seconds: int = 60
    max_context_tokens: int = 8000
    memory_write_policy: str = "pending_only"  # none | pending_only
    can_modify_files: bool = False
    can_execute_commands: bool = False
    can_call_network: bool = False
    output_contract: str = ""  # description of expected output format
    merge_strategy: str = "append"  # append | replace | report

BUILTIN_PROFILES: dict[str, SubagentProfile] = {
    "research_agent": SubagentProfile(
        profile_id="research_agent",
        name="Research Agent",
        role="Collects and summarizes evidence from knowledge, files, artifacts, and public web sources.",
        allowed_action_classes=["read", "network"],
        allowed_tools=["knowledge.manage", "web.manage", "workspace.file", "workspace.artifact", "system.manage"],
        max_steps=8,
        max_runtime_seconds=180,
        can_call_network=True,
        memory_write_policy="pending_only",
        output_contract="Evidence-backed findings, uncertainty, blockers, and source references.",
    ),
    "file_agent": SubagentProfile(
        profile_id="file_agent",
        name="File Agent",
        role="Inspects and edits workspace files within the delegated scope.",
        allowed_action_classes=["read", "write"],
        allowed_tools=["workspace.file", "workspace.artifact", "text.analyze", "report.manage"],
        max_steps=10,
        max_runtime_seconds=300,
        can_modify_files=True,
        memory_write_policy="pending_only",
        output_contract="Changed files, evidence, validation result, and remaining risks.",
    ),
    "data_agent": SubagentProfile(
        profile_id="data_agent",
        name="Data Agent",
        role="Parses and analyzes structured text/table data and drafts compact reports.",
        allowed_action_classes=["read", "write"],
        allowed_tools=["data.manage", "report.manage", "workspace.file", "workspace.artifact"],
        max_steps=8,
        max_runtime_seconds=180,
        can_modify_files=True,
        memory_write_policy="pending_only",
        output_contract="Data summary, transformations, caveats, and saved artifacts when created.",
    ),
}


# ── SubagentTask & Result ──

@dataclass
class SubagentTask:
    subtask_id: str = field(default_factory=_sid)
    parent_task_id: str = ""
    workspace_id: str = ""
    session_id: str = ""
    profile_id: str = ""
    goal: str = ""
    input_context_refs: list = field(default_factory=list)
    status: str = "created"  # created | running | succeeded | failed | cancelled
    allowed_tools: list = field(default_factory=list)
    budget: dict = field(default_factory=dict)
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    summary: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = _now()


@dataclass
class SubagentResult:
    subtask_id: str = ""
    status: str = ""
    summary: str = ""
    findings: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    tool_results: list = field(default_factory=list)
    memory_candidates: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    finished_at: str = ""


# ── Runtime ──

def get_profile(profile_id: str) -> Optional[SubagentProfile]:
    return BUILTIN_PROFILES.get(profile_id)


_TASK_LOCK = threading.RLock()
_CANCEL_EVENTS: dict[tuple[str, str], threading.Event] = {}
_WORKER_THREADS: dict[tuple[str, str], threading.Thread] = {}
_SUBTASK_ID_RE = re.compile(r"^sub-[a-f0-9]{8}$")


def create_subagent_task(
    parent_task_id: str, workspace_id: str, session_id: str,
    profile_id: str, goal: str, context_refs: list = None,
    max_steps: int | None = None,
) -> dict:
    profile = get_profile(profile_id)
    if not profile:
        return {"ok": False, "error": f"unknown profile: {profile_id}"}
    try:
        workspace_id = _validated_workspace_id(workspace_id)
    except ValueError:
        return {"ok": False, "error": "invalid_workspace_id"}

    task = SubagentTask(
        parent_task_id=parent_task_id,
        workspace_id=workspace_id,
        session_id=session_id,
        profile_id=profile_id,
        goal=goal,
        input_context_refs=context_refs or [],
        allowed_tools=profile.allowed_tools,
        budget={
            "max_steps": max(1, min(int(max_steps or profile.max_steps), profile.max_steps)),
            "max_runtime_seconds": profile.max_runtime_seconds,
        },
    )
    _save_task(task)

    _emit_event(workspace_id, parent_task_id, session_id, "subagent_created",
                f"Subagent {profile.name} created for task {parent_task_id}")

    return {"ok": True, "subtask_id": task.subtask_id, "profile": profile.name}


def run_subagent_task(subtask_id: str, ws_id: str) -> dict:
    """Real LLM-driven subagent execution through SSOT Runtime.

    The profile provides the SSOT Runtime-visible tool allowlist. Tool execution still
    goes through ToolRuntimeClient with caller=subagent.
    """
    try:
        ws_id = _validated_workspace_id(ws_id)
        subtask_id = _validated_subtask_id(subtask_id)
    except ValueError:
        return {"ok": False, "error": "invalid_subtask_identity"}
    task = _load_task(ws_id, subtask_id)
    if not task:
        return {"ok": False, "error": "subtask not found"}
    if task.workspace_id != ws_id:
        return {"ok": False, "error": "workspace mismatch"}

    profile = get_profile(task.profile_id)
    if not profile:
        return {"ok": False, "error": "profile not found"}

    key = (ws_id, subtask_id)
    with _TASK_LOCK:
        active_worker = _WORKER_THREADS.get(key)
        if task.status in {"succeeded", "failed"}:
            return {
                "ok": False,
                "error": f"subtask is already {task.status}",
                "status": task.status,
            }
        if (
            task.status == "running"
            and active_worker is not None
            and active_worker.is_alive()
            and active_worker is not threading.current_thread()
        ):
            return {"ok": True, "subtask_id": subtask_id, "status": "running"}
        if task.status not in {"created", "running", "cancelled"}:
            return {"ok": False, "error": f"invalid subtask status: {task.status}"}

    cancel_event = _cancel_event(ws_id, subtask_id)
    if task.status == "cancelled" or cancel_event.is_set():
        payload = _task_result_payload(task, ok=False)
        _release_worker(ws_id, subtask_id)
        return payload

    task.status = "running"
    task.started_at = task.started_at or _now()
    _save_task(task)
    start = _time.time()
    timed_out = False
    result = SubagentResult(subtask_id=subtask_id, status="succeeded")

    # Register in live tasks registry for cancel/status
    from agent.runtime.durable.trajectory import _live_tasks
    _live_tasks[subtask_id] = {
        "subtask_id": subtask_id,
        "status": "running",
        "profile_id": task.profile_id,
        "goal": task.goal,
        "started_at": _now(),
    }

    try:
        # Create restricted session for profile-gated SSOT Runtime execution.
        from agent.core.session import AgentSession

        child_session_id = subtask_id
        sess = AgentSession(session_id=child_session_id, workspace_id=ws_id)
        sess.mark_sub_agent()
        effective_steps = max(1, min(
            int((task.budget or {}).get("max_steps") or profile.max_steps),
            profile.max_steps,
        ))
        sess.metadata["max_steps"] = effective_steps
        sess.metadata["parent_session_id"] = task.session_id
        sess.metadata["subtask_id"] = subtask_id

        # Submit via SSOT Runtime with restricted tools.
        from agent.core.turn import AgentTurn
        from agent.protocol.op import AgentOp
        from agent.runtime.ssot_runtime import run_ssot_turn
        op = AgentOp(
            user_input=task.goal,
            workspace_id=ws_id,
            session_id=child_session_id,
            metadata={
                "subagent_profile": {
                    "profile_id": profile.profile_id,
                    "name": profile.name,
                    "role": profile.role,
                    "max_steps": effective_steps,
                    "max_runtime_seconds": profile.max_runtime_seconds,
                    "allowed_action_classes": list(profile.allowed_action_classes),
                    "output_contract": profile.output_contract,
                },
                "parent_session_id": task.session_id,
                "subtask_id": subtask_id,
                "cancel_check": cancel_event.is_set,
            },
        )
        turn = AgentTurn.from_op(op)
        turn.metadata = {
            "max_steps": effective_steps,
            "subtask_id": subtask_id,
            "subagent_profile": profile.profile_id,
            "cancel_check": cancel_event.is_set,
        }

        try:
            llm_result = _run_ssot_runtime_with_timeout(
                run_ssot_turn,
                sess,
                turn,
                set(profile.allowed_tools or []),
                timeout_seconds=profile.max_runtime_seconds,
                cancel_event=cancel_event,
            )
        except TimeoutError as exc:
            timed_out = True
            llm_result = None
            result.status = "failed"
            result.summary = "Subagent timed out"
            result.errors.append(str(exc)[:200])
        except Exception as e:
            raise RuntimeError(f"LLM turn failed: {str(e)[:200]}") from e

        elapsed = _time.time() - start
        final_resp = (getattr(llm_result, "final_response", "") or "") if llm_result is not None else ""
        is_ok = bool(getattr(llm_result, "ok", False)) if llm_result is not None else False

        # AgentResult.tool_calls is the canonical one-row-per-action projection.
        for te in (getattr(llm_result, "tool_calls", []) or []) if llm_result is not None else []:
            te_get = te.get if isinstance(te, dict) else lambda key, default=None: getattr(te, key, default)
            tool_id = str(te_get("tool_id", "") or "")
            tools_ok = bool(te_get("ok", False))
            summary = str(te_get("summary", "") or "")[:200]
            result.tool_results.append({
                "tool_id": tool_id, "ok": tools_ok,
                "summary": summary,
            })

        if timed_out:
            result.warnings.append(f"Budget exceeded: {profile.max_runtime_seconds}s")
        elif cancel_event.is_set():
            result.status = "cancelled"
            result.summary = "Subagent cancelled by user"
        elif is_ok and final_resp:
            result.summary = final_resp[:4000]
            result.findings = [final_resp[:1000]]
        elif elapsed >= profile.max_runtime_seconds:
            result.status = "failed"
            result.warnings.append(f"Budget exceeded: {profile.max_runtime_seconds}s")
            result.summary = "Subagent timed out"
        else:
            result.status = "failed"
            result.summary = "Subagent LLM call failed"
            if not is_ok:
                result.errors.append("LLM returned error")

    except Exception as e:
        result.status = "failed"
        result.errors.append(f"subagent execution failed: {str(e)[:200]}")
        result.summary = f"Subagent execution error: {str(e)[:100]}"

    elapsed = _time.time() - start
    if elapsed >= profile.max_runtime_seconds and not timed_out:
        result.warnings.append(f"Runtime budget {profile.max_runtime_seconds}s exceeded")
        if result.status != "failed":
            result.status = "failed"

    with _TASK_LOCK:
        persisted = _load_task(ws_id, subtask_id)
        if (persisted and persisted.status == "cancelled") or (
            cancel_event.is_set() and not timed_out
        ):
            result.status = "cancelled"
            result.summary = "Subagent cancelled by user"
        task.status = result.status
        task.summary = result.summary[:4000]
        task.finished_at = _now()
        _save_task(task)
    result.finished_at = _now()

    # Update live tasks registry with final status
    from agent.runtime.durable.trajectory import _live_tasks
    if subtask_id in _live_tasks:
        _live_tasks[subtask_id]["status"] = result.status
        _live_tasks[subtask_id]["finished_at"] = _now()
        _live_tasks[subtask_id]["summary"] = (result.summary or "")[:200]
    _prune_live_tasks(_live_tasks)

    # Emit timeline events
    event_type = {
        "succeeded": "subagent_succeeded",
        "cancelled": "subagent_cancelled",
    }.get(result.status, "subagent_failed")
    _emit_event(ws_id, task.parent_task_id, task.session_id, event_type,
                f"Subagent {profile.name}: {result.summary[:200]}")

    # v3.10: Generate pending memory candidates (subagent cannot write active memory)
    try:
        from storage.memory_governance import MemoryRecord, MemoryWriteGate
        gate = MemoryWriteGate()
        for tr in result.tool_results if result.status == "succeeded" else []:
            if tr.get("ok"):
                rec = MemoryRecord(
                    workspace_id=ws_id, session_id=task.session_id,
                    task_id=task.parent_task_id, scope="task",
                    memory_type="procedural_rule",
                    status="pending", source="subagent",
                    content=str(tr.get("summary", ""))[:500],
                    summary=f"Subagent {profile.name}: {tr.get('tool_id', '')}",
                    confidence=0.5,
                    citations=[{"subtask_id": subtask_id}],
                    created_by="subagent",
                    redacted=True,
                )
                gate.write(rec)
    except Exception as e:
        result.warnings.append(f"Memory candidate write failed: {str(e)[:100]}")

    payload = {
        "ok": result.status == "succeeded",
        "subtask_id": subtask_id,
        "child_session_id": subtask_id,
        "status": result.status,
        "summary": result.summary,
        "findings": result.findings,
        "tool_results": result.tool_results,
        "errors": result.errors,
        "warnings": result.warnings,
    }
    _release_worker(ws_id, subtask_id)
    return payload


def start_subagent_task(subtask_id: str, ws_id: str) -> dict:
    """Start one persisted subagent task exactly once in the background."""
    try:
        ws_id = _validated_workspace_id(ws_id)
        subtask_id = _validated_subtask_id(subtask_id)
    except ValueError:
        return {"ok": False, "error": "invalid_subtask_identity"}
    task = _load_task(ws_id, subtask_id)
    if not task or task.workspace_id != ws_id:
        return {"ok": False, "error": "subtask not found"}
    key = (ws_id, subtask_id)
    with _TASK_LOCK:
        existing = _WORKER_THREADS.get(key)
        if existing and existing.is_alive():
            return {"ok": True, "subtask_id": subtask_id, "status": task.status}
        if task.status != "created":
            return {
                "ok": False,
                "error": f"subtask is {task.status}; only created tasks can start",
                "status": task.status,
            }
        _cancel_event(ws_id, subtask_id).clear()
        task.status = "running"
        task.started_at = task.started_at or _now()
        _save_task(task)
        from storage.principal import bind_storage_principal
        worker = threading.Thread(
            target=bind_storage_principal(run_subagent_task),
            args=(subtask_id, ws_id),
            name=f"subagent-{subtask_id}",
            daemon=True,
        )
        _WORKER_THREADS[key] = worker
        try:
            worker.start()
        except RuntimeError as exc:
            _WORKER_THREADS.pop(key, None)
            task.status = "failed"
            task.finished_at = _now()
            task.summary = "Subagent worker failed to start"
            task.errors.append(type(exc).__name__)
            _save_task(task)
            return {"ok": False, "error": "subagent_worker_start_failed"}
    return {"ok": True, "subtask_id": subtask_id, "status": "running"}


def cancel_subagent_task(subtask_id: str, ws_id: str) -> dict:
    """Persist cancellation and signal the running QueryLoop cooperatively."""
    try:
        ws_id = _validated_workspace_id(ws_id)
        subtask_id = _validated_subtask_id(subtask_id)
    except ValueError:
        return {"ok": False, "error": "invalid_subtask_identity"}
    task = _load_task(ws_id, subtask_id)
    if not task or task.workspace_id != ws_id:
        return {"ok": False, "error": "subtask not found"}
    if task.status in {"succeeded", "failed", "cancelled"}:
        return {
            "ok": False,
            "error": f"subtask is already {task.status}",
            "status": task.status,
        }
    _cancel_event(ws_id, subtask_id).set()
    if task.status not in {"succeeded", "failed", "cancelled"}:
        task.status = "cancelled"
        task.finished_at = _now()
        task.summary = "Subagent cancelled by user"
        _save_task(task)
    from agent.runtime.durable.trajectory import _live_tasks
    live = _live_tasks.get(subtask_id)
    if live is not None:
        live.update({"status": "cancelled", "cancelled_at": _now()})
    return {"ok": True, "subtask_id": subtask_id, "status": task.status}


def list_subagent_tasks(ws_id: str, limit: int = 200) -> list[dict]:
    """Return persisted tasks for one workspace, newest first."""
    from storage.subagent_store import list_subagents
    try:
        ws_id = _validated_workspace_id(ws_id)
    except ValueError:
        return []
    limit = max(1, min(int(limit), 1000))
    rows: list[dict] = []
    for raw in list_subagents(ws_id, limit):
        if raw.get("workspace_id") != ws_id:
            continue
        rows.append({
            "subtask_id": raw.get("subtask_id", ""),
            "status": raw.get("status", "unknown"),
            "profile_id": raw.get("profile_id", ""),
            "instruction": str(raw.get("goal", ""))[:100],
            "summary": str(raw.get("summary", ""))[:200],
            "created_at": raw.get("created_at", ""),
            "started_at": raw.get("started_at", ""),
            "finished_at": raw.get("finished_at", ""),
        })
        if len(rows) >= limit:
            break
    return rows


def get_subagent_task(ws_id: str, subtask_id: str) -> Optional[dict]:
    """Return one persisted subagent projection without scanning task history."""
    task = _load_task(ws_id, subtask_id)
    if task is None:
        return None
    return {
        "subtask_id": task.subtask_id,
        "status": task.status,
        "profile_id": task.profile_id,
        "instruction": task.goal[:100],
        "summary": task.summary[:200],
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
    }


def reconcile_subagent_tasks() -> list[str]:
    """Mark process-owned queued/running tasks interrupted after restart."""
    from storage.subagent_store import list_subagents
    from storage.workspace_store import list_workspace_ids

    reconciled: list[str] = []
    for ws_id in list_workspace_ids():
        for raw in list_subagents(ws_id, 1000):
            try:
                if raw.get("workspace_id") != ws_id or raw.get("status") not in {"created", "running"}:
                    continue
                task = SubagentTask(**{
                    key: value for key, value in raw.items()
                    if key in SubagentTask.__dataclass_fields__
                })
                task.status = "failed"
                task.finished_at = _now()
                task.summary = "Subagent interrupted by service restart"
                task.errors.append("service_restart_interrupted")
                _save_task(task)
                reconciled.append(task.subtask_id)
            except (OSError, ValueError, TypeError):
                continue
    return reconciled


def merge_subagent_result(parent_task_id: str, subtask_id: str, ws_id: str) -> dict:
    task = _load_task(ws_id, subtask_id)
    if not task:
        return {"ok": False, "error": "subtask not found"}
    if task.workspace_id != ws_id:
        return {"ok": False, "error": "workspace mismatch"}
    if task.parent_task_id != parent_task_id:
        return {"ok": False, "error": "subtask parent mismatch"}

    profile = get_profile(task.profile_id)
    _emit_event(ws_id, parent_task_id, task.session_id, "subagent_merged",
                f"Subagent {profile.name if profile else subtask_id} merged into parent")

    return {"ok": True, "merged": True, "subtask_id": subtask_id, "parent_task_id": parent_task_id}


# ── Helpers ──

def _save_task(task: SubagentTask):
    from dataclasses import asdict
    from storage.subagent_store import save_subagent
    task.workspace_id = _validated_workspace_id(task.workspace_id)
    _validated_subtask_id(task.subtask_id)
    save_subagent(task.workspace_id, task.subtask_id, asdict(task))

def _load_task(ws_id: str, subtask_id: str) -> Optional[SubagentTask]:
    from storage.subagent_store import read_subagent
    try:
        ws_id = _validated_workspace_id(ws_id)
        subtask_id = _validated_subtask_id(subtask_id)
    except ValueError:
        return None
    try:
        raw = read_subagent(ws_id, subtask_id)
        if not raw: return None
        return SubagentTask(**{k:v for k,v in raw.items() if k in SubagentTask.__dataclass_fields__})
    except Exception: return None


def _cancel_event(ws_id: str, subtask_id: str) -> threading.Event:
    key = (ws_id, subtask_id)
    with _TASK_LOCK:
        return _CANCEL_EVENTS.setdefault(key, threading.Event())


def _release_worker(ws_id: str, subtask_id: str) -> None:
    key = (ws_id, subtask_id)
    with _TASK_LOCK:
        if _WORKER_THREADS.get(key) is threading.current_thread():
            _WORKER_THREADS.pop(key, None)
        _CANCEL_EVENTS.pop(key, None)


def _prune_live_tasks(live_tasks: dict[str, dict], limit: int = 256) -> None:
    """Bound the live status projection to recent terminal tasks."""
    overflow = len(live_tasks) - max(1, limit)
    if overflow <= 0:
        return
    terminal = [
        key for key, value in live_tasks.items()
        if value.get("status") in {"succeeded", "failed", "cancelled"}
    ]
    for key in terminal[:overflow]:
        live_tasks.pop(key, None)


def _validated_workspace_id(ws_id: str) -> str:
    from storage.ids import validate_workspace_id
    return validate_workspace_id(str(ws_id or "").strip())


def _validated_subtask_id(subtask_id: str) -> str:
    value = str(subtask_id or "").strip()
    if not _SUBTASK_ID_RE.fullmatch(value):
        raise ValueError("invalid subtask_id")
    return value


def _task_result_payload(task: SubagentTask, *, ok: bool) -> dict:
    return {
        "ok": ok,
        "subtask_id": task.subtask_id,
        "child_session_id": task.subtask_id,
        "status": task.status,
        "summary": task.summary,
        "findings": [],
        "tool_results": [],
        "errors": [],
        "warnings": [],
    }

def _get_manifest(tool_id: str):
    try:
        from core.tools.manifest_registry import get_manifest as gm
        return gm(tool_id)
    except Exception: return None

def _execute_as_subagent(tool_id: str, args: dict, ws_id: str) -> dict:
    try:
        from core.tools.integration import get_default_tool_runtime_client
        from core.tools.context import ToolRuntimeContext
        client = get_default_tool_runtime_client()
        ctx = ToolRuntimeContext(workspace_id=ws_id, requested_by="subagent")
        result = client.invoke(tool_id, args, context=ctx)
        return {"ok": result.status in ("succeeded", "dry_run"), "summary": result.summary or ""}
    except Exception as e:
        return {"ok": False, "summary": str(e)[:200]}


def _run_ssot_runtime_with_timeout(
    run_fn, session, turn, allowed_tool_ids, *, timeout_seconds: int,
    cancel_event: threading.Event | None = None,
):
    """Run a subagent turn with a hard parent-side timeout.

    Python cannot forcibly stop an already-running provider call, so timeout
    returns control to the parent and marks the subtask failed while the worker
    thread is abandoned best-effort.
    """
    import concurrent.futures
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="subagent")
    from storage.principal import bind_storage_principal
    future = executor.submit(
        bind_storage_principal(run_fn),
        session,
        turn,
        None,
        allowed_tool_ids=allowed_tool_ids,
        requested_by="subagent",
    )
    try:
        return future.result(timeout=max(1, int(timeout_seconds)))
    except concurrent.futures.TimeoutError as exc:
        if cancel_event is not None:
            cancel_event.set()
        future.cancel()
        raise TimeoutError(f"subagent runtime exceeded {timeout_seconds}s") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

def _emit_event(ws_id: str, parent_task_id: str, session_id: str, event_type: str, summary: str):
    try:
        from agent.runtime.durable import RuntimeEvent
        from agent.runtime.durable.store import append_event
        append_event(RuntimeEvent(
            event_id=f"evt-sub-{uuid.uuid4().hex[:8]}",
            task_id=parent_task_id, workspace_id=ws_id,
            session_id=session_id, run_id="",
            type=event_type, status="ok",
            title=event_type, summary=summary[:200],
        ))
    except Exception as e:
        # best-effort: event emission failure is logged, not propagated
        import logging
        logging.getLogger(__name__).debug("subagent event emission failed", exc_info=True)
