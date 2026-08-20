# Frontend

前端是 React 18 + TypeScript + Vite 应用，使用 Zustand 管理本地状态，并通过 `frontend/src/api` 的 typed API helpers 访问后端。

## Main Screens

- `AgentWorkbench` (`/workbench`): 对话、运行时间线、工具调用、审批。
- `RunsPage` (`/runs`): 最近运行记录和 trace。
- `CapabilityCenter` (`/capabilities`): 能力目录和推荐工具。
- `JobsPage` (`/jobs`): 后台作业。
- `KnowledgeLibrary` (`/knowledge`): 知识库。
- `DataCenter` (`/data`): 文件、制品、引用关系和生命周期。
- `MemoryPage` (`/memory`): 记忆记录。
- `Diagnostics` (`/diagnostics`): 健康检查、自检、提示词、策略。
- `Settings` (`/settings`): LLM 和运行时配置。

## Workbench Data Flow

```text
user sends message
  -> append optimistic user message
  -> create one assistant placeholder
  -> WebSocket or HTTP stream
  -> merge backend messages by stable id / role / content / timestamp
  -> render chat + timeline from store
```

后端持久消息是事实来源；前端乐观消息只是临时状态，最终必须合并而不是重复追加。

## Workspace Contract

所有触碰用户数据的 API helper 都必须传 `currentWorkspaceId`。空工作区 ID 要在 UI 上显示错误，不得静默调用后端。

## Tool And Capability UI

工具目录来自 17 个通用 canonical tools。前端可以显示友好名称，但 API payload 必须使用 canonical tool id。

## Validation

```bash
npm --prefix frontend run typecheck
npm --prefix frontend test -- --run
```
