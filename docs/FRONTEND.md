# 前端

前端位于 `frontend/`，使用 React、Vite、TypeScript 与 Zustand。它是联智中枢的工作台，不是运行时、授权或审计逻辑的副本。

## 主要界面

- 工作台：会话、工具调用、最终答复、Skill 选择和实时状态。
- 任务：运行记录、事件、作业状态和终态删除入口。
- 资料中心：工作区制品、知识源、记忆与文件。
- 能力中心：平台能力、扩展和业务对象入口。
- 系统管理：运行健康、提供方、存储、备份和操作账本。

页面导航的实际路径以 `frontend/src/app/App.tsx` 为准；API 请求以对应客户端模块为准。

## 数据流

```text
Zustand store + route state
  -> HTTP / WebSocket / SSE client
  -> backend API
  -> AgentResult、runtime event、workspace resource
  -> UI projection
```

登录态使用 HttpOnly Cookie；受控 token 流仅从 sessionStorage 读取，不能出现在 URL、localStorage、构建变量或日志中。分离部署时 `VITE_API_BASE` 同时决定 HTTP、WebSocket 和 SSE 的 API origin。

## 显示规则

前端显示服务端给出的 `execution_outcome`、`tool_execution_outcome`、恢复目标和结构化错误。单个工具失败不能被渲染为整个任务失败；外部写入未知应明确呈现为待 read-back/reconcile，不能提供重放原操作的按钮。浏览器不能自行补全 `workspace_id`、设备权限、Skill 范围或恢复目标。

工作台页面由 `AgentWorkbench.tsx` 组织数据与发送生命周期，`WorkbenchHeader`、`WorkbenchComposer`、`WorkbenchEmptyState`、`TaskProgressPanel`、`ResultInline` 和 `ThinkingBlock` 分别承担会话栏、输入、空状态、实时进度、结果投影和推理内容展示。组件拆分不能复制服务端状态判定；交互控件使用独立原生按钮，异步操作只有在真实成功后才显示成功反馈。

## 验证

```bash
cd frontend
npm test -- --run
npm run build
```

涉及真实交互、代理、认证或发布路径时，还要用浏览器验证实际页面请求和服务端返回，而不能只看组件测试。
