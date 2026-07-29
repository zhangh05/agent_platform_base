# Agent Platform Base Design

本设计文档只描述当前实现，不保留历史架构、历史工具名或旁路实现。

## 设计原则

1. **单主链**：所有用户请求进入同一条运行时链路，避免旁路执行。
2. **硬边界**：工具必须经过 manifest、caller、risk、approval、redaction、audit。
3. **显式 workspace**：所有跨数据边界操作必须带已验证 `workspace_id`。
4. **能力只描述，工具才执行**：业务能力目录用于提示和 UI，不参与 handler 注册。
5. **当前口径优先**：不为已移除 API、历史工具名、过期文档叙述保留分支。
6. **持久化事实清晰**：run、message、artifact、memory 各自由其 canonical store 原子写入，不再复制到第二套事件存储。
7. **Function Calling**：工具 schema 通过 LLM 的 `tools` 参数传递，不文本 dump 到 prompt。

## 主链路

```mermaid
sequenceDiagram
  participant UI as Frontend
  participant API as Flask API / WS
  participant App as AgentApp
  participant SSOT Runtime as SSOTRuntimeEngine
  participant LLM as LLM Provider
  participant Tools as ToolRuntimeClient
  participant Store as Durable Stores

  UI->>API: message(workspace_id, session_id, text)
  API->>App: submit_user_message()
  App->>SSOT Runtime: run_ssot_turn()
  SSOT Runtime->>LLM: QueryLoop planning/tool-call iteration
  SSOT Runtime->>SSOT Runtime: validate visible tool contract + budget
  SSOT Runtime->>Tools: invoke canonical tool calls
  Tools->>Tools: manifest/caller/policy/redaction/audit
  Tools-->>SSOT Runtime: ToolResult
  SSOT Runtime->>LLM: same QueryLoop produces the evidence-based response
  SSOT Runtime->>Store: messages, events, trace, run projection
  SSOT Runtime-->>API: AgentResult
  API-->>UI: stream/final/timeline
```

## Durable Runtime

运行时状态由四类数据构成：

- `TaskState`：一次用户任务的权威状态。
- `RuntimeStep`：context、model、tool、final 等阶段步骤。
- `RuntimeEvent`：前端时间线和审计事件来源。
- `RuntimeCheckpoint`：中断、审批、失败恢复的快照。

SSOT Runtime 主链将执行结果投影为 `AgentResult`、message、run 和 trace；长任务类能力仍使用 durable task/checkpoint API 管理自己的可取消状态。

## Tool Runtime

`ToolRuntimeClient.invoke()` 是唯一合法入口。执行顺序：

```text
canonical tool id
  -> CapabilityManifest
  -> requested_by caller gate
  -> ToolPolicy
  -> approval/interrupt when needed
  -> ToolExecutor
  -> redaction
  -> trace/audit
  -> ToolResult
```

当前只有 24 个 canonical tool。`handler_id` 是内部实现细节，不暴露给 LLM、前端或公共 API。SSOT Runtime 节点不会直接调用 handler，只能通过 `ToolRuntimeClient.invoke()` 进入工具边界。

## Capability Catalog

`agent/capabilities/catalog.py` 是业务能力目录，当前 13 个能力，全部 enabled。目录只提供：

- 能力说明
- 推荐 canonical tool
- prompt hint
- safety note
- 前端展示数据

它不注册工具、不控制权限、不分发 handler。

## Approval

审批只用于高危或破坏性操作。普通 read/list/query 不应因为工具类别本身被阻断。审批生命周期是 durable interrupt：

```text
tool policy requires approval
  -> pending approval + checkpoint
  -> TaskState waiting_approval
  -> user approve/reject/edit_args
  -> resume or fail
```

## Memory Governance

记忆写入必须经过 `MemoryWriteGate`：

1. 校验 workspace。
2. 先检测密钥模式，再脱敏。
3. 根据来源、置信度、scope、TTL、冲突判断状态。
4. 只有 `active`、未过期、同 workspace 的记录可检索。

LLM 失败时不能泄露异常文本，降级原因必须结构化。

## Prompt 与上下文

生产提示词只有一个事实来源：`core/runtime_engine/prompt_contract.py`。它定义
QueryLoop 的执行契约、最终答复契约、子 Agent system 约束，以及每轮上下文的
分隔格式。生产主链不再经过第二套 PromptCompiler 或 block assembly。

每轮输入明确分为 runtime identity、`data_only` conversation history、
`data_only` governed context 和 current user request。历史、记忆、知识、制品和
工具输出只能作为证据，不能覆盖 system 规则。子 Agent 的角色、工具范围和预算
进入 system prompt，委派目标仍保持为纯用户任务。

工具 schema 通过模型 tools 字段提供；system prompt 只提供策略和工具选择原则，不内联长工具清单。
所有 canonical 工具仍对主管线 LLM 可见，不使用关键词规则裁剪模型能力。

## Frontend

前端以任务工作台为中心：

- 对话视图展示用户消息、LLM 回复、工具卡、审批气泡。
- 时间线视图读取 `AgentResult.events` 和 runtime state。
- 会话、最近运行、workspace 全部显式绑定。
- 前端不得制造默认 workspace，也不得自行补已移除的 API 格式。
