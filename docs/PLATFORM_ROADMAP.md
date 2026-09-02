# Platformization roadmap

This repository is now treated as an agent application platform rather than a
single domain application. The existing `SSOTRuntimeEngine`, tool governance,
workspace boundaries, durable tasks, product authorization, memory gate and local
filesystem mode remain the runtime kernel.

The kernel also includes domain-neutral goal-driven recovery: a recoverable
read failure becomes a bounded evidence goal inside QueryLoop, not an extension
specific retry worker. This keeps cross-capability recovery governed by the same
tool, authorization and audit boundaries. See [Loop Engineering](LOOP_ENGINEERING.md).

## Stage 1: extension contract

The `extensions` package defines a declarative manifest with version, tools,
capabilities, permissions, routes and frontend modules. `scripts/platform_contract.py`
prints the current tool/capability counts and validates discovered manifests.
`core.tools.mcp_client.StdioMcpClient` provides a timeout-bounded MCP stdio
boundary. Registered MCP tools are discovered and invoked through
`skill.manage`, so trust checks, permissions, authorization, redaction and audit
remain in the existing governance pipeline.

The `evaluation` package provides the first deterministic golden-case contract.
It is intentionally small; domain-specific golden cases should live with the
extension that owns them.

## Stage 2: production adapters

The filesystem adapter remains the development default. Concrete adapters now
cover PostgreSQL JSON runtime records, S3-compatible objects, Redis
cross-process events and durable jobs, plus OTLP trace export. Record and object
storage are independently selectable, so PostgreSQL and S3 can run together.
Redis workers use renewable leases and reclaim stale work with at-least-once
semantics. Verified snapshots, readiness probes, Prometheus HTTP metrics, and
immutable release slots with rollback complete the production operations path. They are selected
through environment variables, while local mode remains zero-infrastructure.
`scripts/platform_runtime_check.py` validates required production environment
variables without making network calls.

## Stage 3: enterprise control plane

Set `LZCORE_IDENTITY_ENABLED=true` to enable the file-backed identity
adapter. User passwords are PBKDF2-hashed, sessions refresh current roles and
workspace membership on every request, and `/api/identity/users` supports
administrator-managed users. Provider API keys are encrypted at rest when
`LZCORE_MASTER_KEY` is configured. The existing
environment-variable login remains compatible and acts as an administrator
bootstrap when identity mode is enabled.

Task-specific model routing is available through variables such as
`LZCORE_MODEL_ROUTE_ASSISTANT_CHAT=deepseek`. A routed provider is tried
first and real invocation failures fall back to the active provider. Explicit
per-call configuration remains authoritative.

## Stage 4: multi-application control plane

Workspace-scoped DAG workflows compose core and extension tools through the
governed runtime. Durable workflow jobs, cancellation, safe output projections,
template/dependency validation, and the 应用编排 workbench make multi-application
orchestration a platform feature rather than application-specific glue.

Organizations now own workspaces uniquely. Membership-derived roles and workspace
sets are refreshed on every authenticated request; organization administrators no
longer bypass tenant scope. The 组织与成员 workbench exposes the control plane.

## Further scale-out work

The PostgreSQL, S3, Redis queue and Redis workspace-event adapters support
cross-process state, and Redis workers can execute concurrently under leases.
The current live per-turn WebSocket coordinator still uses a single web process;
deployments must not scale the web service beyond one process until that event
transport becomes shared. Larger enterprise
installations still need an external secret manager, OIDC/SSO, database-native
schema migrations, and broader migration of workspace metadata from files to
PostgreSQL. OIDC/SCIM, database row-level security, scheduler clustering, and a
visual drag-and-drop workflow canvas remain optional enterprise follow-on work,
not blockers for the v2 platform contract.

`start.sh` serves a production build through Vite preview by default; use
`FRONTEND_MODE=dev` only for local hot-reload development.
