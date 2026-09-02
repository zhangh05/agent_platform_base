# 工具模板

优先在现有 canonical tool 中增加动作。只有确实需要新的公共工具时，才在 `core/tools/tool_namespace_data.py` 新增 ID。

## 接入顺序

1. 更新 tool namespace、manifest registry 与 canonical registry。
2. 定义 JSON schema、调用方范围、风险等级、side effect 与输出敏感性。
3. 保持执行经 `ToolRuntimeClient.invoke()`。
4. 添加 namespace、manifest、policy、handler、脱敏和 API/前端路径测试。
5. 更新 API、架构和用户文档。

```python
CapabilityManifest(
    tool_id="text.analyze",
    action_class="read",
    risk_level="low",
    destructive=False,
    side_effects=False,
    allowed_callers=("turn_runner", "rest_api", "job_runner", "subagent"),
    output_sensitivity="internal",
)
```

handler 返回可序列化字典，由 `ToolExecutor` 包装为 `ToolResult`。返回 summary、结构化字段和可引用证据；绝不返回原始密钥或把外部写入未知伪装为失败后可重试。
