# 可选操作审批扩展

## 作用与边界

`extensions/approval/` 为网络 Skill 的 `configure` 调用提供可选的外部决定步骤。它默认关闭；只有已发布 Skill 的 `approval_enabled` 被显式设为 `true` 时才生效。

它不是权限系统，也不判断命令是否“危险”。网络设备账号仍是实际命令权限的最终来源；Skill 和连接选择仍由网络扩展在服务端实时校验。

## 生命周期

```text
模型提出 configure
  -> execution_interceptor 冻结精确操作
  -> pending（未连接设备）
  -> 用户 approve / reject / cancel
  -> approve 时重新核验 digest、Skill、设备与连接版本
  -> ToolRuntimeClient 执行原始参数（仅 approve）
  -> executed / unknown / invalidated / rejected / cancelled
  -> 同一决策集全部终态后，服务端自动恢复原 QueryLoop checkpoint
  -> 原 tool call id 的完整实际结果回注，模型继续原目标
```

一次 prepared operation 包含目标设备、连接公开元数据和 revision、Skill 的 `updated_at` 与连接范围、命令数组的原始顺序和 UTF-8 文本、超时和执行模型。digest 覆盖这些冻结字段；任何 digest 不匹配、Skill 修改、连接修改、设备修改或连接脱离 Skill 范围都会使批准记录失效，且不会打开设备连接。

## 持久恢复

审批不会占用 LLM 请求、HTTP 请求或设备会话等待用户。暂停时扩展保存完整的模型消息、原始工具调用、此前工具结果和本轮结果边界到 `approval_continuation` checkpoint；不做摘要、截断或浏览器端重组。每项审批可独立决定，但同一轮的全部待决定调用必须进入终态，服务端才原子认领 checkpoint 并自动恢复原逻辑循环。批准的真实结果、拒绝/取消/失效的结构化结果都按原 `call_id` 回填给模型。浏览器不会发送“继续处理”这类伪造用户消息。

checkpoint 没有 TTL；等待十分钟或更久不会使其过期。若等待期间 Skill、设备或连接范围发生变化，调用会以 `invalidated` 结果回注原循环，不会执行冻结命令。

## 批量命令

批准后的批量配置不是隐式事务。服务端只将首词为 `display`、`show` 或 `ping` 的命令视为只读；其他任意命令会使整批进入审批路径。设备明确返回某条命令失败时，驱动继续发送后续命令并在 `command_results` 中保留每一条结果。仅当传输已经断开、后续命令无法客观发送时，剩余命令才标为 `not_sent`，同时完整返回模型。写入结果不确定时不会自动重放；模型可依据完整记录决定 read-back 或后续操作。

## UI 与 API

工作台在等待时展示目标、完整命令和 digest，并提供批准、拒绝、取消。记录 API 均位于：

- `GET /api/extensions/approval/operations`
- `GET /api/extensions/approval/operations/{operation_id}`
- `POST /api/extensions/approval/operations/{operation_id}/decision`

所有 API 均要求 `workspace_id`。扩展记录不会存储设备明文凭据。
