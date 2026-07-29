# Architecture Notes

This folder contains current architecture notes only. The authoritative runtime chain is:

```text
AgentApp → AgentThread (core/thread.py) → run_ssot_turn (ssot_runtime.py)
       → ToolRuntimeClient.invoke → ToolExecutor → canonical handlers
```

**Memory** runs in parallel:
- Auto-inject per turn start: `MemoryHitsFragment` → `UnifiedRetriever`
- Generate per turn end: `llm_memory.py` → `MemoryWriteGate`

Current anchors:

- `../ARCHITECTURE.md` — short architecture reference (includes memory boundary).
- `../MEMORY_SUBSYSTEM.md` — full memory pipeline documentation.
- `../../core/tools/tool_namespace_data.py` — public tool namespace (29 IDs).
- `../../core/tools/manifest_registry.py` — tool manifests.
- `../../agent/capabilities/catalog.py` — business capability catalog.
- `../../storage/memory_governance.py` — MemoryRecord, MemoryStore, MemoryWriteGate.
- `../../core/context/unified_retriever.py` — BM25 retrieval engine.

Do not add documents for removed compatibility paths or old tool names.
