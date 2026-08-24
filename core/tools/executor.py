# core/tools/executor.py
"""ToolExecutor — execute a ToolInvocation through the full pipeline.

Pipeline:
  1. Validate invocation + lookup ToolSpec
  2. Validate arguments against input_schema
  3. Run ToolPolicy.check()
  4. If blocked → return ToolResult("blocked")
  5. If dry_run and supported → execute dry-run handler or return early
  6. Execute handler
  7. Redact output
  8. Build audit metadata
  9. Return structured ToolResult
"""

import time
from copy import deepcopy
from core.tools.schemas import ToolSpec, ToolInvocation, ToolResult, PolicyDecision
from core.tools.registry import ToolRegistry
from core.tools.policy import ToolPolicy
from core.tools.redaction import redact_tool_output
from core.tools.audit import build_audit_event


class ToolExecutor:
    """Execute a single tool invocation with full safety pipeline."""

    def __init__(self, registry: ToolRegistry, policy: ToolPolicy = None):
        self.registry = registry
        self.policy = policy or ToolPolicy()

    def execute(self, invocation: ToolInvocation) -> ToolResult:
        """Execute a tool invocation through the full pipeline.

        Returns a structured ToolResult. Never raises — all errors are captured.
        """
        start_time = time.time()

        # ── 1. Validate invocation ──
        if not invocation.tool_id:
            return _failed_result(invocation.invocation_id, "", "Missing tool_id", 0)

        # ── 2. Lookup ToolSpec ──
        spec = self.registry.get_tool(invocation.tool_id)
        if spec is None:
            return _failed_result(
                invocation.invocation_id, invocation.tool_id,
                f"Tool not found: {invocation.tool_id}",
                int((time.time() - start_time) * 1000),
            )

        # ── 3. Validate arguments against schema ──
        schema_errors = _validate_arguments(invocation.arguments, spec.input_schema)
        if schema_errors:
            return ToolResult(
                invocation_id=invocation.invocation_id,
                tool_id=invocation.tool_id,
                status="blocked",
                output={
                    "ok": False,
                    "executed": False,
                    "error": schema_errors[0],
                    "errors": schema_errors,
                    "error_code": "TOOL_ARGUMENT_VALIDATION_FAILED",
                    "retryable": False,
                },
                summary=f"Schema validation failed: {', '.join(schema_errors)}",
                errors=schema_errors,
                duration_ms=int((time.time() - start_time) * 1000),
                redacted=True,
                policy_decision=PolicyDecision(allowed=False, reason="schema_validation_failed",
                                               risk_level=spec.risk_level,
                                               blocked_rules=["schema_validation"]),
            )

        # ── 4. Policy check ──
        decision = self.policy.check(spec, invocation)
        if not decision.allowed:
            return ToolResult(
                invocation_id=invocation.invocation_id,
                tool_id=invocation.tool_id,
                status="blocked",
                summary=f"Blocked by policy: {decision.reason}",
                errors=[decision.reason],
                duration_ms=int((time.time() - start_time) * 1000),
                redacted=True,
                policy_decision=decision,
            )
        if decision.requires_approval and not invocation.approval_id:
            return ToolResult(
                invocation_id=invocation.invocation_id,
                tool_id=invocation.tool_id,
                status="blocked",
                output={
                    "ok": False,
                    "error": "approval_required",
                    "requires_approval": True,
                    "risk_level": decision.risk_level,
                },
                summary=f"Approval required before executing {invocation.tool_id}: {decision.reason}",
                errors=["approval_required"],
                duration_ms=int((time.time() - start_time) * 1000),
                redacted=True,
                policy_decision=PolicyDecision(
                    allowed=False,
                    reason=decision.reason or "approval_required",
                    risk_level=decision.risk_level,
                    blocked_rules=[*decision.blocked_rules, "approval_required"],
                    requires_approval=True,
                ),
            )

        if decision.requires_approval and invocation.approval_id:
            # approval_id is a durable reference, never a bearer capability.
            # Validate the resolved record at the last canonical execution gate
            # so a value injected into ToolRuntimeContext cannot authorize a
            # different workspace, run, tool or argument payload.
            try:
                from agent.approval import get_approval_store
                approval_valid = get_approval_store(str(invocation.workspace_id or "")).validate_resolved_approval(
                    str(invocation.approval_id),
                    workspace_id=str(invocation.workspace_id or ""),
                    tool_id=invocation.tool_id,
                    arguments=dict(invocation.arguments or {}),
                    # A continuation executes in a new run, but the durable
                    # approval is bound to the parent run that created it.
                    # Keep the current run id for audit; use only the explicit
                    # server-provided approval binding id for validation.
                    run_id=str(invocation.approval_run_id or invocation.run_id or ""),
                )
            except Exception:
                approval_valid = False
            if not approval_valid:
                return ToolResult(
                    invocation_id=invocation.invocation_id,
                    tool_id=invocation.tool_id,
                    status="blocked",
                    output={
                        "ok": False,
                        "error": "invalid_approval_binding",
                        "requires_approval": True,
                        "risk_level": decision.risk_level,
                    },
                    summary=f"Approval binding is invalid for {invocation.tool_id}",
                    errors=["invalid_approval_binding"],
                    duration_ms=int((time.time() - start_time) * 1000),
                    redacted=True,
                    policy_decision=PolicyDecision(
                        allowed=False,
                        reason="invalid_approval_binding",
                        risk_level=decision.risk_level,
                        blocked_rules=[*decision.blocked_rules, "invalid_approval_binding"],
                        requires_approval=True,
                    ),
                )

        # ── 5. Handle dry_run ──
        if invocation.dry_run and spec.dry_run_supported:
            # Tools that support dry_run should implement their own handler logic.
            # If the handler returns a dict with "dry_run" key, the executor
            # treats it as dry-run output.
            handler = self.registry.get_handler(invocation.tool_id)
            if handler is None:
                return _failed_result(
                    invocation.invocation_id, invocation.tool_id,
                    "Handler not found for dry_run",
                    int((time.time() - start_time) * 1000),
                )
            try:
                raw = handler(invocation)
                # Redact output
                raw_safe = redact_tool_output(raw) if isinstance(raw, dict) else redact_tool_output({"output": str(raw)})
                duration = int((time.time() - start_time) * 1000)
                result = ToolResult(
                    invocation_id=invocation.invocation_id,
                    tool_id=invocation.tool_id,
                    status="dry_run",
                    output=raw_safe,
                    summary=raw_safe.get("summary", f"dry_run completed for {invocation.tool_id}"),
                    duration_ms=duration,
                    redacted=True,
                    policy_decision=decision,
                )
                return result
            except Exception as exc:
                return _failed_result(
                    invocation.invocation_id, invocation.tool_id,
                    f"dry_run failed: {str(exc)[:200]}",
                    int((time.time() - start_time) * 1000),
                )

        # ── 6. Execute handler ──
        handler = self.registry.get_handler(invocation.tool_id)
        if handler is None:
            return _failed_result(
                invocation.invocation_id, invocation.tool_id,
                "Handler not found",
                int((time.time() - start_time) * 1000),
            )

        try:
            raw = handler(invocation)
        except Exception as exc:
            return _failed_result(
                invocation.invocation_id, invocation.tool_id,
                f"Execution failed: {str(exc)[:200]}",
                int((time.time() - start_time) * 1000),
            )

        # ── 7. Redact output ──
        output = redact_tool_output(raw) if isinstance(raw, dict) else redact_tool_output({"output": str(raw)})

        duration = int((time.time() - start_time) * 1000)

        # ── 8. Build result ──
        ok = output.get("ok", True)  # v1.0.3.5: check ok to propagate errors
        summary = (
            output.get("summary")
            or output.get("_hint")
            or _structured_summary(invocation.tool_id, output, ok)
        )
        # No per-field truncation — query_loop enforces a single 50K cap on the full payload.

        errors = output.get("errors", [])
        if not ok and not errors:
            errors = [output.get("error", summary)]

        result = ToolResult(
            invocation_id=invocation.invocation_id,
            tool_id=invocation.tool_id,
            status="succeeded" if ok else "failed",
            output=output,
            summary=summary,
            warnings=output.get("warnings", []),
            errors=errors,
            artifact_ids=output.get("artifact_ids", []),
            duration_ms=duration,
            redacted=True,
            policy_decision=decision,
        )

        return result


def _structured_summary(tool_id: str, output: dict, ok: bool) -> str:
    """Produce an evidence-bearing fallback for raw structured handlers."""
    if not ok:
        return str(output.get("error") or f"Tool {tool_id} failed")[:500]
    for count_key, noun in (
        ("match_count", "match(es)"),
        ("row_count", "row(s)"),
        ("count", "item(s)"),
    ):
        if count_key in output:
            return f"{tool_id} returned {output[count_key]} {noun}."
    return f"{tool_id} completed with structured output."


def canonicalize_tool_arguments(arguments: dict, input_schema: dict) -> dict:
    """Return a copied, default-expanded argument projection for one ToolSpec.

    This is a pure schema projection: it neither authorizes nor executes a
    tool.  ToolExecutor remains the only validation and execution gate.
    """
    canonical = deepcopy(arguments or {})
    _validate_arguments(canonical, input_schema or {})
    return canonical


def _validate_arguments(arguments: dict, schema: dict) -> list:
    """Validate arguments against a practical JSON Schema subset.

    Covers: required, closed-object fields, type, enum, numeric range,
    string/array cardinality, array items, and nested object properties.
    Returns list of error strings (empty = valid).
    """
    errors = []
    if not schema:
        return errors

    required = schema.get("required", [])
    properties = schema.get("properties", {})

    if schema.get("additionalProperties") is False:
        for field in arguments:
            if field not in properties:
                errors.append(f"Unknown field: '{field}'")

    # Check required fields
    for field in required:
        if field not in arguments:
            errors.append(f"Missing required field: '{field}'")
            continue
        _validate_field(field, arguments[field], properties.get(field, {}), errors)

    # Check non-required fields that are present
    for field, value in arguments.items():
        if field in properties and field not in required:
            _validate_field(field, value, properties[field], errors)

    # Apply defaults for missing fields
    for field, field_schema in properties.items():
        if field not in arguments and "default" in field_schema:
            arguments[field] = field_schema["default"]

    return errors


def validate_schema_value(field: str, value, schema: dict) -> list[str]:
    """Validate one value with the same recursive schema rules as execution."""
    errors: list[str] = []
    _validate_field(field, value, schema or {}, errors)
    return errors


def _validate_field(field: str, value, field_schema: dict, errors: list):
    """Validate a single field against its schema definition."""
    alternatives = field_schema.get("oneOf")
    if isinstance(alternatives, list) and alternatives:
        alternative_errors = []
        for alternative in alternatives:
            candidate_errors: list[str] = []
            _validate_field(field, value, alternative if isinstance(alternative, dict) else {}, candidate_errors)
            if not candidate_errors:
                return
            alternative_errors.append(candidate_errors)
        errors.append(
            f"Field '{field}' does not match any allowed schema: "
            + " | ".join("; ".join(candidate[:3]) for candidate in alternative_errors)
        )
        return
    expected_type = field_schema.get("type", "")

    # ── Type check ──
    if expected_type == "string" and not isinstance(value, str):
        errors.append(f"Field '{field}' expected string, got {type(value).__name__}")
        return
    elif expected_type == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        errors.append(f"Field '{field}' expected number, got {type(value).__name__}")
        return
    elif expected_type == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        errors.append(f"Field '{field}' expected integer, got {type(value).__name__}")
        return
    elif expected_type == "boolean" and not isinstance(value, bool):
        errors.append(f"Field '{field}' expected boolean, got {type(value).__name__}")
        return
    elif expected_type == "object" and not isinstance(value, dict):
        errors.append(f"Field '{field}' expected object, got {type(value).__name__}")
        return
    elif expected_type == "array" and not isinstance(value, list):
        errors.append(f"Field '{field}' expected array, got {type(value).__name__}")
        return

    # ── Enum check ──
    allowed = field_schema.get("enum")
    if allowed is not None:
        if value not in allowed:
            errors.append(
                f"Field '{field}' value '{value}' not in allowed: {allowed}"
            )

    # ── Range checks (integer/number) ──
    if expected_type in ("integer", "number") and isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in field_schema and value < field_schema["minimum"]:
            errors.append(
                f"Field '{field}' value {value} below minimum {field_schema['minimum']}"
            )
        if "maximum" in field_schema and value > field_schema["maximum"]:
            errors.append(
                f"Field '{field}' value {value} above maximum {field_schema['maximum']}"
            )

    # ── String length checks ──
    if expected_type == "string" and isinstance(value, str):
        if "minLength" in field_schema and len(value) < field_schema["minLength"]:
            errors.append(
                f"Field '{field}' length {len(value)} below minimum {field_schema['minLength']}"
            )
        if "maxLength" in field_schema and len(value) > field_schema["maxLength"]:
            errors.append(
                f"Field '{field}' length {len(value)} above maximum {field_schema['maxLength']}"
            )

    # ── Array items type check ──
    if expected_type == "array" and isinstance(value, list):
        if "minItems" in field_schema and len(value) < field_schema["minItems"]:
            errors.append(
                f"Field '{field}' has {len(value)} item(s), below minimum {field_schema['minItems']}"
            )
        if "maxItems" in field_schema and len(value) > field_schema["maxItems"]:
            errors.append(
                f"Field '{field}' has {len(value)} item(s), above maximum {field_schema['maxItems']}"
            )
        items_schema = field_schema.get("items")
        if isinstance(items_schema, dict) and items_schema:
            for i, item in enumerate(value):
                _validate_field(f"{field}[{i}]", item, items_schema, errors)

    # ── Nested object properties check ──
    if expected_type == "object" and isinstance(value, dict):
        nested_props = field_schema.get("properties")
        if isinstance(nested_props, dict):
            if field_schema.get("additionalProperties") is False:
                for nested_field in value:
                    if nested_field not in nested_props:
                        errors.append(f"Unknown field: '{field}.{nested_field}'")
            for nested_field in field_schema.get("required", []):
                if nested_field not in value:
                    errors.append(f"Missing required field: '{field}.{nested_field}'")
            for nf, nv in value.items():
                if nf in nested_props:
                    _validate_field(f"{field}.{nf}", nv, nested_props[nf], errors)
            for dependency, dependents in (field_schema.get("dependentRequired") or {}).items():
                if dependency not in value:
                    continue
                for dependent in dependents if isinstance(dependents, list) else []:
                    if dependent not in value:
                        errors.append(
                            f"Field '{field}.{dependency}' requires '{field}.{dependent}'"
                        )

    # ── Array type check ──
    if expected_type == "array" and not isinstance(value, list):
        errors.append(f"Field '{field}' expected array, got {type(value).__name__}")

    return errors


def _failed_result(invocation_id: str, tool_id: str, error: str, duration_ms: int) -> ToolResult:
    """Build a standard failure result with redacted error text."""
    safe_error = str(redact_tool_output({"error": str(error or "")}).get("error") or "Tool execution failed")
    return ToolResult(
        invocation_id=invocation_id,
        tool_id=tool_id,
        status="failed",
        summary=safe_error[:200],
        errors=[safe_error[:200]],
        duration_ms=duration_ms,
        redacted=True,
    )
