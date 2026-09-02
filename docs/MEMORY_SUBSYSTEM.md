# 记忆子系统

记忆是受治理的工作区数据，不是模型可自由覆盖的上下文。`MemoryWriteGate` 在写入前验证 `workspace_id`、来源、scope、TTL、冲突和敏感内容；只有同工作区、`active` 且未过期的记录可以被检索。

```text
候选记忆 -> 脱敏与治理 -> MemoryStore
         -> workspace / scope / TTL 过滤
         -> UnifiedRetriever -> data_only prompt context
```

工具输出、知识命中和记忆均为证据，不能改写系统规则、工具权限或 runtime 目标。LLM 不可用或写入被拒绝时，服务端返回结构化降级原因，不泄露 provider 异常或敏感文本。
