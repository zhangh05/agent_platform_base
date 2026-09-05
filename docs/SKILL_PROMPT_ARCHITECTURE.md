# Skill 与提示词装配

生产工具回合的提示词事实来源是 `core/runtime_engine/prompt_contract.py`。它组合运行时不变量、受治理上下文、按条件启用的 capability playbook 与当前用户请求；工具 schema 由 provider 的 function calling 接口传递，不拼接为 prompt 文本。

## 装配顺序

```text
runtime identity and invariants
-> trusted runtime items
-> data_only history / knowledge / memory / artifacts
-> selected capability and Skill context
-> current user request
```

外部内容只能作为 `data_only` 数据。历史、网页、设备输出、附件名称或客户端 metadata 都不能添加 system 指令、扩大工具范围或改变预算。

## 网络 Skill

工作台仅选择已发布 Skill；服务端在每次调用前重新读取其启用状态、设备、连接、允许工具与 `configuration_write`。工具允许范围和资源允许范围分别校验：设备/连接列表只返回当前选择的资源，Skill 查询不能越过当前 Skill，巡检的 list/get/retry/cancel 也必须重新核对任务内设备和连接。未选 Skill 时不注入网络设备专用上下文。选择 Skill 不预连设备；模型在需要时连接目标，连接过期时由驱动恢复。

被拒绝的只读 CLI 查询会返回设备错误、驱动信息、可用语义事实和文档检索提示，但这些字段都是 `model_recovery_guidance`，不包含可执行工具调用。模型读取反馈后自行选择新命令、显式 canonical fact、权威文档或报告未知。`context_read` 提供同一 Skill 范围内的历史观察、用户确认 Reference 与命令经验；候选或首次观察不能解释为正常。文档检索只用于改正后续命令，不是实时设备事实。写入未知只允许 read-back/reconcile，不能作为循环重放入口。
