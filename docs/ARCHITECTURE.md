# 架构边界

LZCore 的边界由执行链路而非页面或提示词决定：`backend/` 接收请求，`agent/` 建立会话与 runtime，`core/runtime_engine/` 运行 QueryLoop，`core/tools/` 执行受治理工具，`storage/` 与相应 store 持久化事实。

## 运行时

`SSOTRuntimeEngine` 以 `TaskState` 保存一次任务的受信任状态，并投影为 `AgentResult`、消息、事件和运行记录。浏览器、对话历史和工具输出只能提供证据，不能修改服务端解析的 Skill 范围、结构性执行边界或恢复目标。运行时没有整轮累计时间预算；单次外部调用自身的超时作为工具事实返回。

运行时不以 token 预算截断模型可见的会话、工具输出或证据。若 provider 返回未完成输出，QueryLoop 把已接收内容保留在同一会话中并继续生成，直到获得完整的模型答复或用户取消。

## 工具

工具通过 `ToolRuntimeClient` 统一进入 manifest、调用方检查、Skill 范围、executor 和审计。通用工具由 canonical registry 管理；扩展工具仍须使用同一执行边界。网络设备命令没有平台危险命令策略或配置写入开关，设备账号决定实际命令权限。

## 恢复

QueryLoop 只对可恢复的只读观察建立有界目标。`plan_goal_ids` 只关联替代调用，运行时还会校验能力和资源目标；正向 evidence claim 必须来自成功、已终止的结果。每个领域目标独立计量重规划次数，阻塞或完成后不会被投影重新打开。写入、取消、Skill 范围外调用和写入结果未知都不会触发平台自动重试；完整工具结果始终交回模型决定下一步。平台保留经过完整合同校验的 `runtime_recoveries`；网络 CLI 语义纠错使用模型可读反馈，由模型在下一轮选择动作。

## 委派

子 Agent 是同一运行时内的委派回合，不是低权限旁路。profile 表示任务分工；子 Agent 继承父 Agent 的完整工具面。若父 Agent 选中了 Skill，服务端在创建时重新解析 Skill 并继承父会话当前设备与连接切片，随后由同一 ToolRuntimeClient 再次执行范围检查。子 Agent 没有累计运行时间限制，支持显式用户取消。

## 数据与界面

所有数据操作验证 `workspace_id`。Zustand 仅管理前端状态；服务端才是权限、任务状态和审计事实来源。WebSocket、SSE 与 HTTP 返回的结果均以 `AgentResult` 及对应的运行时记录为准。
