# 领域模块模板

模块封装确定性的领域逻辑，不直接暴露公共工具 ID。

```text
agent/modules/<name>/
  __init__.py
  service.py       # 确定性领域逻辑
  tools.py         # 可选内部适配器
```

将模块接入已有 canonical handler；如需新的公共工具，按工具模板完成 namespace、manifest、registry、policy 与测试。需要用户可见能力时再更新 `agent/capabilities/catalog.py`。不要创建模块专属的旁路工具执行入口。
