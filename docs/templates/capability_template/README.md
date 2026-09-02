# 能力目录模板

能力是面向用户的结果说明、推荐工具和安全提示，不是工具注册表，也不分发 handler。

1. 在 `agent/capabilities/catalog.py` 增加或更新条目。
2. `recommended_tool_ids` 只能引用 `core/tools/tool_namespace_data.py` 中的 canonical tool ID。
3. 先实现运行时路径与测试，再暴露能力目录。

```python
{
    "capability_id": "my_feature",
    "display_name": "My Feature",
    "description": "说明用户可获得的结果。",
    "module_ids": ("my_feature",),
    "recommended_tool_ids": ("workspace.file", "text.analyze"),
    "prompt_hints": ("先读取证据，再给出结论。",),
    "safety_notes": ("不要把未验证结果表述为生产事实。",),
    "status": "enabled",
}
```

禁止增加工具别名、第二套 capability registry 或把能力目录作为授权依据。
