# 生产值班手册

本手册对应 `deployment/observability/alerts.yml`。任何恢复操作都先保留日志、当前发布槽、`/api/ready` 响应和最近一次备份校验结果。

## 服务不可达

1. 检查 gateway、frontend、backend 容器状态和 backend `/api/ready`。
2. 如果依赖组件异常，先恢复 PostgreSQL、Redis 或对象存储，不跳过 readiness 强行发布。
3. 新发布导致异常时，使用 `scripts/release_slots.py rollback` 或回退上一镜像摘要。

## 接口错误率升高

1. 按路由模板和状态码查看指标，避免使用含用户、workspace 或 run ID 的高基数标签。
2. 对照 trace 与审计记录定位 LLM、工具、存储或代理层故障。
3. 不通过扩大重试次数掩盖非幂等写入失败。

## 工具失败

1. 从运行 trace 检查 canonical tool ID、action、参数校验、权限和外部依赖。
2. Python 强隔离失败时确认固定镜像摘要和 Docker daemon；不得自动降级为本地执行。
3. 查看 `goal_loop`、`recovery_goals` 和 `recovery_goal_events`：`pending` 表示运行时仍在等待另一条安全观察，`passed` 表示替代证据已闭环，`blocked` 表示有界策略已耗尽。
4. 仅让 QueryLoop 对结构化标记为可恢复、无不可逆副作用的读取做实质不同的替代观察；不要人工或脚本化重放同一失败调用。
5. 若结果为 `partial`，交接已验证覆盖和每个 blocked goal 的缺失证据；若为 `unknown`，按操作账本执行 read-back/reconcile，不把它改判为失败或成功。

## 作业失败

1. 检查 Redis lease、worker heartbeat、attempt 和幂等键。
2. worker 中断后确认 stale lease 已回收，再允许新 worker claim。
3. 外部副作用不明确时停止自动重放并转人工核对。

## 备份与恢复演练

每月至少执行一次 `backup_cli.py create`、`verify` 和隔离目录恢复；记录 RPO、RTO、文件数、摘要校验和回滚路径。恢复前必须使用 `RESTORE` 明确确认，并在恢复后重新验证 `/api/ready`。

## 外部写入结果未知

网络配置或其他非幂等外部写入返回 unknown 时，不自动重试。使用操作账本中的 connection、命令摘要和调用标识进行只读回查；确认成功或失败后再人工核定结果。无法核对时保持 unknown，并发起新的可审计任务，不重放原写入。
