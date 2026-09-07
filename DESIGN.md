# LZCore 当前设计

本文只陈述现行实现。历史迁移与清理记录见 `docs/CODE_CLEANUP_20260831.md`，不能作为当前接口或架构依据。

## 设计目标

LZCore 将模型推理放在统一运行时中：模型可以选择工具、组织证据和决定下一步；运行时只维护工具 schema、资源身份、Skill 范围、传输生命周期与数据边界。

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

调用级 `dry_run` 默认关闭并拒绝执行。只有显式声明并实现无副作用 preview handler 的工具才能启用；普通 handler 不能把 `dry_run=True` 当作可信保护。公开 `/api/tools/dry-run` 只生成策略与调用元数据，不进入工具 handler。

## 目标驱动恢复

可恢复的只读失败会形成受限的恢复目标。模型可通过 `plan_goal_ids` 关联替代调用，但关联本身不是完成证据；运行时仍校验能力、资源身份以及 evidence kind、fact、target。只有成功且终止的只读观察可关闭目标。通用恢复预算包含原始失败；每个领域证据目标分别维护最终重规划预算，`passed` 与 `blocked` 不会被后续投影重新打开。预算耗尽后系统收敛为 `partial` 或 `blocked`，不会无限重试。

平台仍支持领域无关的 `runtime_recoveries` 只读证据合同；每项必须完整声明 kind、tool、arguments 和证据目标，且不能借此授权新工具、资源、凭据或写操作。网络扩展不使用该合同替模型选择命令：CLI 语法拒绝只产生 `model_recovery_guidance`，下一轮由模型在新命令、显式语义采集、权威文档或报告未知之间自主决定。

`core/runtime_engine/context_contract.py` 只定义领域无关的 Observation 来源/时间/完整性和 Reference 生命周期。Observation 永远只表示某一时点的事实，不能自称“正常”。网络巡检可从完整或部分观察生成 `candidate`；只有完整观察经用户显式确认后才成为当前 `confirmed` Reference，同范围旧 Reference 进入 `superseded`，也可显式 `invalidated`。

## 授权与设备执行

平台没有危险命令策略或配置写入开关。每个已发布并选中的网络 Skill 都内建设备读取和 `configure` 能力；服务端仅实时验证 Skill 已启用、工具在其允许范围内、目标连接属于该 Skill。设备账号是命令执行权限的最终控制者。范围外调用以结构化结果返回模型。

Skill 可选开启“执行前要求审批”。这不是平台权限或风险判定：开启时，`approval` 扩展会冻结模型提出的精确 `configure` 调用（目标、连接版本、Skill 版本、命令顺序和文本、超时），计算 digest 后交给界面决定。批准前不接触设备；批准时服务端再次核验冻结对象仍有效，再经同一 `ToolRuntimeClient` 执行。拒绝、取消、失效与实际执行结果都是结构化事实，完整回注模型继续完成原任务。关闭时扩展不拦截任何调用。

外部操作账本记录事实，不替模型决定后续动作。结果未知时，完整结果照常返回模型；模型可自行选择 read-back、继续配置、重试或向用户说明状态。运行时不自动重放命令、不冻结后续写入，也不以风险判断替代设备账号权限。

## 数据、记忆与提示词

- 每次跨数据访问必须使用验证后的 `workspace_id`。
- 本机单节点密钥由 `storage/secret_store.py` 使用 `cryptography.fernet.Fernet` 加密；主密钥来自 `LZCORE_MASTER_KEY` 或 `LZCORE_MASTER_KEY_FILE`，密文不进入普通记录或客户端响应。
- `MemoryWriteGate` 在写入前处理 workspace、来源、范围、TTL、冲突与脱敏；仅 active、未过期、同工作区记录可检索。
- 历史、知识、记忆、制品和工具输出是 `data_only` 证据，不能覆盖运行时规则。
- 生产工具回合的提示词事实来源是 `core/runtime_engine/prompt_contract.py`；`prompts/templates/` 服务于安全的非工具说明与总结任务。

## 前端职责

前端以 Zustand 管理界面状态，展示会话、任务、时间线、工具卡、工作区和数据中心。它不能自行判定权限、编造任务终态、注入恢复目标或把本地缓存当作实时外部事实。
