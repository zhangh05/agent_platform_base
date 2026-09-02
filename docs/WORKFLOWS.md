# 工作流

工作流是工作区范围内的 DAG。节点引用 canonical platform tool 或已安装扩展工具，不能直接调用扩展 handler，因此每个节点仍受 `ToolRuntimeClient` 的 schema、策略、授权、脱敏和审计保护。

## 定义

```json
{
  "workflow_id": "readonly_inspection",
  "name": "批量只读巡检",
  "failure_policy": "fail_fast",
  "nodes": [{
    "node_id": "inspect",
    "tool_id": "network.operations.inspection",
    "arguments": {"connection_ids": "${input.connection_ids}"}
  }]
}
```

节点 ID 唯一，依赖必须存在且无环；引用只能读取传递依赖的输出。定义包含 1–30 个节点，单节点解析后的输入上限为 1 MiB。密码、token、私钥和授权字段不得作为持久化定义的一部分。

## 执行语义

独立只读节点在并发上限内执行；写入和其他有副作用节点形成顺序屏障。结果按稳定的节点顺序记录。`POST /api/workflows/<workflow_id>/runs` 默认同步执行，设置 `enqueue: true` 后创建 durable `workflow_run` 作业。

worker 队列是 at-least-once。涉及外部写入的 handler 必须使用作业 ID 与节点 ID 构造幂等键。工作流的 `failure_policy` 仅管理 DAG 节点，不创建第二套 LLM 失败恢复机制，也不会自动重放写操作。

## 与对话运行时的关系

QueryLoop 的只读证据恢复和工作流节点失败是两种不同语义。只有工具结果处于对话回合中时，QueryLoop 才可能消费其安全恢复指令；普通工作流会记录结果，由工作流所有者决定后续操作。
