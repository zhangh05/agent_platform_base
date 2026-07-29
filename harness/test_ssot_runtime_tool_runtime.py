"""
P0 fix coverage: ``core.runtime_engine.tool_runtime.ToolRuntime`` must propagate
the handler's ``ok`` / ``success`` flag and structured error fields into
the ``ToolResult`` it produces.

Background (5-layer audit, v3.10):

  The previous implementation constructed ``ToolResult(success=True)``
  unconditionally — handler return values like
  ``{"ok": False, "error": "..."}`` were silently treated as
  successes. The downstream retry policy, dependency gate, and
  ``tool_calls[0].ok`` projection all read ``ToolResult.success``,
  so the bug propagated end-to-end:

    * retry policy never fires,
    * downstream nodes run as if nothing failed,
    * ``node_success_count`` over-counts,
    * ``result.metadata.runtime.all_nodes_success`` stays True even
      when the only node failed.

These tests assert the new behavior:

  * A handler returning ``{"ok": False, "error": "..."}`` produces
    a ``ToolResult`` with ``success=False`` and the error message.
  * A handler returning ``{"ok": True}`` produces
    ``ToolResult(success=True)``.
  * A handler returning ``{"success": False, "error_code": "...",
    "error": "..."}`` produces the expected ``error_code``.
  * A handler returning bare dict with no flag is rejected.
  * The engine's ``metadata.runtime`` reports ``node_failure_count=1``
    when the only node fails (no longer over-counted).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from core.runtime_engine.models import ExecutionStatus, SSOTRuntimeConfig, ToolResult
from core.runtime_engine.tool_runtime import ToolRuntime


def _make_runtime(handler, *, max_retries: int = 0) -> ToolRuntime:
    cfg = SSOTRuntimeConfig(max_retries_per_node=max_retries)
    rt = ToolRuntime(cfg)
    rt.register("test.tool", handler)
    return rt


def _run_node(handler, args=None):
    """Build a minimal ExecutionNode and run it."""
    from core.runtime_engine.models import ExecutionNode, StatelessContext

    rt = _make_runtime(handler)
    node = ExecutionNode(
        id="n1",
        tool="test.tool",
        args=args or {},
    )
    ctx = StatelessContext(
        session_id="audit_e2e_p0_unit",
        workspace_id="default",
        user_input="unit test",
        request_id="req-unit",
    )
    return asyncio.run(rt.execute_node(node, ctx, {}))


# ── A: handler ok=False → ToolResult.success=False ─────────────────────


def test_handler_ok_false_propagates_to_tool_result():
    """A handler that returns ``{"ok": False, "error": "..."}`` must
    produce ``ToolResult.success=False`` and the error message.
    """

    def handler(args: dict) -> dict:
        return {"ok": False, "error": "missing param 'name'"}

    result = _run_node(handler)

    assert isinstance(result, ToolResult), f"expected ToolResult, got {type(result)}"
    assert result.success is False, (
        f"P0 bug regressed: handler returned ok=False but ToolResult.success "
        f"is still True. result={result!r}"
    )
    assert "missing param 'name'" in result.error
    assert result.error_code == "TOOL_RETURNED_NOT_OK"


# ── B: handler ok=True → ToolResult.success=True ───────────────────────


def test_handler_ok_true_propagates_to_tool_result():
    def handler(args: dict) -> dict:
        return {"ok": True, "data": {"answer": 42}}

    result = _run_node(handler)
    assert result.success is True
    assert not result.error  # empty string or None
    assert not result.error_code


# ── C: handler ok=False + error_code → structured error_code ───────


def test_handler_structured_error_code_is_propagated():
    """A handler that returns ``{"ok": False, "error_code": "X",
    "error": "..."}`` (the modern contract) must produce
    ``ToolResult(success=False, error_code="X")`` and the error
    message.

    The current contract uses the canonical ``ok`` key to propagate the
    handler's ``error_code`` into the resolver.
    """
    def handler(args: dict) -> dict:
        return {
            "ok": False,
            "error_code": "CRED_MISSING",
            "error": "no credentials in vault",
        }

    result = _run_node(handler)
    assert result.success is False
    assert result.error_code == "CRED_MISSING"
    assert "no credentials" in result.error


# ── D: bare dict with no verdict is invalid ──────────────────────────


def test_bare_dict_without_ok_is_rejected():
    """Every current handler result must declare the canonical ``ok`` verdict."""

    def handler(args: dict) -> dict:
        return {"answer": 42}

    result = _run_node(handler)
    assert result.success is False
    assert result.error_code == "TOOL_RESULT_INVALID"


# ── E: errors list/dict is joined into the error message ──────────────


def test_handler_errors_list_joined_into_error_message():
    def handler(args: dict) -> dict:
        return {
            "ok": False,
            "errors": ["first failure", "second failure"],
        }

    result = _run_node(handler)
    assert result.success is False
    assert "first failure" in result.error
    assert "second failure" in result.error


# ── F: engine-level node_failure_count tracks the new success flag ────


def test_engine_metadata_reflects_node_failure():
    """End-to-end: a failing tool node must produce
    ``node_failure_count=1`` and ``all_nodes_success=False`` in
    the engine metadata — the P0 fix is observable from the
    public Engine.run() surface.
    """

    from core.runtime_engine.engine import SSOTRuntimeEngine

    def mock_llm(**_kw):
        return json.dumps({
            "nodes": [
                {
                    "id": "n1",
                    "tool": "test.tool",
                    "action": "do",
                    "args": {},
                }
            ]
        })

    def failing_handler(args: dict) -> dict:
        return {"ok": False, "error": "explicit fail"}

    registry = {
        "test.tool": {
            "description": "",
            "args_schema": {
                "required": ["action"],
                "properties": {"action": {"type": "string"}},
            },
        }
    }
    cfg = SSOTRuntimeConfig(max_retries_per_node=0)
    engine = SSOTRuntimeEngine(
        config=cfg,
        llm_invoke=mock_llm,
        tool_registry=registry,
    )
    engine.register_tool("test.tool", failing_handler)

    result = asyncio.run(engine.run(
        "trigger the failing tool",
        workspace_id="default",
        session_id="audit_e2e_p0",
    ))

    # node_failure_count / node_success_count / all_nodes_success live at
    # the top level of ``result.metadata`` (ssot_runtime flattens them
    # into a top-level projection for frontend consumers).
    meta = result.metadata or {}
    assert meta.get("node_failure_count") == 1, (
        f"P0 fix regression: handler returned ok=False but engine counted "
        f"node_failure_count={meta.get('node_failure_count')}. "
        f"result={result!r}"
    )
    assert meta.get("all_nodes_success") is False, (
        f"P0 fix regression: all_nodes_success should be False after a "
        f"tool failure, got {meta.get('all_nodes_success')!r}"
    )
    assert meta.get("node_success_count") == 0, (
        f"P0 fix regression: success_count must be 0 after a failed node, "
        f"got {meta.get('node_success_count')!r}"
    )
