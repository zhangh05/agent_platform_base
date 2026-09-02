# Architecture

LZCore 只有一条运行时主链路：

```text
HTTP / WebSocket / SSE / Job entry
  -> AgentApp.submit_user_message
  -> AgentThread + SessionManager
  -> run_ssot_turn
  -> QueryLoop
  -> typed evidence ledger + goal-driven recovery + LLM function calling + bounded tool loop
  -> ToolRuntimeClient.invoke / ToolRuntime.invoke_raw
  -> canonical handlers
  -> AgentResult + RuntimeEvent timeline
```

## Tool Boundary

底座暴露 17 个通用 canonical tools。`core/tools/tool_namespace.py`、`core/tools/manifest_registry.py`、`core/runtime_engine/contracts.py` 和 `core/tools/canonical_registry.py` 必须数量一致、ID 一致。

工具执行必须经过：

- canonical tool id
- CapabilityManifest
- explicit `requested_by`
- workspace/session/run context
- caller gate
- risk policy
- redacted result
- audit / trace event

## Data Boundary

所有用户数据都以 `workspace_id` 隔离。运行记录、会话、制品、FileStore、记忆、作业和 trace 都属于工作区数据。后端路由不得静默推断工作区；缺失或非法 `workspace_id` 必须返回客户端错误。

## Capability Boundary

`agent/capabilities/catalog.py` 只描述业务能力，不执行工具。业务项目可以添加自己的能力目录，但工具执行仍必须走 canonical registry。

## Memory Boundary

记忆写入由 `MemoryWriteGate` 管控。每轮经验进入持久日志，任务边界或显式记忆指令触发整理，再写入 MemoryStore 并索引到 ContextStore。检索结果以 `data_only` 形式进入运行时提示词，不能变成系统指令。

## Prompt Boundary

`core/runtime_engine/prompt_contract.py` 是生产 QueryLoop 的系统提示词源。历史、记忆、知识和制品摘要都放入明确边界的 data-only 区块；当前用户请求单独隔离。

`core/runtime_engine/evidence.py` 定义请求级证据协议与消费账本。canonical 工具只输出 `evidence_parts` 引用，QueryLoop 负责登记和交付，模型适配器仅在单次调用边界解析图片字节。`core/runtime_engine/batch_compiler.py` 根据工具声明的批处理契约优化独立标量调用，不包含具体工具分支，也不改变依赖图语义。

## Goal-driven recovery boundary

`core/runtime_engine/goal_loop.py` 将每一轮 canonical ToolResult 规范为观察。可恢复的只读失败生成服务端拥有的恢复目标，目标未被真实读取证据满足时，最终门禁止模型提前结束。模型可修正参数、缩小范围或调用其他已授权读取能力；跨工具恢复必须在 `plan_goal_ids` 中显式引用目标 ID，避免同一 workspace 或设备内的无关调用错误结案。

目标与断言通过 TaskState 以有界标识持久化，并在受信任的续跑合同中恢复。通用目标的当前最大尝试数为三（含原始失败）；领域证据目标使用独立的最终重规划预算。预算耗尽后状态为 `blocked`；有已验证覆盖时任务投影为 `partial`。授权、策略、取消、凭据和未知外部写入不进入自动恢复。新扩展通过 `runtime_recoveries` 列表发布领域恢复计划；QueryLoop 仍读取旧的单数 `runtime_recovery` 作为兼容输入，但新代码不得再产生它。完整契约见 [Loop Engineering](LOOP_ENGINEERING.md)。
