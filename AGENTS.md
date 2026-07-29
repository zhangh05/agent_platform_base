# AGENTS.md

This file is the handoff contract for AI coding agents working in Agent Platform Base.

## Non-Negotiable Rules

1. Keep this repository domain-neutral. Do not add product-specific features to the base unless they are introduced as clearly isolated extensions.
2. All tool invocation goes through the SSOT QueryLoop runtime and registered canonical handlers. Do not add alternate planner, dispatch, or compatibility paths.
3. Canonical tool definitions are the single source of truth for LLM-visible capabilities.
4. `workspace_id` must be explicit and validated at API boundaries. Empty values return 400.
5. Approval is for high-risk/destructive actions, not for ordinary read/list/query operations.
6. Memory writes go through `storage.memory_governance.MemoryWriteGate`.
7. Do not commit runtime data, provider secrets, logs, build output, caches, or workspace contents.
8. Removed product modules must not be kept as hidden compatibility layers.

## Current Main Chain

```
Frontend
  -> backend/main.py routes or backend/ws/agent_ws.py
  -> agent.app.facade.AgentApp
  -> SSOTRuntimeEngine
     ├─ Fast-path classifier
     ├─ Pre-planner guard
     └─ QueryLoop iterative LLM+tool loop
  -> ToolRuntime.invoke_raw() → registered canonical handlers
  -> durable state, artifacts, memory, trace
```

## Base Canonical Tools

The base exposes only domain-neutral tools:

`agent.manage`, `browser.manage`, `data.manage`, `exec.run`, `knowledge.manage`, `memory.manage`, `report.manage`, `skill.manage`, `system.manage`, `text.analyze`, `web.manage`, `workspace.artifact`, `workspace.document.pdf.extract_text`, `workspace.file`, `workspace.filestore`, `workspace.metadata.get`

Product projects can add domain tools later, but they must be added through:

- `core/tools/canonical_registry.py`
- `core/tools/tool_namespace_data.py`
- `core/tools/manifest_registry.py`
- `core/runtime_engine/contracts.py`
- focused tests for the new tool surface

## Review Checklist

Before committing:

- Confirm QueryLoop is the only active tool-capable runtime path.
- Verify tool name normalisation: `__` → `.` on parse, `.` → `__` on append.
- Check registry, namespace, manifest, and contracts expose the same canonical IDs.
- Run focused tests for the changed layer.
- Inspect status and stage only intended source/docs/tests.

Useful checks:

```bash
python3 - <<'PY'
from core.tools.tool_namespace import TOOL_NAMESPACE
from core.tools.canonical_registry import CANONICAL_REGISTRY
from core.tools.manifest_registry import MANIFESTS
from core.runtime_engine.contracts import BUILTIN_CONTRACTS
print(len(TOOL_NAMESPACE), len(CANONICAL_REGISTRY), len(MANIFESTS), len(BUILTIN_CONTRACTS))
print(sorted(TOOL_NAMESPACE))
PY
```

## Local Cleanup

Safe cleanup targets:

- `.DS_Store`
- `__pycache__/`
- `.pytest_cache/`
- frontend build output
- generated audit reports

Preserve:

- `config/providers/`
- `config/llm.local.yaml`
- running backend/frontend processes unless the user asks to restart
