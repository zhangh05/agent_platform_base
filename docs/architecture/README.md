# Architecture Notes

This folder contains current architecture notes only. The authoritative runtime chain is:

```text
AgentApp → AgentThread (agent/core/thread.py) → run_ssot_turn (agent/runtime/ssot_runtime.py)
       → SSOTRuntimeEngine → QueryLoop → goal-driven evidence recovery
       → ToolRuntimeClient.invoke → ToolExecutor → canonical handlers
```

**Memory** is governed around the turn:
- `agent/runtime/ssot_runtime.py` retrieves bounded `memory_hits` through
  `core/context/unified_retriever.py` and projects them as data-only context.
- The same runtime appends experience through
  `agent/runtime/memory_write/event_log.py`; consolidation writes only through
  `storage/memory_governance.MemoryWriteGate`.

Current anchors:

- `../ARCHITECTURE.md` — short architecture reference (includes memory and recovery boundaries).
- `../LOOP_ENGINEERING.md` — authoritative goal-driven recovery lifecycle.
- `../MEMORY_SUBSYSTEM.md` — full memory pipeline documentation.
- `../../core/tools/tool_namespace_data.py` — public tool namespace (current count is validated by registry tests, not this document).
- `../../core/tools/manifest_registry.py` — tool manifests.
- `../../agent/capabilities/catalog.py` — business capability catalog.
- `../../storage/memory_governance.py` — MemoryRecord, MemoryStore, MemoryWriteGate.
- `../../core/context/unified_retriever.py` — BM25 retrieval engine.

Do not add documents for removed compatibility paths or old tool names.
