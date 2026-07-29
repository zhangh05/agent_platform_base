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
                output={"ok": False, "error": schema_errors[0], "errors": schema_errors},
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
        output = redact_tool_output(raw) if isinstance(raw, dict) else {"output": str(raw)}

        duration = int((time.time() - start_time) * 1000)

        # ── 8. Build result ──
        ok = output.get("ok", True)  # v1.0.3.5: check ok to propagate errors
        summary = output.get("summary", f"Tool {invocation.tool_id} {'completed' if ok else 'failed'}")
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


def _validate_arguments(arguments: dict, schema: dict) -> list:
    """Validate arguments against a practical JSON Schema subset.

    Covers: required, type, enum, integer range (min/max), string length
    (minLength/maxLength), array items type, and nested object properties.
    Returns list of error strings (empty = valid).
    """
    errors = []
    if not schema:
        return errors

    required = schema.get("required", [])
    properties = schema.get("properties", {})

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


def _validate_field(field: str, value, field_schema: dict, errors: list):
    """Validate a single field against its schema definition."""
    expected_type = field_schema.get("type", "")

    # ── Type check ──
    if expected_type == "string" and not isinstance(value, str):
        errors.append(f"Field '{field}' expected string, got {type(value).__name__}")
        return
    elif expected_type == "number" and not isinstance(value, (int, float)):
        errors.append(f"Field '{field}' expected number, got {type(value).__name__}")
        return
    elif expected_type == "integer" and not isinstance(value, int):
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
    if expected_type in ("integer", "number") and isinstance(value, (int, float)):
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
        items_schema = field_schema.get("items")
        if isinstance(items_schema, dict) and "type" in items_schema:
            items_type = items_schema["type"]
            for i, item in enumerate(value):
                if items_type == "string" and not isinstance(item, str):
                    errors.append(f"Field '{field}' item[{i}] expected string")
                elif items_type == "number" and not isinstance(item, (int, float)):
                    errors.append(f"Field '{field}' item[{i}] expected number")

    # ── Nested object properties check ──
    if expected_type == "object" and isinstance(value, dict):
        nested_props = field_schema.get("properties")
        if isinstance(nested_props, dict):
            for nf, nv in value.items():
                if nf in nested_props:
                    _validate_field(f"{field}.{nf}", nv, nested_props[nf], errors)

    # ── Array type check ──
    if expected_type == "array" and not isinstance(value, list):
        errors.append(f"Field '{field}' expected array, got {type(value).__name__}")

    return errors


def _failed_result(invocation_id: str, tool_id: str, error: str, duration_ms: int) -> ToolResult:
    """Build a standard failure result."""
    return ToolResult(
        invocation_id=invocation_id,
        tool_id=tool_id,
        status="failed",
        summary=error[:200],
        errors=[error[:200]],
        duration_ms=duration_ms,
        redacted=True,
    )
