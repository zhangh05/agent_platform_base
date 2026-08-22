# Cross-extension workflows

Workflows are workspace-scoped DAGs whose nodes reference canonical platform or
installed extension tool IDs. They never call extension handlers directly. Each
node enters `ToolRuntimeClient`, preserving caller checks, JSON-schema validation,
risk policy, approval requirements, redaction, quotas, tracing, and workspace
scope.

Nodes in the same dependency layer are scheduled together: independent
read-only calls run concurrently within the configured limit, while writes and
other side-effecting calls remain ordering barriers. Results are persisted in
stable node order, so concurrency never changes references or audit semantics.

## Definition

```json
{
  "workflow_id": "readonly_inspection",
  "name": "批量只读巡检",
  "failure_policy": "fail_fast",
  "nodes": [
    {
      "node_id": "inspect",
      "tool_id": "network.operations.inspection",
      "arguments": {"asset_ids": "${input.asset_ids}"}
    }
  ]
}
```

Node IDs are unique. Dependencies must exist, cycles are rejected, and a node may
only reference outputs from its transitive dependencies. Definitions may contain
runtime-input or secret-reference templates, but cannot persist literal password,
API-token, authorization, or private-key fields.

Definitions contain 1–50 nodes and accept at most 1 MiB of resolved input per
node. Persisted workflow inputs and outputs are redacted; large node outputs are
projected to a bounded structured record. The full in-memory output remains
available to downstream nodes during that run.

## Execution

`POST /api/workflows/<workflow_id>/runs` executes synchronously by default. Set
`"enqueue": true` to create a durable `workflow_run` job for a worker. Runs record
every node status, summary, safe output projection, errors, timing, and cancellation
state. Queued execution inherits the production queue's at-least-once semantics,
so external writes must use job/node idempotency keys.

High-risk tools still require an approval. Pass an approval map keyed by node ID:

```json
{"workspace_id":"default","inputs":{},"approvals":{"change":"approval_123"}}
```

Roles are explicit in identity mode: viewers can read definitions and runs,
operators can execute/cancel, developers can create or update, and organization
administrators inherit those permissions. The 应用编排 page provides the same
model with Chinese labels, application/tool counts, ordered step cards, parameter
templates, dependencies, save, and test-run results.
