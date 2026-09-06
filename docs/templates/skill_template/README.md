# Skill 模板

Skill 是面向模型与 UI 的说明和范围元数据，不绕过 canonical runtime 的资源范围和服务端授权。

```text
agent/skills/<name>/
  SKILL.md
  skill.yaml
```

`SKILL.md` 应说明适用任务、所需输入、证据策略和输出边界；不要在其中嵌入密钥、固定设备状态或未经授权的写操作指令。

```yaml
skill_id: my_feature
name: My Feature
version: "1.0.0"
status: enabled
description: "当前业务能力的说明层。"
related_tools:
  - workspace.file
  - text.analyze
evidence_rules:
  - cite_sources_when_using_retrieval
```

`related_tools` 必须是 canonical ID。每个已发布网络 Skill 默认可配置其已登记设备；服务端在调用时重新验证发布状态、设备/连接和工具范围，设备账号决定实际命令权限。
