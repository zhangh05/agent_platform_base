"""Validated DAG workflows executed through the governed Tool Runtime."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
from typing import Any
import uuid

from storage.atomic_io import atomic_write_json
from storage.locking import FileLock
from storage.records import workspace_record_dir, workspace_record_file
from storage.time_utils import now_iso


class WorkflowError(ValueError):
    pass


def _id(value: str, label: str = "workflow_id") -> str:
    result = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", result):
        raise WorkflowError(f"invalid {label}")
    return result


def _definition_path(workspace_id: str, workflow_id: str) -> Path:
    return workspace_record_file(workspace_id, "workflows", "definitions", f"{_id(workflow_id)}.json")


def _run_path(workspace_id: str, run_id: str) -> Path:
    return workspace_record_file(workspace_id, "workflows", "runs", f"{_id(run_id, 'run_id')}.json")


def validate_definition(payload: dict[str, Any]) -> dict[str, Any]:
    from core.runtime_engine.models import SSOTRuntimeConfig

    limits = SSOTRuntimeConfig()
    if not isinstance(payload, dict):
        raise WorkflowError("workflow definition must be an object")
    workflow_id = _id(str(payload.get("workflow_id") or f"workflow_{uuid.uuid4().hex[:8]}"))
    name = str(payload.get("name") or "").strip()
    if not name:
        raise WorkflowError("workflow name is required")
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= limits.max_nodes:
        raise WorkflowError(f"workflow must contain 1 to {limits.max_nodes} nodes")
    available_tools = {item["tool_id"] for item in _tool_client().list_tools() if item.get("enabled", True)}
    normalized = []
    node_ids: set[str] = set()
    for raw in nodes:
        if not isinstance(raw, dict):
            raise WorkflowError("workflow nodes must be objects")
        node_id = _id(str(raw.get("node_id") or ""), "node_id")
        if node_id in node_ids:
            raise WorkflowError(f"duplicate workflow node: {node_id}")
        node_ids.add(node_id)
        tool_id = str(raw.get("tool_id") or "").strip()
        if tool_id not in available_tools:
            raise WorkflowError(f"unknown or disabled workflow tool: {tool_id}")
        arguments = raw.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise WorkflowError(f"node arguments must be an object: {node_id}")
        _reject_static_secrets(arguments, node_id)
        depends_on = raw.get("depends_on") or []
        if not isinstance(depends_on, list):
            raise WorkflowError(f"node dependencies must be a list: {node_id}")
        normalized.append({
            "node_id": node_id,
            "name": str(raw.get("name") or node_id)[:120],
            "tool_id": tool_id,
            "arguments": arguments,
            "depends_on": [str(item) for item in depends_on],
            "when": raw.get("when", True),
        })
    for node in normalized:
        missing = set(node["depends_on"]) - node_ids
        if missing or node["node_id"] in node["depends_on"]:
            raise WorkflowError(f"invalid dependencies for {node['node_id']}: {sorted(missing)}")
    layers = _execution_layers(normalized)
    if len(layers) > limits.max_depth:
        raise WorkflowError(f"workflow depth exceeds runtime limit: {limits.max_depth}")
    order = [node_id for layer in layers for node_id in layer]
    failure_policy = str(payload.get("failure_policy") or "fail_fast")
    if failure_policy not in {"fail_fast", "continue"}:
        raise WorkflowError("failure_policy must be fail_fast or continue")
    try:
        version = int(payload.get("version") or 1)
    except (TypeError, ValueError) as exc:
        raise WorkflowError("workflow version must be a positive integer") from exc
    if version < 1:
        raise WorkflowError("workflow version must be a positive integer")
    status = str(payload.get("status") or "active")
    if status not in {"draft", "active", "archived"}:
        raise WorkflowError("workflow status must be draft, active, or archived")
    _validate_references(normalized)
    return {
        "workflow_id": workflow_id,
        "name": name[:120],
        "description": str(payload.get("description") or "")[:1000],
        "version": version,
        "status": status,
        "failure_policy": failure_policy,
        "nodes": normalized,
        "execution_order": order,
        "execution_layers": layers,
    }


def _topological_order(nodes: list[dict[str, Any]]) -> list[str]:
    return [node_id for layer in _execution_layers(nodes) for node_id in layer]


def _execution_layers(nodes: list[dict[str, Any]]) -> list[list[str]]:
    dependencies = {node["node_id"]: set(node["depends_on"]) for node in nodes}
    layers: list[list[str]] = []
    while dependencies:
        ready = sorted(node_id for node_id, required in dependencies.items() if not required)
        if not ready:
            raise WorkflowError("workflow graph contains a cycle")
        layers.append(ready)
        for node_id in ready:
            dependencies.pop(node_id)
        for required in dependencies.values():
            required.difference_update(ready)
    return layers


def _validate_references(nodes: list[dict[str, Any]]) -> None:
    dependencies = {node["node_id"]: set(node["depends_on"]) for node in nodes}
    def ancestors(node_id: str) -> set[str]:
        result: set[str] = set()
        pending = list(dependencies[node_id])
        while pending:
            dependency = pending.pop()
            if dependency in result: continue
            result.add(dependency); pending.extend(dependencies[dependency])
        return result
    def references(value: Any) -> set[str]:
        if isinstance(value, dict): return set().union(*(references(item) for item in value.values())) if value else set()
        if isinstance(value, list): return set().union(*(references(item) for item in value)) if value else set()
        if isinstance(value, str): return {match.group(1).split(".", 1)[0] for match in re.finditer(r"\$\{nodes\.([A-Za-z0-9_-]+)\.", value)}
        return set()
    for node in nodes:
        invalid = (references(node["arguments"]) | references(node.get("when"))) - ancestors(node["node_id"])
        if invalid:
            raise WorkflowError(f"node {node['node_id']} references nodes outside its dependencies: {sorted(invalid)}")


def _reject_static_secrets(value: Any, node_id: str, key: str = "") -> None:
    from storage.redaction import is_sensitive_field

    if isinstance(value, dict):
        for item_key, item in value.items(): _reject_static_secrets(item, node_id, str(item_key))
        return
    if isinstance(value, list):
        for item in value: _reject_static_secrets(item, node_id, key)
        return
    if is_sensitive_field(key) and not (isinstance(value, str) and _TEMPLATE.search(value)):
        raise WorkflowError(
            f"node {node_id} contains a static secret; use a secret reference or a preconfigured asset"
        )


def _reject_runtime_secrets(value: Any, key: str = "") -> None:
    """Keep raw secrets out of durable, resumable workflow state."""
    from storage.redaction import is_sensitive_field

    if isinstance(value, dict):
        for item_key, item in value.items():
            _reject_runtime_secrets(item, str(item_key))
        return
    if isinstance(value, list):
        for item in value:
            _reject_runtime_secrets(item, key)
        return
    if is_sensitive_field(key) and value is not None and value != "":
        raise WorkflowError(
            "workflow inputs cannot contain raw secrets; use a secret reference or a preconfigured asset"
        )


def validate_workflow_inputs(inputs: dict[str, Any] | None) -> dict[str, Any]:
    """Validate workflow inputs before any durable run or queue record is created."""
    normalized = inputs or {}
    if not isinstance(normalized, dict):
        raise WorkflowError("workflow inputs must be an object")
    _reject_runtime_secrets(normalized)
    if len(json.dumps(normalized, ensure_ascii=False, default=str).encode()) > 1_048_576:
        raise WorkflowError("workflow inputs are too large")
    return dict(normalized)


def save_workflow(workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    definition = validate_definition(payload)
    path = _definition_path(workspace_id, definition["workflow_id"])
    existing = get_workflow(workspace_id, definition["workflow_id"])
    definition["created_at"] = str((existing or {}).get("created_at") or now_iso())
    definition["updated_at"] = now_iso()
    with FileLock(path.with_suffix(".lock")):
        atomic_write_json(path, definition)
    return definition


def get_workflow(workspace_id: str, workflow_id: str) -> dict[str, Any] | None:
    path = _definition_path(workspace_id, workflow_id)
    if not path.is_file(): return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def list_workflows(workspace_id: str) -> list[dict[str, Any]]:
    root = workspace_record_dir(workspace_id, "workflows", "definitions", create=False)
    if not root.is_dir(): return []
    records = []
    for path in sorted(root.glob("*.json")):
        value = get_workflow(workspace_id, path.stem)
        if value and value.get("status") != "archived": records.append(value)
    return records


def delete_workflow(workspace_id: str, workflow_id: str) -> dict[str, Any]:
    """Permanently remove one inactive workflow and its completed run records.

    Active executions are protected so the unified worker never loses its
    canonical definition while running. Global Job records remain platform audit data.
    """
    current = get_workflow(workspace_id, workflow_id)
    if not current:
        raise WorkflowError("workflow not found")
    runs = list_runs(workspace_id, workflow_id, limit=500)
    if any(run.get("status") in {"queued", "running", "awaiting_approval"} for run in runs):
        raise WorkflowError("workflow_has_active_runs")
    definition_path = _definition_path(workspace_id, workflow_id)
    removable_runs = [_run_path(workspace_id, str(run["run_id"])) for run in runs if run.get("run_id")]
    with FileLock(definition_path.with_suffix(".lock")):
        if not definition_path.is_file():
            raise WorkflowError("workflow not found")
        definition_path.unlink()
    removed_runs = 0
    for run_path in removable_runs:
        with FileLock(run_path.with_suffix(".lock")):
            if run_path.is_file():
                run_path.unlink()
                removed_runs += 1
    return {"workflow_id": workflow_id, "removed_runs": removed_runs}


def execute_workflow(workspace_id: str, workflow_id: str, inputs: dict[str, Any] | None = None, *, approvals: dict[str, str] | None = None, job_id: str = "", run_id: str = "") -> dict[str, Any]:
    definition = get_workflow(workspace_id, workflow_id)
    if not definition or definition.get("status") != "active":
        raise WorkflowError("workflow not found or inactive")
    definition = validate_definition(definition)
    inputs = validate_workflow_inputs(inputs)
    requested_run_id = str(run_id or "")
    run_id = _id(requested_run_id or f"wfrun_{uuid.uuid4().hex[:12]}", "run_id")
    from core.runtime_engine.budget_controller import BudgetController
    from core.runtime_engine.models import SSOTRuntimeConfig

    runtime_config = SSOTRuntimeConfig()
    budget = BudgetController(runtime_config)
    layers = definition.get("execution_layers") or _execution_layers(definition["nodes"])
    reservation = budget.reserve_execution_batch(
        node_count=len(definition["nodes"]),
        depth=len(layers),
        parallel_width=min(
            runtime_config.max_layer_concurrency,
            max((len(layer) for layer in layers), default=1),
        ),
    )
    if not reservation.ok:
        raise WorkflowError(f"workflow execution budget rejected: {reservation.exceeded}")
    from storage.redaction import redact_dict
    existing_run = get_run(workspace_id, run_id) if requested_run_id else None
    outputs: dict[str, Any] = {}
    dependency_outcomes: dict[str, bool] = {}
    prior_failed = False
    if existing_run is not None:
        if existing_run.get("workflow_id") != workflow_id or existing_run.get("status") != "awaiting_approval":
            raise WorkflowError("workflow run is not resumable")
        record = dict(existing_run)
        retained_nodes = []
        for entry in list(record.get("nodes") or []):
            node_id = str(entry.get("node_id") or "")
            if entry.get("status") == "awaiting_approval":
                continue
            retained_nodes.append(entry)
            outputs[node_id] = dict(entry.get("output") or {})
            dependency_outcomes[node_id] = entry.get("status") in {"succeeded", "dry_run", "skipped"}
            prior_failed = prior_failed or entry.get("status") in {"failed", "cancelled", "rejected"}
        record["nodes"] = retained_nodes
        record["status"] = "running"
        record["updated_at"] = now_iso()
        record.pop("finished_at", None)
    else:
        record = {
            "run_id": run_id,
            "workflow_id": workflow_id,
            "workflow_version": definition["version"],
            "workspace_id": workspace_id,
            "job_id": job_id,
            "status": "running",
            "inputs": redact_dict(dict(inputs or {})),
            "nodes": [],
            "started_at": now_iso(),
            "updated_at": now_iso(),
        }
    _save_run(record)
    budget.begin_execution()
    nodes_by_id = {node["node_id"]: node for node in definition["nodes"]}
    failed = prior_failed
    approval_pending = False
    def execute_node(node_id: str) -> tuple[dict[str, Any], dict[str, Any], bool]:
        node = nodes_by_id[node_id]
        try:
            scope = {"input": dict(inputs or {}), "nodes": {key: {"output": value} for key, value in outputs.items()}}
            if not bool(_resolve(node.get("when", True), scope)):
                entry = {"node_id": node_id, "tool_id": node["tool_id"], "status": "skipped", "started_at": now_iso(), "finished_at": now_iso()}
                return entry, {}, True
            arguments = _resolve(node["arguments"], scope)
            tool_client = _tool_client()
            arguments = tool_client.canonicalize_arguments(node["tool_id"], arguments)
            if len(json.dumps(arguments, ensure_ascii=False, default=str).encode()) > 1_048_576:
                raise WorkflowError(f"resolved arguments are too large: {node_id}")
        except Exception as exc:
            entry = {"node_id": node_id, "tool_id": node["tool_id"], "status": "failed", "summary": "步骤输入解析失败", "errors": [str(exc)[:500]], "started_at": now_iso(), "finished_at": now_iso()}
            return entry, {}, False
        started_at = now_iso()
        approval_id = str((approvals or {}).get(node_id) or "")
        if approval_id and not _workflow_approval_is_valid(
            approval_id=approval_id,
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            run_id=run_id,
            node=node,
            arguments=arguments,
        ):
            entry = {
                "node_id": node_id,
                "tool_id": node["tool_id"],
                "status": "failed",
                "summary": "审批凭证无效或与当前流程动作不匹配",
                "errors": ["invalid_approval_id"],
                "started_at": started_at,
                "finished_at": now_iso(),
            }
            return entry, {}, False
        from core.tools.context import ToolRuntimeContext
        try:
            result = tool_client.invoke(
                node["tool_id"],
                arguments,
                context=ToolRuntimeContext(workspace_id=workspace_id, run_id=run_id, job_id=job_id or None, module="workflow", requested_by="job_runner", approval_id=approval_id or None),
            )
        except Exception as exc:
            entry = {
                "node_id": node_id, "tool_id": node["tool_id"], "status": "failed",
                "summary": "工具执行异常", "errors": [str(exc)[:500]],
                "started_at": started_at, "finished_at": now_iso(),
            }
            return entry, {}, False
        decision = getattr(result, "policy_decision", None)
        if bool(getattr(decision, "requires_approval", False)) and not (approvals or {}).get(node_id):
            approval_id = _create_workflow_approval(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                run_id=run_id,
                job_id=job_id,
                node=node,
                arguments=arguments,
                risk_level=str(getattr(decision, "risk_level", "high") or "high"),
                description=str(result.summary or "Workflow action requires approval"),
            )
            return {
                "node_id": node_id,
                "tool_id": node["tool_id"],
                "status": "awaiting_approval",
                "summary": str(result.summary or "Approval required")[:1000],
                "approval_id": approval_id,
                "errors": ["approval_required"],
                "started_at": started_at,
                "finished_at": now_iso(),
            }, {}, False
        success = result.status in {"succeeded", "dry_run"}
        entry = {
            "node_id": node_id,
            "tool_id": node["tool_id"],
            "status": result.status,
            "summary": str(result.summary or "")[:1000],
            "output": _safe_persisted_output(result.output or {}),
            "errors": [str(item)[:500] for item in (result.errors or [])[:20]],
            "duration_ms": result.duration_ms,
            "started_at": started_at,
            "finished_at": now_iso(),
        }
        return entry, (result.output if success else {}), success

    from core.runtime_engine.contracts import is_read_only_call
    for layer_index, layer in enumerate(layers, start=1):
        if _cancel_requested(workspace_id, run_id, job_id):
            record["status"] = "cancelled"
            break

        # Continue keeps independent branches alive; it never executes a node
        # whose declared dependency failed or was skipped.
        runnable_layer: list[str] = []
        for node_id in layer:
            if node_id in dependency_outcomes:
                continue
            failed_dependencies = [
                dependency
                for dependency in nodes_by_id[node_id].get("depends_on") or []
                if dependency_outcomes.get(dependency) is not True
            ]
            if not failed_dependencies:
                runnable_layer.append(node_id)
                continue
            record["nodes"].append({
                "node_id": node_id,
                "tool_id": nodes_by_id[node_id]["tool_id"],
                "status": "skipped",
                "summary": "依赖步骤未成功，当前步骤未执行",
                "errors": [f"failed dependencies: {failed_dependencies}"],
                "started_at": now_iso(),
                "finished_at": now_iso(),
                "orchestration": {
                    "layer": layer_index,
                    "parallel": False,
                    "depends_on": list(nodes_by_id[node_id].get("depends_on") or []),
                },
            })
            outputs[node_id] = {}
            dependency_outcomes[node_id] = False
        if len(runnable_layer) != len(layer):
            _save_run(record)

        # Independent reads run concurrently. Writes remain ordering barriers,
        # even when the saved DAG did not declare a dependency between them.
        groups: list[tuple[bool, list[str]]] = []
        read_group: list[str] = []
        for node_id in runnable_layer:
            node = nodes_by_id[node_id]
            try:
                scope = {"input": dict(inputs or {}), "nodes": {key: {"output": value} for key, value in outputs.items()}}
                preview_args = _resolve(node["arguments"], scope)
            except Exception:
                preview_args = node["arguments"]
            if is_read_only_call(node["tool_id"], preview_args):
                read_group.append(node_id)
                continue
            if read_group:
                groups.append((True, read_group)); read_group = []
            groups.append((False, [node_id]))
        if read_group:
            groups.append((True, read_group))

        for parallel, node_ids in groups:
            budget_status = budget.check_execution()
            if not budget_status.ok:
                for node_id in node_ids:
                    record["nodes"].append({
                        "node_id": node_id,
                        "tool_id": nodes_by_id[node_id]["tool_id"],
                        "status": "failed",
                        "summary": "流程执行预算已耗尽，当前步骤未执行",
                        "errors": [budget_status.exceeded],
                        "started_at": now_iso(),
                        "finished_at": now_iso(),
                        "orchestration": {
                            "layer": layer_index,
                            "parallel": False,
                            "depends_on": list(nodes_by_id[node_id].get("depends_on") or []),
                        },
                    })
                    dependency_outcomes[node_id] = False
                failed = True
                _save_run(record)
                break
            if parallel and len(node_ids) > 1:
                with ThreadPoolExecutor(max_workers=min(5, len(node_ids)), thread_name_prefix="workflow-read") as pool:
                    futures = {node_id: pool.submit(execute_node, node_id) for node_id in node_ids}
                    completed = [(node_id, futures[node_id].result()) for node_id in node_ids]
            else:
                completed = [(node_id, execute_node(node_id)) for node_id in node_ids]
            for node_id, (entry, output, success) in completed:
                entry["orchestration"] = {
                    "layer": layer_index,
                    "parallel": parallel and len(node_ids) > 1,
                    "depends_on": list(nodes_by_id[node_id].get("depends_on") or []),
                }
                record["nodes"].append(entry)
                outputs[node_id] = output
                dependency_outcomes[node_id] = bool(
                    success and entry.get("status") not in {"skipped", "cancelled"}
                )
                if entry.get("status") == "awaiting_approval":
                    record["status"] = "awaiting_approval"
                    record.setdefault("approval_ids", []).append(entry["approval_id"])
                    approval_pending = True
                elif not success:
                    failed = True
            _save_run(record)
            if approval_pending:
                break
        if approval_pending:
            break
        if failed and definition["failure_policy"] == "fail_fast":
            break
        if not budget.check_execution().ok:
            break
    budget.end_execution()
    if record["status"] == "running":
        record["status"] = "failed" if failed else "succeeded"
    if record["status"] != "awaiting_approval":
        record["finished_at"] = now_iso()
    else:
        record.pop("finished_at", None)
    _save_run(record)
    if record.get("status") == "failed":
        try:
            from storage.review_store import record_workflow_failure_review
            record_workflow_failure_review(record)
        except Exception:
            # Review intake is supplementary and must not alter the canonical run result.
            pass
    return record

def _create_workflow_approval(*, workspace_id: str, workflow_id: str, run_id: str, job_id: str, node: dict[str, Any], arguments: dict[str, Any], risk_level: str, description: str) -> str:
    """Create a durable Guardian approval from a canonical policy decision."""
    from agent.approval import get_approval_store
    request = get_approval_store(workspace_id).create(
        session_id=f"wf-{run_id}",
        tool_id=str(node["tool_id"]),
        arguments=dict(arguments),
        description=description,
        risk_level=risk_level,
        workspace_id=workspace_id,
        run_id=run_id,
        job_id=job_id,
        approval_kind="workflow",
        metadata={"workflow_id": workflow_id, "workflow_node_id": node["node_id"]},
    )
    return request.approval_id


def _workflow_approval_is_valid(
    *,
    approval_id: str,
    workspace_id: str,
    workflow_id: str,
    run_id: str,
    node: dict[str, Any],
    arguments: dict[str, Any],
) -> bool:
    from agent.approval import get_approval_store

    return get_approval_store(workspace_id).validate_resolved_approval(
        approval_id,
        workspace_id=workspace_id,
        tool_id=str(node["tool_id"]),
        arguments=arguments,
        run_id=run_id,
        metadata={"workflow_id": workflow_id, "workflow_node_id": node["node_id"]},
    )


def resume_workflow_run(workspace_id: str, run_id: str, approval_id: str) -> dict[str, Any]:
    """Resume the exact awaiting workflow run after a bound approval."""
    record = get_run(workspace_id, run_id)
    if not record or record.get("status") != "awaiting_approval":
        raise WorkflowError("workflow run is not awaiting approval")
    pending = next(
        (
            entry
            for entry in list(record.get("nodes") or [])
            if entry.get("status") == "awaiting_approval" and entry.get("approval_id") == approval_id
        ),
        None,
    )
    if pending is None:
        raise WorkflowError("approval is not bound to this workflow run")
    return execute_workflow(
        workspace_id,
        str(record.get("workflow_id") or ""),
        dict(record.get("inputs") or {}),
        approvals={str(pending["node_id"]): approval_id},
        job_id=str(record.get("job_id") or ""),
        run_id=run_id,
    )


def reject_workflow_run(workspace_id: str, run_id: str, approval_id: str) -> dict[str, Any] | None:
    """Close an awaiting workflow run when its bound approval is denied."""
    record = get_run(workspace_id, run_id)
    if not record or record.get("status") != "awaiting_approval":
        return None
    matched = False
    for entry in list(record.get("nodes") or []):
        if entry.get("status") == "awaiting_approval" and entry.get("approval_id") == approval_id:
            entry["status"] = "rejected"
            entry["errors"] = ["approval_rejected"]
            entry["finished_at"] = now_iso()
            matched = True
    if not matched:
        return None
    record["status"] = "rejected"
    record["finished_at"] = now_iso()
    _save_run(record)
    return record

def _tool_client():
    from core.tools.integration import get_default_tool_runtime_client
    return get_default_tool_runtime_client()


def _safe_persisted_output(output: dict[str, Any]) -> dict[str, Any]:
    from core.runtime_engine.context_budget import project_json_to_tokens
    from storage.redaction import redact_dict
    safe, truncated = project_json_to_tokens(redact_dict(output), max_tokens=12_000)
    if not isinstance(safe, dict): safe = {"value": safe}
    if truncated: safe["_workflow_projection"] = {"truncated": True}
    return safe


_TEMPLATE = re.compile(r"\$\{([^{}]+)\}")


def _lookup(scope: dict[str, Any], expression: str) -> Any:
    value: Any = scope
    for part in expression.split("."):
        if not isinstance(value, dict) or part not in value:
            raise WorkflowError(f"workflow template value not found: {expression}")
        value = value[part]
    return value


def _resolve(value: Any, scope: dict[str, Any]) -> Any:
    if isinstance(value, dict): return {key: _resolve(item, scope) for key, item in value.items()}
    if isinstance(value, list): return [_resolve(item, scope) for item in value]
    if not isinstance(value, str): return value
    exact = _TEMPLATE.fullmatch(value)
    if exact: return _lookup(scope, exact.group(1))
    return _TEMPLATE.sub(lambda match: str(_lookup(scope, match.group(1))), value)


def _save_run(record: dict[str, Any]) -> None:
    record["updated_at"] = now_iso()
    path = _run_path(record["workspace_id"], record["run_id"])
    with FileLock(path.with_suffix(".lock")):
        # Cancellation is a monotonic signal written by another request. A
        # workflow step may still hold an older in-memory snapshot, so merge
        # the durable flag while holding the same run lock before replacing
        # the record. Otherwise a normal progress save can resurrect a run
        # immediately after the user cancelled it.
        if path.is_file():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                current = {}
            if isinstance(current, dict) and current.get("cancel_requested"):
                record["cancel_requested"] = True
        atomic_write_json(path, record)


def get_run(workspace_id: str, run_id: str) -> dict[str, Any] | None:
    path = _run_path(workspace_id, run_id)
    if not path.is_file(): return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError): return None


def list_runs(workspace_id: str, workflow_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
    root = workspace_record_dir(workspace_id, "workflows", "runs", create=False)
    if not root.is_dir(): return []
    records = []
    for path in sorted(root.glob("*.json"), reverse=True):
        value = get_run(workspace_id, path.stem)
        if value and (not workflow_id or value.get("workflow_id") == workflow_id): records.append(value)
        if len(records) >= max(1, min(limit, 500)): break
    return records


def cancel_run(workspace_id: str, run_id: str) -> dict[str, Any]:
    record = get_run(workspace_id, run_id)
    if not record: raise WorkflowError("workflow run not found")
    if record.get("status") in {"running", "queued"}:
        record["cancel_requested"] = True
        _save_run(record)
    elif record.get("status") == "awaiting_approval":
        record["cancel_requested"] = True
        record["status"] = "cancelled"
        record["finished_at"] = now_iso()
        _save_run(record)
    try:
        from storage.review_store import record_workflow_failure_review
        record_workflow_failure_review(record)
    except Exception:
        # Review intake is supplementary; it must not change canonical run state.
        pass
    return record


def _cancel_requested(workspace_id: str, run_id: str, job_id: str) -> bool:
    run = get_run(workspace_id, run_id)
    if run and run.get("cancel_requested"): return True
    if job_id:
        from jobs.store import get_job
        job = get_job(workspace_id, job_id)
        return bool(job and job.cancel_requested)
    return False
