"""
Stateless Tool Runtime — pure function style execution.

Key rules:
  - Tool must be pure function style: execute_tool(name, args) → result
  - No hidden shared state
  - No implicit context access
  - All independent executions are concurrent

v4 contract (runtime_contracts.ExecutionContract.TOOL_TRUTH_SINGLE_SOURCE):

  Every tool handler return value MUST pass through
  ``resolve_tool_outcome`` before it lands in a ``ToolResult``.
  No code path may construct ``ToolResult(success=True, ...)``
  without the resolver's verdict. This is the single source of
  truth for "did the tool actually succeed?" — the previous
  helper trio (``_resolve_success_flag`` / ``_resolve_error_code``
  / ``_resolve_error_message``) was consolidated into
  ``resolve_tool_outcome`` so the contract has exactly one
  enforcement point.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from .models import ExecutionNode, ExecutionStatus, SSOTRuntimeConfig, StatelessContext, ToolResult
from .runtime_contracts import (
    ErrorCode,
    ExecutionContract,
    ExecutionSemanticsContract,
    assert_error_code_usage,
)

ToolHandler = Callable[[dict[str, Any]], Any | Awaitable[Any]]


# ── v4.2: use typed ErrorCode enum ──────────────────────────────────

# Handlers that explicitly report ok=False but provide no code.
_HANDLER_NOT_OK_DEFAULT: ErrorCode = ErrorCode.TOOL_RETURNED_NOT_OK
# Handler returned None (no value at all).
_NULL_RESULT_DEFAULT: ErrorCode = ErrorCode.NULL_RESULT


def _code_val(c: ErrorCode) -> str:
    return c.value

# Outcome status codes.
_STATUS_SUCCESS = "SUCCESS"
_STATUS_FAIL = "FAIL"


def resolve_tool_outcome(result: Any) -> tuple[str, str | None, Any]:
    """v4 single-source tool truth resolver.

    Returns a 3-tuple ``(status, error_code, normalized)`` where:

      * ``status`` is ``"SUCCESS"`` or ``"FAIL"`` — the boolean
        verdict of the handler's return value.
      * ``error_code`` is a non-empty string on FAIL and ``None``
        on SUCCESS. Empty codes are normalized to the current runtime default.
      * ``normalized`` is the value the ``ToolResult.data`` field
        should hold. For dicts this is the original result; for
        ``None`` it is an empty dict so ``ToolResult.data`` is
        always a defined value (never ``None``).

    Resolution order (per v4 spec):

      1. ``result is None`` → FAIL / NULL_RESULT / {}.
      2. ``result["ok"] is False`` → FAIL / result.error_code or
         ``TOOL_RETURNED_NOT_OK`` / result.
      3. ``result["ok"] is True`` → SUCCESS / None / result.
      4. Any other shape → FAIL / TOOL_RESULT_INVALID.

    This function is the ONLY function in the v4 runtime that
    decides whether a tool call succeeded. Every
    ``ToolResult(success=...)`` field is filled by feeding the
    handler return value through this resolver and mapping the
    status to a boolean.
    """
    if result is None:
        return _STATUS_FAIL, _code_val(_NULL_RESULT_DEFAULT), {}

    if isinstance(result, dict):
        # ok=False is the strongest failure signal — preserve the
        # handler's error_code when present.
        if result.get("ok") is False:
            code = result.get("error_code") or _code_val(_HANDLER_NOT_OK_DEFAULT)
            return _STATUS_FAIL, str(code), result

        # ok=True is the strongest success signal.
        if result.get("ok") is True:
            return _STATUS_SUCCESS, None, result

        return _STATUS_FAIL, _code_val(ErrorCode.TOOL_RESULT_INVALID), result

    return _STATUS_FAIL, _code_val(ErrorCode.TOOL_RESULT_INVALID), result


def extract_error(result: Any) -> str:
    """v4 single-source error string extractor.

    Resolution order:

      1. ``result["error"]`` (str) — direct.
      2. ``result["errors"]`` (list / dict / str) — flattened via
         ``_stringify_errors`` so list members are joined with
         ``"; "``.
      3. ``result["message"]`` (str) — fallback.
      4. ``error_code`` / ``code`` (str) — preserved verbatim as
         a last-resort hint.

    Returns ``""`` when no error field is present. Non-dict
    results always return ``""``.
    """
    if not isinstance(result, dict):
        return ""

    err = result.get("error")
    if err:
        return str(err)

    errors = result.get("errors")
    if errors:
        text = _stringify_errors(errors)
        if text:
            return text

    msg = result.get("message")
    if msg:
        return str(msg)

    code = result.get("error_code") or result.get("code")
    if code:
        return str(code)

    return ""


def _stringify_errors(errors: Any) -> str:
    """Best-effort flattening of an ``errors`` payload into a
    single human-readable string for ``ToolResult.error``.

    Accepts ``list`` / ``dict`` / ``str`` / scalar. Returns "" if
    nothing useful is present.
    """
    if not errors:
        return ""
    if isinstance(errors, str):
        return errors
    if isinstance(errors, list):
        parts = [_stringify_errors(e) for e in errors if e not in (None, "")]
        return "; ".join(p for p in parts if p)
    if isinstance(errors, dict):
        import json as _json
        try:
            return _json.dumps(errors, ensure_ascii=False, default=str)
        except Exception:
            return str(errors)
    return str(errors)


class ToolRuntime:
    """Stateless tool execution runtime.

    Tools are registered as handler functions. Each handler receives
    arguments and returns results — no shared state, no implicit context.
    """

    def __init__(self, config: SSOTRuntimeConfig):
        self._config = config
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, tool_id: str, handler: ToolHandler) -> None:
        """Register a tool handler.

        Handler signature: handler(args: dict) → result | Awaitable[result]
        """
        self._handlers[tool_id] = handler

    def has_tool(self, tool_id: str) -> bool:
        return tool_id in self._handlers

    def invoke_raw(self, tool_id: str, arguments: dict | None = None) -> dict:
        """Invoke a registered tool handler directly and return a result dict.

        Handles both sync and async handlers safely from any thread.
        """
        arguments = arguments or {}
        handler = self._handlers.get(tool_id)
        if handler is None:
            return {"ok": False, "error": f"Tool not found: {tool_id}"}
        try:
            result = handler(arguments)
            if asyncio.iscoroutine(result):
                # Handler is async — create a temp event loop (safe in thread pool)
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(result)
                finally:
                    loop.close()
            if isinstance(result, dict):
                return result
            return {"ok": True, "data": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def execute_node(
        self,
        node: ExecutionNode,
        ctx: StatelessContext,
        dep_results: dict[str, ToolResult],
    ) -> ToolResult:
        """Execute a single node with dependency injection.

        Args:
            node: The compiled execution node
            ctx: Minimal stateless context
            dep_results: Resolved results of this node's dependencies

        Returns:
            ToolResult with success/failure and data

        The handler result is normalized so that ``ok=False``
        in the returned dict maps to ``ToolResult.success=False``.
        This is what lets the retry policy, dependency gate, and
        tool-call aggregation see the real outcome — previously
        SSOT Runtime always returned ``success=True`` whenever the handler
        didn't raise, even when its inner data said the call had
        failed. The behavior now mirrors the production tool
        runtime at ``core.tools.executor``: the handler's
        explicit ``ok`` field drives success.
        """
        start = time.monotonic()

        if node.tool not in self._handlers:
            elapsed = (time.monotonic() - start) * 1000
            return ToolResult(
                node_id=node.id,
                tool=node.tool,
                success=False,
                error=f"Tool '{node.tool}' has no registered handler",
                error_code="TOOL_NOT_REGISTERED",
                error_code_raw="",
                error_code_norm="TOOL_NOT_REGISTERED",
                latency_ms=elapsed,
                retry_count=0,
            )

        # Inject dependency results into args
        merged_args = self._merge_dep_results(node.args, dep_results)

        try:
            handler = self._handlers[node.tool]
            # Run with timeout
            result = await asyncio.wait_for(
                self._invoke_handler(handler, merged_args),
                timeout=self._config.single_node_timeout_ms / 1000,
            )
            elapsed = (time.monotonic() - start) * 1000
            return _normalize_result(node, result, elapsed)
        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - start) * 1000
            return ToolResult(
                node_id=node.id,
                tool=node.tool,
                success=False,
                error=f"Tool execution timed out after {self._config.single_node_timeout_ms}ms",
                error_code="TOOL_TIMEOUT",
                error_code_raw="",
                error_code_norm="TOOL_TIMEOUT",
                latency_ms=elapsed,
                retry_count=node.retry_count,
            )
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return ToolResult(
                node_id=node.id,
                tool=node.tool,
                success=False,
                error=f"{type(e).__name__}: {e}",
                error_code="TOOL_EXCEPTION",
                error_code_raw="",
                error_code_norm="TOOL_EXCEPTION",
                latency_ms=elapsed,
                retry_count=node.retry_count,
            )

    async def execute_layer(
        self,
        nodes: list[ExecutionNode],
        ctx: StatelessContext,
        dep_results: dict[str, ToolResult],
    ) -> dict[str, ToolResult]:
        """Execute all nodes in a layer concurrently.

        All nodes at the same depth run fully parallel via asyncio.gather.
        """
        if not nodes:
            return {}

        tasks = {
            node.id: asyncio.create_task(
                self.execute_node(node, ctx, dep_results)
            )
            for node in nodes
        }

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        layer_results: dict[str, ToolResult] = {}
        for node_id, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                layer_results[node_id] = ToolResult(
                    node_id=node_id,
                    tool="unknown",
                    success=False,
                    error=f"{type(result).__name__}: {result}",
                )
            else:
                layer_results[node_id] = result

        return layer_results

    async def _invoke_handler(self, handler: ToolHandler, args: dict[str, Any]) -> Any:
        """Invoke a handler, supporting both sync and async handlers."""
        result = handler(args)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    def _merge_dep_results(
        self,
        args: dict[str, Any],
        dep_results: dict[str, ToolResult],
    ) -> dict[str, Any]:
        """Inject dependency results into node arguments.

        If an arg value is a special reference like "$dep.node_id.data",
        replace it with the actual dependency result.
        """
        merged = dict(args)
        for key, value in list(merged.items()):
            if isinstance(value, str) and value.startswith("$dep."):
                # Resolve dependency: "$dep.node_id.data"
                parts = value.replace("$dep.", "").split(".", 1)
                dep_id = parts[0]
                if dep_id in dep_results:
                    dep_result = dep_results[dep_id]
                    if len(parts) > 1 and parts[1] == "data":
                        merged[key] = dep_result.data
                    else:
                        merged[key] = dep_result.data
        return merged


# ── Module-level result normalizer ──────────────────────────────────────


def _normalize_result(
    node: ExecutionNode,
    handler_result: Any,
    elapsed_ms: float,
) -> ToolResult:
    """v4 single-source ``ToolResult`` builder.

    Routes the handler's return value through
    ``resolve_tool_outcome`` to decide the verdict, and through
    ``extract_error`` to flatten the error message. This is the
    ONLY function in the runtime that constructs a
    ``ToolResult`` for a handler return value — see
    ``ExecutionContract.TOOL_TRUTH_SINGLE_SOURCE``.

    Direct ``ToolResult`` constructions elsewhere in this file
    are reserved for runtime-level failure paths (handler not
    registered, asyncio timeout, exception raised) where the
    handler did NOT return and there is no verdict to resolve.
    """
    assert ExecutionContract.TOOL_TRUTH_SINGLE_SOURCE, (
        "v4 contract TOOL_TRUTH_SINGLE_SOURCE is off — "
        "_normalize_result refuses to build a ToolResult without "
        "the resolver enforcement."
    )

    status, error_code, normalized = resolve_tool_outcome(handler_result)
    success = status == _STATUS_SUCCESS
    error_code_norm = error_code or ""
    error_message = extract_error(handler_result) if not success else ""

    # v4.1: preserve raw error_code from handler's return value
    # so audit/retry can distinguish handler-declared codes from
    # normalised fallbacks.
    error_code_raw = ""
    if isinstance(handler_result, dict):
        error_code_raw = (
            handler_result.get("error_code")
            or handler_result.get("code")
            or ""
        )

    tr = ToolResult(
        node_id=node.id,
        tool=node.tool,
        success=success,
        data=normalized,
        error=error_message or None,
        error_code=error_code_norm,
        latency_ms=elapsed_ms,
        retry_count=node.retry_count,
        error_code_raw=str(error_code_raw) if error_code_raw else "",
        error_code_norm=str(error_code_norm),
    )
    # v6: enforce error_code boundary on every tool result
    if ExecutionSemanticsContract.SINGLE_TRUTH_TOOL_RESULT:
        assert_error_code_usage(tr)
    return tr
