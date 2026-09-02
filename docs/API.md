# 联智中枢 API 参考

服务基地址：`http://127.0.0.1:8011`（本地默认）。生产环境由反向代理提供同源 `/api`、`/ws/agent` 与 SSE 路径。

本文档以 `backend.main.create_app()` 当前注册的 Flask 路由为准，只描述公开 HTTP 与 WebSocket 面，不把内部 handler 当作 API。工作区数据接口必须携带服务端验证的 `workspace_id`；它可能在 query 或路径 `<ws_id>` 中，具体请求 schema 以对应路由实现为准。identity 模式下还需要有效登录会话或服务凭据。

除健康检查外，调用方应处理结构化错误与资源生命周期，不能从 HTTP 状态码推断写入是否可安全重试。运行时结果以 `AgentResult` 投影及其 `execution_outcome`、`tool_execution_outcome` 为准。

## Service, authentication and agent

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health`, `/api/ready`, `/api/version` | Liveness, readiness and version projection. |
| `GET` | `/api/metrics`, `/metrics` | JSON and Prometheus metrics. |
| `GET` | `/api/auth/status` | Safe current-session projection. |
| `POST` | `/api/auth/login`, `/api/auth/logout` | Password-session lifecycle. |
| `GET` | `/api/auth/oidc/start`, `/api/auth/oidc/callback` | Optional OIDC flow. |
| `POST` | `/api/agent/message` | Run one Agent turn. |
| `GET` | `/api/agent/sse/stream/<session_id>` | Stream one session's agent events. |
| `WS` | `/ws/agent` | WebSocket agent stream. |
| `GET` | `/api/agent/status`, `/api/agent/usage` | Agent status and usage projection. |
| `GET/POST/DELETE` | `/api/agent/llm/config` | LLM configuration lifecycle. |
| `GET` | `/api/agent/llm/providers`, `/api/agent/llm/providers/<provider_id>` | Provider catalog/detail. |
| `POST/DELETE` | `/api/agent/llm/providers/<provider_id>` | Provider save/delete. |
| `POST` | `/api/agent/llm/activate`, `/api/agent/llm/test` | Activate or test an LLM configuration. |
| `GET` | `/api/agent/llm/status` | Safe LLM availability projection. |

## Runtime, context and prompts

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/runtime/summary`, `/api/runtime/health`, `/api/runtime/selfcheck` | Runtime catalog and health/self-check. |
| `GET` | `/api/runtime/tasks`, `/api/runtime/tasks/<task_id>` | Durable runtime task list/detail. |
| `GET` | `/api/runtime/tasks/<task_id>/events`, `/checkpoints` | Task event and checkpoint history. |
| `POST` | `/api/runtime/tasks/<task_id>/cancel`, `/resume`, `/checkpoint` | Task lifecycle control. |
| `POST` | `/api/runtime/tasks/<task_id>/steps/<step_id>/retry` | Retry a safe failed step. |
| `GET/POST` | `/api/runtime/tasks/<task_id>/audit-report` | Read/create task audit report. |
| `GET` | `/api/runtime/trajectories`, `/api/runtime/trajectories/<traj_id>` | Runtime trajectory projections. |
| `POST` | `/api/context/build`, `/api/context/resolve`, `/api/prompts/render` | Governed context/prompt operations. |
| `GET` | `/api/context/status`, `/api/prompts`, `/api/prompts/<prompt_id>`, `/api/harness/status` | Context, prompt and harness projections. |

### Result and recovery lifecycle

Agent, SSE, WebSocket and task-detail projections can contain server-generated
metadata. `execution_outcome` is the user-task result (`complete`, `partial`,
`failed` or `unknown`); `tool_execution_outcome` is the corresponding tool-attempt
result. `recovery_goals`, `recovery_goal_events` and `goal_loop` explain
goal-driven recovery when present.

A failed tool attempt alone does not determine the user-task result. `partial`
means some coverage is verified but required coverage remains blocked; `unknown`
means an external write or long-running work cannot yet be confirmed. Clients
must display these server values, not synthesize them.

## Sessions, runs and workspace state

| Method | Path | Purpose |
| --- | --- | --- |
| `GET/POST` | `/api/sessions` | Session list/create. |
| `GET/PUT/DELETE` | `/api/sessions/<session_id>` | Session detail/update/hard delete. |
| `GET` | `/api/sessions/<session_id>/messages`, `/api/sessions/default` | Durable messages/default session. |
| `POST` | `/api/sessions/<session_id>/archive`, `/restore` | Archive/restore a session. |
| `GET` | `/api/runs/recent`, `/api/runs/<run_id>` | Recent run list/run detail. |
| `GET/POST` | `/api/workspaces` | Workspace list/create. |
| `DELETE` | `/api/workspaces/<ws_id>` | Delete a workspace. |
| `POST` | `/api/workspaces/<ws_id>/rename` | Rename a workspace. |
| `GET` | `/api/workspaces/<ws_id>/state`, `/status`, `/history`, `/runs`, `/traces` | Workspace state/history/run/trace projections. |
| `GET` | `/api/workspaces/<ws_id>/runs/<run_id>`, `/artifacts`, `/trace` | One run and its evidence/trace projections. |
| `POST` | `/api/workspaces/<ws_id>/runs/<run_id>/report` | Create a run report. |
| `GET` | `/api/workspaces/<ws_id>/reports`, `/reports/<artifact_id>/content` | Report list/content. |
| `GET` | `/api/workspaces/<ws_id>/selfcheck`, `/storage/health` | Workspace checks. |
| `PUT` | `/api/workspaces/<ws_id>/settings` | Workspace settings. |
| `POST` | `/api/workspaces/batch-delete` | Explicit batch workspace deletion. |

Archive/retention routes are also workspace-scoped: `GET /api/workspaces/<ws_id>/archive/items`,
`/archive/preview`, `/archive/audits`, `/archive/audits/<audit_id>` and
`POST /archive/apply`, `/archive/restore`; retention has the analogous
`GET /retention/preview`, `/retention/audits`, `/retention/audits/<audit_id>`
and `POST /retention/apply` routes.

## Artifacts, files, knowledge, memory, reports and reviews

| Method | Path | Purpose |
| --- | --- | --- |
| `GET/POST` | `/api/workspaces/<ws_id>/artifacts` | Artifact list/create. |
| `GET/DELETE` | `/api/workspaces/<ws_id>/artifacts/<artifact_id>` | Artifact detail/hard delete. |
| `GET` | `/content`, `/review-items`, `/summarize` below one artifact | Artifact content, review items and summary. |
| `POST` | `/promote` below one artifact; `/artifacts/upload`, `/artifacts/batch-delete` | Promote/upload/explicit batch delete. |
| `GET` | `/api/storage/overview`, `/files`, `/events` | Managed-file storage projections. |
| `GET/DELETE` | `/api/storage/files/<file_id>` | Managed-file detail/hard delete. |
| `GET` | `/content`, `/preview`, `/relations` below one managed file | File content/preview/relations. |
| `GET` | `/api/knowledge/sources`, `/search`, `/chunks/<chunk_id>` | Knowledge sources/search/chunk. |
| `POST` | `/api/knowledge/upload`, `/sources/from-artifact`, `/sources/<source_id>/reindex` | Knowledge ingestion/reindex. |
| `GET/PATCH/DELETE` | `/api/knowledge/sources/<source_id>` | Knowledge source lifecycle. |
| `GET` | `/api/memory/status`, `/list`; `POST /search`, `/write`, `/confirm`, `/reject`, `/batch-delete`; `DELETE /<memory_id>` | Governed memory lifecycle. |
| `POST` | `/api/reports/create` | Create a report. |
| `PUT` | `/api/review-items/<item_id>` | Update a review item. |
| `GET/POST` | `/api/workspaces/<ws_id>/review-items` | Workspace review-item lifecycle. |

## Jobs

| Method | Path | Purpose |
| --- | --- | --- |
| `GET/POST` | `/api/jobs` | Job list/create. |
| `GET/DELETE` | `/api/jobs/<job_id>` | Job detail/hard delete. |
| `POST` | `/api/jobs/<job_id>/cancel`, `/retry` | Cancel/retry. |
| `GET` | `/api/jobs/<job_id>/events`, `/logs`, `/artifacts` | Job evidence projections. |
| `POST` | `/api/jobs/worker/run-once` | Run one worker iteration. |
| `GET` | `/api/jobs/worker/status` | Worker status. |

Job deletion is hard deletion. The JSON body must contain the explicit
`workspace_id` and `confirmation: "DELETE <job_id>"`. Queued or running jobs
return conflict; cancel and wait for a terminal state first.

## Tools, capabilities and extensions

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/tools/catalog`, `/api/tools/permissions` | Canonical tool catalog/permission projection. |
| `POST` | `/api/tools/dry-run` | Tool dry-run metadata. |
| `GET` | `/api/capabilities`, `/api/workbench/skills` | Capability catalog and server-projected workbench Skills. |
| `GET` | `/api/extensions`, `/api/extensions/repository` | Installed extension and repository catalog. |
| `POST` | `/api/extensions/<extension_id>/enable`, `/disable`, `/migrate`, `/uninstall` | Extension lifecycle. |
| `GET` | `/api/extensions/<extension_id>/quota` | Extension quota projection. |
| `POST` | `/api/extensions/repository/publish`, `/repository/<extension_id>/<version>/install` | Package publishing/install. |

`network.operations` owns its namespaced business objects:

| Method | Path |
| --- | --- |
| `GET/POST` | `/api/extensions/network.operations/regions`, `/devices`, `/connections`, `/skills`, `/scripts`, `/inspections` |
| `GET/PUT/DELETE` | `/api/extensions/network.operations/regions/<region_id>`, `/devices/<device_id>`, `/connections/<connection_id>`, `/skills/<skill_id>`, `/scripts/<script_id>` |
| `POST` | `/connections/<connection_id>/test`, `/inspections/<task_id>/cancel`, `/inspections/<task_id>/retry` |
| `GET` | `/inspections/<task_id>`, `/inspections/<task_id>/evidence` |

The relative paths in the last table are under
`/api/extensions/network.operations`. Device, connection and Skill deletion are
their domain lifecycle operations; Skill selection is only an authorization
scope and performs no device I/O.

## Workflows, identity and administration

| Method | Path | Purpose |
| --- | --- | --- |
| `GET/POST` | `/api/workflows` | Workflow list/create. |
| `GET/PUT/DELETE` | `/api/workflows/<workflow_id>` | Workflow lifecycle. |
| `GET/POST` | `/api/workflows/<workflow_id>/runs` | Workflow runs. |
| `GET` | `/api/workflow-runs/<run_id>`, `/api/workflow-templates` | Run/template projections. |
| `POST` | `/api/workflow-runs/<run_id>/cancel`, `/api/workflow-templates/<template_id>/instantiate` | Cancel/instantiate. |
| `GET/POST` | `/api/identity/users`, `/api/identity/organizations`, `/api/identity/organizations/<organization_id>/memberships` | Identity lifecycle. |
| `PUT/DELETE` | `/api/identity/users/<username>` | User update/delete. |
| `GET` | `/api/admin/production`, `/api/admin/backups`, `/api/admin/operation-ledger` | Administrator projections. |
| `POST` | `/api/admin/backups`, `/api/admin/backups/prune`, `/api/admin/backups/<backup_id>/restore`, `/api/admin/operation-ledger/<operation_id>/resolve` | Verified administration actions. |

## Error shape

应用定义的 API 错误返回 JSON，通常含 `ok: false` 和稳定错误标识；个别路由还会
附带安全的校验详情。调用方不应依赖未记录的错误详情字段。

```json
{ "ok": false, "error": "invalid_workspace_id" }
```

Do not infer retry safety from HTTP status alone. For Agent/runtime work, use
the server result lifecycle and the canonical tool/operation-ledger evidence.

## Exact route inventory

The grouped sections above explain the surface. This inventory keeps every
currently registered route pattern explicit; methods separated by `|` are
registered on the same pattern.

```text
POST        /api/ecosystem/import/apply
POST        /api/ecosystem/import/preview
GET         /api/ecosystem/providers
POST        /api/extensions/<extension_id>/disable
POST        /api/extensions/<extension_id>/migrate
POST        /api/extensions/<extension_id>/uninstall
GET|POST    /api/extensions/network.operations/connections
DELETE|GET|PUT /api/extensions/network.operations/connections/<connection_id>
POST        /api/extensions/network.operations/connections/<connection_id>/test
GET|POST    /api/extensions/network.operations/devices
DELETE|GET|PUT /api/extensions/network.operations/devices/<device_id>
GET|POST    /api/extensions/network.operations/inspections
GET         /api/extensions/network.operations/inspections/<task_id>
POST        /api/extensions/network.operations/inspections/<task_id>/cancel
GET         /api/extensions/network.operations/inspections/<task_id>/evidence
POST        /api/extensions/network.operations/inspections/<task_id>/retry
GET|POST    /api/extensions/network.operations/scripts
DELETE|GET|PUT /api/extensions/network.operations/scripts/<script_id>
GET|POST    /api/extensions/network.operations/skills
DELETE|GET|PUT /api/extensions/network.operations/skills/<skill_id>
POST        /api/extensions/repository/<extension_id>/<version>/install
GET         /api/jobs/<job_id>/artifacts
GET         /api/jobs/<job_id>/logs
POST        /api/jobs/<job_id>/retry
GET         /api/knowledge/chunks/<chunk_id>
GET         /api/knowledge/search
POST        /api/knowledge/sources/<source_id>/reindex
POST        /api/knowledge/sources/from-artifact
DELETE      /api/memory/<memory_id>
POST        /api/memory/batch-delete
POST        /api/memory/confirm
GET         /api/memory/list
POST        /api/memory/reject
POST        /api/memory/search
POST        /api/memory/write
POST        /api/runtime/tasks/<task_id>/checkpoint
GET         /api/runtime/tasks/<task_id>/checkpoints
POST        /api/runtime/tasks/<task_id>/resume
POST        /api/sessions/<session_id>/restore
GET         /api/storage/events
GET         /api/storage/files/<file_id>/content
GET         /api/storage/files/<file_id>/preview
GET         /api/storage/files/<file_id>/relations
POST        /api/workspaces/<ws_id>/archive/apply
GET         /api/workspaces/<ws_id>/archive/audits
GET         /api/workspaces/<ws_id>/archive/audits/<audit_id>
GET         /api/workspaces/<ws_id>/archive/preview
POST        /api/workspaces/<ws_id>/archive/restore
GET         /api/workspaces/<ws_id>/artifacts/<artifact_id>/content
POST        /api/workspaces/<ws_id>/artifacts/<artifact_id>/promote
GET         /api/workspaces/<ws_id>/artifacts/<artifact_id>/review-items
GET         /api/workspaces/<ws_id>/artifacts/<artifact_id>/summarize
POST        /api/workspaces/<ws_id>/artifacts/batch-delete
POST        /api/workspaces/<ws_id>/artifacts/upload
GET         /api/workspaces/<ws_id>/history
GET         /api/workspaces/<ws_id>/jobs
GET         /api/workspaces/<ws_id>/jobs/<job_id>
GET         /api/workspaces/<ws_id>/reports/<artifact_id>/content
POST        /api/workspaces/<ws_id>/retention/apply
GET         /api/workspaces/<ws_id>/retention/audits
GET         /api/workspaces/<ws_id>/retention/audits/<audit_id>
GET         /api/workspaces/<ws_id>/retention/preview
GET         /api/workspaces/<ws_id>/runs/<run_id>/artifacts
GET         /api/workspaces/<ws_id>/runs/<run_id>/trace
GET         /api/workspaces/<ws_id>/status
GET         /api/workspaces/<ws_id>/storage/health
GET         /api/workspaces/<ws_id>/traces
```
