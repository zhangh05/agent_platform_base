# Architecture

Agent Platform Base 只有一条运行时主链路：

```text
HTTP / WebSocket / SSE / Job entry
  -> AgentApp.submit_user_message
  -> AgentThread + SessionManager
  -> run_ssot_turn
  -> QueryLoop
  -> typed evidence ledger + LLM function calling + bounded tool loop
  -> ToolRuntimeClient.invoke / ToolRuntime.invoke_raw
  -> canonical handlers
  -> AgentResult + RuntimeEvent timeline
```

## Tool Boundary

底座暴露 16 个通用 canonical tools。`core/tools/tool_namespace.py`、`core/tools/manifest_registry.py`、`core/runtime_engine/contracts.py` 和 `core/tools/canonical_registry.py` 必须数量一致、ID 一致。

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

所有用户数据都以 `workspace_id` 隔离。运行记录、会话、制品、FileStore、记忆、审批、作业和 trace 都属于工作区数据。后端路由不得静默推断工作区；缺失或非法 `workspace_id` 必须返回客户端错误。

## Capability Boundary

`agent/capabilities/catalog.py` 只描述业务能力，不执行工具。业务项目可以添加自己的能力目录，但工具执行仍必须走 canonical registry。

## Memory Boundary

记忆写入由 `MemoryWriteGate` 管控。每轮经验进入持久日志，任务边界或显式记忆指令触发整理，再写入 MemoryStore 并索引到 ContextStore。检索结果以 `data_only` 形式进入运行时提示词，不能变成系统指令。

## Prompt Boundary

`core/runtime_engine/prompt_contract.py` 是生产 QueryLoop 的系统提示词源。历史、记忆、知识和制品摘要都放入明确边界的 data-only 区块；当前用户请求单独隔离。

`core/runtime_engine/evidence.py` 定义请求级证据协议与消费账本。canonical 工具只输出 `evidence_parts` 引用，QueryLoop 负责登记和交付，模型适配器仅在单次调用边界解析图片字节。`core/runtime_engine/batch_compiler.py` 根据工具声明的批处理契约优化独立标量调用，不包含具体工具分支，也不改变依赖图语义。
