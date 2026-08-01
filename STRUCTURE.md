# Current Project Structure

```text
agent_platform_base/
├── agent/
│   ├── app/                    # AgentApp, session/thread orchestration
│   ├── capabilities/           # Business capability catalog only
│   ├── core/                   # Turn/session core types
│   ├── llm/                    # Provider config, runtime calls, key resolver
│   ├── modules/                # Domain implementations used by canonical tools; per-module prompt_templates/
│   ├── runtime/                # SSOT Runtime adapter, result projection, durable state
│   └── tools/                  # Agent-facing router/registry adapter
├── artifacts/                  # Artifact records and content store
├── backend/
│   ├── main.py                 # Flask app entry
│   ├── api/                    # REST routes
│   ├── core/                   # Auth, limits, settings, response helpers
│   └── ws/                     # WebSocket gateways
├── config/                     # Local provider/runtime config; secrets ignored
├── docs/                       # Current architecture and API docs
├── frontend/                   # React 18 + TypeScript + Vite app
├── harness/                    # Backend contract/architecture tests
├── jobs/                       # Job records, lifecycle, runner, worker
├── observability/              # Trace/event persistence
├── prompts/                    # Top-level prompt templates and registry
├── reports/                    # Generated reports; audits are ignored unless tracked
├── storage/                    # File store abstractions
├── core/                       # SSOT Runtime engine, canonical tools, core utilities
│   ├── context/
│   ├── llm/                    # LLMProvider registry/config
│   ├── reports/                # Report renderers
│   ├── runtime/                # Diagnostics/Selfcheck/Retention
│   ├── runtime_engine/         # SSOT QueryLoop runtime, budgets, tracking, audit
│   ├── time/
│   ├── tools/                  # Canonical platform tools, manifest, policy, executor
│   └── workspaces/             # Workspace abstractions
├── workspaces/                 # Local workspace data; not committed
├── start.sh
└── stop.sh
```

## Ownership Rules

- Tool execution belongs to `core/tools/`.
- Business capability descriptions belong to `agent/capabilities/catalog.py`.
- Durable runtime state belongs to `agent/runtime/durable/`.
- User-visible workbench state belongs to `frontend/src/stores/`.
- Persistent local user data belongs under `workspaces/`, `config/providers/`, or `logs/` and must stay out of Git.
- `workspaces/_runtime/` contains application-level runtime records not owned by one workspace.

## Removed Concepts

The current tree does not use tool aliases, compatibility shims, generated module capability registries, or multiple approval stores. Do not reintroduce them.
