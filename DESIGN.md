# LZCore 当前设计

本文只陈述现行实现。历史迁移与清理记录见 `docs/CODE_CLEANUP_20260831.md`，不能作为当前接口或架构依据。

## 设计目标

LZCore 将模型推理放在受治理的运行时中：模型可以选择工具和组织证据，但不能改变工具授权、风险策略、数据边界、预算或外部写入保护规则。

## 请求链路

```text
Frontend / HTTP / WebSocket
  -> AgentApp
  -> SSOTRuntimeEngine
  -> QueryLoop
  -> ToolRuntimeClient
  -> canonical tool / extension tool
  -> durable stores + AgentResult
```

`AgentResult` 是 API 与前端的统一结果投影。会话消息、运行记录、事件、trace 和制品分别由其 canonical store 写入；不维护第二套“同步副本”。

## 任务与工具的不同结果

`execution_outcome` 表示用户目标：例如 `complete`、`partial`、`blocked` 或 `unknown`。`tool_execution_outcome` 描述本次工具尝试。某一调用失败后，如后续取得替代的真实只读证据，任务仍可以完成；反之，外部写入结果未知始终保持 `unknown`，直到 read-back/reconcile 给出事实。

## 工具边界

所有工具经由 `ToolRuntimeClient.invoke()` 进入。`core/tools/manifest_registry.py` 是 manifest 的当前注册表：

```text
tool id -> manifest -> caller gate -> policy / authorization
        -> executor -> redaction -> trace / audit -> ToolResult
```

`handler_id` 是内部实现，不向模型、前端或 API 暴露。工具 schema 通过 provider 的 function calling `tools` 字段提供，不能通过 prompt 文本伪造工具接口。

## 目标驱动恢复

可恢复的只读失败会形成受限的恢复目标。模型后续调用必须通过 `plan_goal_ids` 显式关联目标，并采用实质不同的安全观察策略：纠正参数、缩小范围、使用已授权的替代只读能力，或先查询权威资料再进行新的实时观察。通用恢复预算包含原始失败；领域证据目标使用独立的最终重规划预算。预算耗尽后系统收敛为 `partial` 或 `blocked`，不会无限重试。

扩展以 `runtime_recoveries` 列表发布只读证据指令。它不能借此授权新工具、设备、连接、凭据、写操作或主机命令。旧的单数 `runtime_recovery` 仅作为 QueryLoop 输入兼容存在，新代码不得产生它。

## 授权与外部写入

平台没有审批等待状态。网络设备写入由服务端实时验证已发布 Skill、`configuration_write`、设备/连接范围和允许工具范围。策略拒绝和未授权调用以结构化结果返回模型；不创建审批记录或后台续跑。

非幂等外部写入不能因恢复、worker 重启或“失败重试”被自动重放。结果未知时操作账本保留事实，后续只能执行受控 read-back/reconcile。

## 数据、记忆与提示词

- 每次跨数据访问必须使用验证后的 `workspace_id`。
- 本机单节点密钥由 `storage/secret_store.py` 使用 `cryptography.fernet.Fernet` 加密；主密钥来自 `LZCORE_MASTER_KEY` 或 `LZCORE_MASTER_KEY_FILE`，密文不进入普通记录或客户端响应。
- `MemoryWriteGate` 在写入前处理 workspace、来源、范围、TTL、冲突与脱敏；仅 active、未过期、同工作区记录可检索。
- 历史、知识、记忆、制品和工具输出是 `data_only` 证据，不能覆盖运行时规则。
- 生产工具回合的提示词事实来源是 `core/runtime_engine/prompt_contract.py`；`prompts/templates/` 服务于安全的非工具说明与总结任务。

## 前端职责

前端以 Zustand 管理界面状态，展示会话、任务、时间线、工具卡、工作区和数据中心。它不能自行判定权限、编造任务终态、注入恢复目标或把本地缓存当作实时外部事实。
