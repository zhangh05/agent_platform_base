"""Validated DAG workflows executed through the governed Tool Runtime."""

from __future__ import annotations

import json
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
    if not isinstance(payload, dict):
        raise WorkflowError("workflow definition must be an object")
    workflow_id = _id(str(payload.get("workflow_id") or f"workflow_{uuid.uuid4().hex[:8]}"))
    name = str(payload.get("name") or "").strip()
    if not name:
        raise WorkflowError("workflow name is required")
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 50:
        raise WorkflowError("workflow must contain 1 to 50 nodes")
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
    order = _topological_order(normalized)
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
    }


def _topological_order(nodes: list[dict[str, Any]]) -> list[str]:
    dependencies = {node["node_id"]: set(node["depends_on"]) for node in nodes}
    order: list[str] = []
    while dependencies:
        ready = sorted(node_id for node_id, required in dependencies.items() if not required)
        if not ready:
            raise WorkflowError("workflow graph contains a cycle")
        order.extend(ready)
        for node_id in ready:
            dependencies.pop(node_id)
        for required in dependencies.values():
            required.difference_update(ready)
    return order


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
    if isinstance(value, dict):
        for item_key, item in value.items(): _reject_static_secrets(item, node_id, str(item_key))
        return
    if isinstance(value, list):
        for item in value: _reject_static_secrets(item, node_id, key)
        return
    normalized = key.lower().replace("-", "_")
    secret_key = any(marker in normalized for marker in ("password", "api_key", "token", "authorization", "private_key")) and not normalized.endswith("_ref")
    if secret_key and not (isinstance(value, str) and _TEMPLATE.search(value)):
        raise WorkflowError(f"node {node_id} contains a static secret; use runtime input or a secret reference")


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


def archive_workflow(workspace_id: str, workflow_id: str) -> dict[str, Any]:
    current = get_workflow(workspace_id, workflow_id)
    if not current: raise WorkflowError("workflow not found")
    current["status"] = "archived"
    current["updated_at"] = now_iso()
    path = _definition_path(workspace_id, workflow_id)
    with FileLock(path.with_suffix(".lock")):
        atomic_write_json(path, current)
    return current


def execute_workflow(workspace_id: str, workflow_id: str, inputs: dict[str, Any] | None = None, *, approvals: dict[str, str] | None = None, job_id: str = "", run_id: str = "") -> dict[str, Any]:
    definition = get_workflow(workspace_id, workflow_id)
    if not definition or definition.get("status") != "active":
        raise WorkflowError("workflow not found or inactive")
    definition = validate_definition(definition)
    if not isinstance(inputs or {}, dict):
        raise WorkflowError("workflow inputs must be an object")
    if len(json.dumps(inputs or {}, ensure_ascii=False, default=str).encode()) > 1_048_576:
        raise WorkflowError("workflow inputs are too large")
    run_id = _id(run_id or f"wfrun_{uuid.uuid4().hex[:12]}", "run_id")
    from storage.redaction import redact_dict
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
    outputs: dict[str, Any] = {}
    nodes_by_id = {node["node_id"]: node for node in definition["nodes"]}
    failed = False
    for node_id in definition["execution_order"]:
        if _cancel_requested(workspace_id, run_id, job_id):
            record["status"] = "cancelled"
            break
        node = nodes_by_id[node_id]
        try:
            scope = {"input": dict(inputs or {}), "nodes": {key: {"output": value} for key, value in outputs.items()}}
            if not bool(_resolve(node.get("when", True), scope)):
                record["nodes"].append({"node_id": node_id, "tool_id": node["tool_id"], "status": "skipped", "started_at": now_iso(), "finished_at": now_iso()})
                _save_run(record)
                continue
            arguments = _resolve(node["arguments"], scope)
            if len(json.dumps(arguments, ensure_ascii=False, default=str).encode()) > 1_048_576:
                raise WorkflowError(f"resolved arguments are too large: {node_id}")
        except Exception as exc:
            failed = True
            record["nodes"].append({"node_id": node_id, "tool_id": node["tool_id"], "status": "failed", "summary": "步骤输入解析失败", "errors": [str(exc)[:500]], "started_at": now_iso(), "finished_at": now_iso()})
            _save_run(record)
            if definition["failure_policy"] == "fail_fast": break
            continue
        started_at = now_iso()
        from core.tools.context import ToolRuntimeContext
        result = _tool_client().invoke(
            node["tool_id"],
            arguments,
            context=ToolRuntimeContext(workspace_id=workspace_id, run_id=run_id, job_id=job_id or None, module="workflow", requested_by="job_runner", approval_id=(approvals or {}).get(node_id)),
        )
        success = result.status in {"succeeded", "dry_run"}
        outputs[node_id] = result.output if success else {}
        record["nodes"].append({
            "node_id": node_id,
            "tool_id": node["tool_id"],
            "status": result.status,
            "summary": str(result.summary or "")[:1000],
            "output": _safe_persisted_output(result.output or {}),
            "errors": [str(item)[:500] for item in (result.errors or [])[:20]],
            "duration_ms": result.duration_ms,
            "started_at": started_at,
            "finished_at": now_iso(),
        })
        _save_run(record)
        if not success:
            failed = True
            if definition["failure_policy"] == "fail_fast": break
    if record["status"] == "running":
        record["status"] = "failed" if failed else "succeeded"
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
    return record


def _cancel_requested(workspace_id: str, run_id: str, job_id: str) -> bool:
    run = get_run(workspace_id, run_id)
    if run and run.get("cancel_requested"): return True
    if job_id:
        from jobs.store import get_job
        job = get_job(workspace_id, job_id)
        return bool(job and job.cancel_requested)
    return False
