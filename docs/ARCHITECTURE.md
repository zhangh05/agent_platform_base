# 架构边界

LZCore 的边界由执行链路而非页面或提示词决定：`backend/` 接收请求，`agent/` 建立会话与 runtime，`core/runtime_engine/` 运行 QueryLoop，`core/tools/` 执行受治理工具，`storage/` 与相应 store 持久化事实。

## 运行时

`SSOTRuntimeEngine` 以 `TaskState` 保存一次任务的受信任状态，并投影为 `AgentResult`、消息、事件和运行记录。浏览器、对话历史和工具输出只能提供证据，不能修改 runtime 的授权、预算或恢复目标。

## 工具

工具通过 `ToolRuntimeClient` 统一进入 manifest、调用方检查、风险策略、产品授权、executor、脱敏和审计。通用工具由 canonical registry 管理；扩展工具仍须使用同一执行边界。

## 恢复

QueryLoop 只对可恢复的只读观察建立有界目标。`plan_goal_ids` 只关联替代调用，运行时还会校验能力和资源目标；正向 evidence claim 必须来自成功、已终止的结果。每个领域目标独立计量最终重规划次数，阻塞或完成后不会被投影重新打开。写入、取消、授权/策略拒绝和写入结果未知不会自动重试。平台保留经过完整合同校验的 `runtime_recoveries`；网络 CLI 语义纠错使用模型可读反馈，由模型在下一轮选择动作。

## 数据与界面

所有数据操作验证 `workspace_id`。Zustand 仅管理前端状态；服务端才是权限、任务状态和审计事实来源。WebSocket、SSE 与 HTTP 返回的结果均以 `AgentResult` 及对应的运行时记录为准。
