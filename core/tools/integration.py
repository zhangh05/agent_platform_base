# core/tools/integration.py
"""Integration helpers — factory functions and trace metadata adapter.

Provides:
  - get_default_tool_runtime_client(): default client from canonical tools
  - create_tool_runtime_client(): configurable factory for testing
  - build_trace_metadata_from_tool_result(): safe trace metadata builder
"""

import threading

from core.tools.registry import ToolRegistry
from core.tools.policy import ToolPolicy
from core.tools.client import ToolRuntimeClient
from core.tools.canonical_registry import to_tool_specs
from core.tools.trace_metadata import build_trace_metadata_from_tool_result

# ── Module-level default (lazy singleton, double-checked locking) ──
_default_client: ToolRuntimeClient = None
_default_client_lock = threading.Lock()
_default_client_build_count: int = 0


def get_default_tool_runtime_client() -> ToolRuntimeClient:
    """Get or create the default ToolRuntimeClient with canonical tools.

    v3.10: the previous implementation had a classic
    double-checked-locking bug — the read of ``_default_client``
    was outside any lock, so two threads that observed ``None``
    could each construct a full registry + client. The lock now
    covers the entire critical section (read + create + assign),
    and the inner re-check inside the lock prevents duplicate
    builds when concurrent callers race past the first null-check.

    Returns a singleton instance. Thread-safe for creation (not
    mutation). Uses independent ToolInvocation/ToolResult — does
    NOT import agent/state.py.
    """
    global _default_client, _default_client_build_count
    if _default_client is not None:
        return _default_client
    with _default_client_lock:
        if _default_client is None:
            registry = ToolRegistry()
            for spec, handler in to_tool_specs():
                registry.register_tool(spec, handler)
            policy = ToolPolicy()
            _default_client = ToolRuntimeClient(registry, policy)
            _default_client_build_count += 1
        return _default_client


def get_default_client_build_count() -> int:
    """Return how many times the singleton has actually been built.

    Exposed for tests and diagnostics to verify that concurrent
    callers cannot accidentally trigger a duplicate build.
    """
    return _default_client_build_count


def reset_default_client_for_tests() -> None:
    """Drop the cached singleton — tests use this to assert that
    a fresh build runs through ``register_builtin_tools`` /
    ``register_all_general_tools`` on each call."""
    global _default_client, _default_client_build_count
    with _default_client_lock:
        _default_client = None
        _default_client_build_count = 0


def create_tool_runtime_client(
    registry: ToolRegistry = None,
    policy: ToolPolicy = None,
) -> ToolRuntimeClient:
    """Create a ToolRuntimeClient with optional custom registry/policy.

    If registry is None, creates empty registry (no builtins).
    Useful for testing with mock/injected registries.
    """
    if registry is None:
        registry = ToolRegistry()
    if policy is None:
        policy = ToolPolicy()
    return ToolRuntimeClient(registry, policy)
