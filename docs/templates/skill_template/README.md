# Skill 模板

Skill 是面向模型与 UI 的说明和范围元数据，不绕过 canonical runtime、策略或服务端授权。

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
safety_rules:
  - no_unauthorized_destructive_actions
  - cite_sources_when_using_retrieval
```

`related_tools` 必须是 canonical ID。网络 Skill 的写权限由服务端在调用时以发布状态、`configuration_write`、设备/连接和工具范围重新验证。
