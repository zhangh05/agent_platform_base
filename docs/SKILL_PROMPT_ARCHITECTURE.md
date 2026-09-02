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

工作台仅选择已发布 Skill；服务端在每次调用前重新读取其启用状态、设备、连接、允许工具与 `configuration_write`。未选 Skill 时不注入网络设备专用上下文。选择 Skill 不预连设备；模型在需要时连接目标，连接过期时由驱动恢复。

被拒绝的单命令只读 CLI 查询可被扩展映射为 canonical fact，并通过 `runtime_recoveries` 让 QueryLoop 尝试受限的替代读取。文档检索只用于改正后续命令，不是实时设备事实。写入未知只允许 read-back/reconcile，不能作为循环重放入口。
