# API

Base URL: `http://localhost:8011`

所有工作区数据接口都要求显式、合法的 `workspace_id`。缺失或非法工作区 ID 返回 400。

## Agent

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/agent/message` | 执行一次用户请求 |
| `GET` | `/api/agent/sse/stream/<session_id>?workspace_id=<ws>` | 订阅会话运行事件 |
| `WS` | `/ws/agent` | WebSocket 对话流 |

## Runtime

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/runtime/summary` | 工具和能力概览 |
| `GET` | `/api/runtime/health?workspace_id=<ws>` | 运行时健康 |
| `GET` | `/api/runtime/selfcheck?workspace_id=<ws>` | 自检 |
| `GET` | `/api/runtime/tasks?workspace_id=<ws>&session_id=<id>` | 后台任务列表 |
| `GET` | `/api/runtime/tasks/<task_id>?workspace_id=<ws>` | 任务详情 |
| `GET` | `/api/runtime/tasks/<task_id>/events?workspace_id=<ws>` | 任务事件 |
| `GET` | `/api/runtime/tasks/<task_id>/checkpoints?workspace_id=<ws>` | 任务检查点 |
| `POST` | `/api/runtime/tasks/<task_id>/cancel?workspace_id=<ws>` | 取消任务 |
| `POST` | `/api/runtime/tasks/<task_id>/resume?workspace_id=<ws>` | 恢复任务 |
| `POST` | `/api/runtime/tasks/<task_id>/steps/<step_id>/retry?workspace_id=<ws>` | 重试安全失败步骤 |

## Tools And Capabilities

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/tools/catalog` | canonical 工具目录与动作级执行契约 |
| `POST` | `/api/tools/dry-run?workspace_id=<ws>` | 工具 dry-run 元数据 |
| `GET` | `/api/capabilities` | 能力目录 |

## Workspace Data

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/sessions?workspace_id=<ws>` | 会话列表 |
| `GET` | `/api/sessions/<session_id>/messages?workspace_id=<ws>` | 会话消息 |
| `GET` | `/api/runs/recent?workspace_id=<ws>&session_id=<id>` | 最近运行记录 |
| `GET` | `/api/workspaces/<ws>/artifacts` | 制品列表 |
| `GET` | `/api/workspaces/<ws>/artifacts/<artifact_id>` | 制品详情 |
| `GET` | `/api/storage/overview?workspace_id=<ws>` | 数据中心概览 |
| `GET` | `/api/storage/files?workspace_id=<ws>` | FileStore 文件列表 |
| `GET` | `/api/memory/search?workspace_id=<ws>` | 记忆检索 |
| `POST` | `/api/memory/write` | 通过记忆门控写入 |

## Jobs

终态任务可通过 `DELETE /api/jobs/<job_id>` 硬删除；请求体必须包含工作区和精确确认字符串
`DELETE <job_id>`。排队或运行中的任务返回 409，必须先取消并等待进入终态。

## Error Shape

```json
{ "ok": false, "error": "invalid_workspace_id" }
```
