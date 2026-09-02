# Cross-extension workflows

Workflows are workspace-scoped DAGs whose nodes reference canonical platform or
installed extension tool IDs. They never call extension handlers directly. Each
node enters `ToolRuntimeClient`, preserving caller checks, JSON-schema validation,
risk policy, product authorization, redaction, quotas, tracing, and workspace
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
      "arguments": {"connection_ids": "${input.connection_ids}"}
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

Workflows do not create an interactive authorization state. Product-owned tools
validate their own published scope at invocation time; rejected nodes fail with
a structured authorization error. Destructive host commands are blocked by the
shared runtime policy.

## Relation to the conversational goal loop

Workflow `failure_policy` controls declared DAG nodes; it does not create a
second LLM recovery loop. A failed node remains a workflow result with its
stable node identity. Safe explicit node retry is available only through the
runtime task API and must obey the original canonical contract, authorization,
idempotency and write fences.

Conversational QueryLoop recovery is separate and domain-neutral: it may pursue
bounded alternative **read** evidence after a recoverable observation failure.
It never auto-replays a workflow write or changes a workflow definition. If a
workflow tool result publishes an evidence recovery directive, QueryLoop may
consume it only when that result is part of a conversational turn; ordinary
workflow execution records the directive as output and leaves the next action
to the workflow owner/operator. See [Loop Engineering](LOOP_ENGINEERING.md).

Roles are explicit in identity mode: viewers can read definitions and runs,
operators can execute/cancel, developers can create or update, and organization
administrators inherit those permissions. Workflow APIs remain a framework
capability; the retired generic editor is not part of the current product UI.
