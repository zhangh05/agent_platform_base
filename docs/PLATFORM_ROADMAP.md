# Platformization roadmap

This repository is now treated as an agent application platform rather than a
single domain application. The existing `SSOTRuntimeEngine`, tool governance,
workspace boundaries, durable tasks, approvals, memory gate and local
filesystem mode remain the runtime kernel.

## Stage 1: extension contract

The `extensions` package defines a declarative manifest with version, tools,
capabilities, permissions, routes and frontend modules. `scripts/platform_contract.py`
prints the current tool/capability counts and validates discovered manifests.
`core.tools.mcp_client.StdioMcpClient` provides the dependency-free MCP stdio
boundary. MCP tools must be converted into the platform manifest and pass the
existing risk, approval, redaction and audit pipeline before execution.

The `evaluation` package provides the first deterministic golden-case contract.
It is intentionally small; domain-specific golden cases should live with the
extension that owns them.

## Stage 2: production adapters

The filesystem adapter remains the development default. `storage.backend`,
`storage.object_store`, `storage.event_bus`, `jobs.queue` and
`observability.exporters` define replaceable boundaries for PostgreSQL,
S3-compatible storage, Redis/NATS-style events, distributed queues and OTLP.
`scripts/platform_runtime_check.py` validates required production environment
variables without making network calls.

## Stage 3: enterprise control plane

Set `AGENT_PLATFORM_IDENTITY_ENABLED=true` to enable the file-backed identity
adapter. User passwords are PBKDF2-hashed, sessions carry role and organization,
and `/api/identity/users` supports administrator-managed users. The existing
environment-variable login remains compatible and acts as an administrator
bootstrap when identity mode is enabled.

Task-specific model routing is available through variables such as
`AGENT_PLATFORM_MODEL_ROUTE_ASSISTANT_CHAT=deepseek`. With no route configured,
the active-provider behavior is unchanged.

## Production completion still required

The adapters are deliberately dependency-free seams. A production deployment
must still provide concrete PostgreSQL/object-store/queue implementations,
database migrations, secret-manager integration, distributed leases and an
OIDC provider before horizontal scaling or external tenants are enabled.
