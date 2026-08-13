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

## 审批积压

1. 检查 pending 审批的 `expires_at`、requester、workspace、run 和工具绑定。
2. 确认审批人是原请求人或管理员；不得通过源 IP、代理地址或伪造 resolver 放行。
3. 到期记录应以 `system_expired / approval_ttl_expired` 进入审计，不手工删除审计行。

## 工具失败

1. 从运行 trace 检查 canonical tool ID、action、参数校验、权限和外部依赖。
2. Python 强隔离失败时确认固定镜像摘要和 Docker daemon；不得自动降级为本地执行。
3. 仅重试结构化标记为可恢复且没有不可逆副作用的调用。

## 作业失败

1. 检查 Redis lease、worker heartbeat、attempt 和幂等键。
2. worker 中断后确认 stale lease 已回收，再允许新 worker claim。
3. 外部副作用不明确时停止自动重放并转人工核对。

## 备份与恢复演练

每月至少执行一次 `backup_cli.py create`、`verify` 和隔离目录恢复；记录 RPO、RTO、文件数、摘要校验和回滚路径。恢复前必须使用 `RESTORE` 明确确认，并在恢复后重新验证 `/api/ready`。
