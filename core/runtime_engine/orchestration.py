"""Dynamic, incremental tool-call orchestration for the SSOT QueryLoop.

The LLM may annotate an ordinary function call with a stable step id,
dependencies and result bindings.  Calls without annotations remain simple
single-node calls.  The runtime validates the resulting *incremental* graph;
it never asks the model to commit to a complete plan up front.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable


STEP_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
REFERENCE_RE = re.compile(
    r"^(?:steps\.)?([A-Za-z][A-Za-z0-9_-]{0,63})(?:\.output)?(?:\.(.+))?$"
)
ORCHESTRATION_ARGUMENTS = frozenset({
    "plan_step_id", "plan_depends_on", "plan_bindings", "plan_failure",
})
ALLOWED_FAILURE_POLICIES = frozenset({"replan", "stop", "continue"})


class OrchestrationError(ValueError):
    """A model-produced execution graph is invalid or unsafe to execute."""


@dataclass(frozen=True)
class StepEvidence:
    step_id: str
    call_id: str
    tool_id: str
    ok: bool
    output: dict[str, Any]
    error: str = ""


def extract_orchestration(arguments: dict[str, Any], fallback_step_id: str) -> tuple[dict[str, Any], str, list[str], dict[str, str], str]:
    """Remove runtime-only plan fields from handler arguments."""
    cleaned = dict(arguments or {})
    raw_step_id = cleaned.pop("plan_step_id", "")
    raw_depends = cleaned.pop("plan_depends_on", [])
    raw_bindings = cleaned.pop("plan_bindings", {})
    raw_failure = cleaned.pop("plan_failure", "replan")

    if raw_step_id:
        step_id = str(raw_step_id).strip()
    else:
        safe_fallback = re.sub(r"[^A-Za-z0-9_-]", "_", str(fallback_step_id or "step"))[:56]
        if not safe_fallback or not safe_fallback[0].isalpha():
            safe_fallback = f"step_{safe_fallback}"
        step_id = safe_fallback
    depends_on = [str(item).strip() for item in raw_depends] if isinstance(raw_depends, list) else []
    bindings = (
        {str(key).strip(): str(value).strip() for key, value in raw_bindings.items()}
        if isinstance(raw_bindings, dict) else {}
    )
    failure_policy = str(raw_failure or "replan").strip().lower()
    return cleaned, step_id, depends_on, bindings, failure_policy


def validate_incremental_graph(calls: Iterable[Any], prior: dict[str, StepEvidence] | None = None) -> list[list[str]]:
    """Validate one LLM call batch and return stable topological layers."""
    prior = prior or {}
    calls = list(calls)
    step_ids = [str(getattr(call, "step_id", "") or getattr(call, "id", "")) for call in calls]
    if len(step_ids) != len(set(step_ids)):
        raise OrchestrationError("duplicate plan_step_id in tool-call batch")
    reused = set(step_ids) & set(prior)
    reused_successful = {
        step_id for step_id in reused
        if bool(getattr(prior.get(step_id), "ok", False))
    }
    if reused_successful:
        raise OrchestrationError(
            f"plan_step_id already succeeded: {sorted(reused_successful)}"
        )
    for step_id in step_ids:
        if not STEP_ID_RE.fullmatch(step_id):
            raise OrchestrationError(f"invalid plan_step_id: {step_id}")

    current = set(step_ids)
    dependencies: dict[str, set[str]] = {}
    for call, step_id in zip(calls, step_ids):
        depends = list(getattr(call, "depends_on", None) or [])
        if len(depends) != len(set(depends)):
            raise OrchestrationError(f"duplicate dependency for step {step_id}")
        unknown = set(depends) - current - set(prior)
        if unknown:
            raise OrchestrationError(f"step {step_id} has unknown dependencies: {sorted(unknown)}")
        if step_id in depends:
            raise OrchestrationError(f"step {step_id} depends on itself")
        policy = str(getattr(call, "failure_policy", "replan") or "replan")
        if policy not in ALLOWED_FAILURE_POLICIES:
            raise OrchestrationError(f"invalid plan_failure for step {step_id}: {policy}")
        bindings = dict(getattr(call, "result_bindings", None) or {})
        for target, reference in bindings.items():
            if not target or "." in target:
                raise OrchestrationError(f"invalid binding target for step {step_id}: {target}")
            source_id, _ = parse_reference(reference)
            if source_id not in set(depends):
                raise OrchestrationError(
                    f"binding source {source_id} must be declared in plan_depends_on for step {step_id}"
                )
            if not safe_binding_target(
                getattr(call, "name", ""),
                (getattr(call, "arguments", None) or {}).get("action", ""),
                target,
            ):
                raise OrchestrationError(
                    f"unsafe binding target for {getattr(call, 'name', '')}: {target}"
                )
        dependencies[step_id] = set(depends) & current

    layers: list[list[str]] = []
    pending = {key: set(value) for key, value in dependencies.items()}
    original_order = {step_id: index for index, step_id in enumerate(step_ids)}
    while pending:
        ready = sorted(
            (step_id for step_id, deps in pending.items() if not deps),
            key=original_order.__getitem__,
        )
        if not ready:
            raise OrchestrationError("tool-call dependency graph contains a cycle")
        layers.append(ready)
        for step_id in ready:
            pending.pop(step_id)
        for deps in pending.values():
            deps.difference_update(ready)
    return layers


def parse_reference(reference: str) -> tuple[str, list[str]]:
    match = REFERENCE_RE.fullmatch(str(reference or "").strip())
    if not match:
        raise OrchestrationError(f"invalid result reference: {reference}")
    path = [part for part in str(match.group(2) or "").split(".") if part]
    return match.group(1), path


def resolve_bindings(arguments: dict[str, Any], bindings: dict[str, str], evidence: dict[str, StepEvidence]) -> dict[str, Any]:
    resolved = dict(arguments or {})
    for target, reference in bindings.items():
        source_id, path = parse_reference(reference)
        source = evidence.get(source_id)
        if source is None:
            raise OrchestrationError(f"binding source not available: {source_id}")
        if not source.ok:
            raise OrchestrationError(f"binding source failed: {source_id}")
        projection = source.output.get("_evidence_projection") if isinstance(source.output, dict) else None
        if isinstance(projection, dict) and projection.get("truncated"):
            raise OrchestrationError(
                f"binding source was truncated: {source_id}; request a narrower source result"
            )
        value: Any = source.output
        for part in path:
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                value = value[int(part)]
            else:
                raise OrchestrationError(f"binding path not found: {reference}")
        resolved[target] = value
    return resolved


def safe_binding_target(tool_id: str, action: str, target: str) -> bool:
    """Only allow bound values where they cannot bypass write/risk review."""
    normalized = str(tool_id or "").replace("__", ".")
    action = str(action or "").lower()
    if normalized == "exec.run":
        return action == "python" and target == "input_data"
    if normalized == "data.manage":
        return target in {"text", "rows", "right_text", "right_rows"}
    if normalized == "text.analyze":
        return target == "text"
    return False
