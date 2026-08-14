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

## 审批续跑停滞

当 `LZCoreContinuationStalled` 告警触发时，先在管理诊断页或 continuation 记录中核对 `continuation_id`、workspace、审批决定、`execution_phase` 与最后一次 heartbeat。**不得**通过直接调用工具处理器或重放原工具调用恢复执行。若状态无法由受控 read-back 确认，应由操作员关闭该 stalled continuation，并以新的、可审计的 Agent turn 发起后续操作。

## 审批续跑不一致

当 `LZCoreContinuationDecisionMismatch` 告警触发时，比较 Guardian durable approval record 的 `approval_id`、`workspace_id`、metadata 中的 `continuation_id` 与 continuation 的绑定审批列表。该告警表示持久事实不一致，协调器不会自动 claim、dispatch 或重放工具；应先冻结该 continuation 并完成操作结果核对，再决定关闭或重新发起操作。

## 审批续跑协调器

协调器仅负责补写已 durable 的 Guardian decision、标记过期/停滞 continuation、更新指标和执行 retention 清理。它不调用 canonical handler、不恢复工具执行。单机文件模式使用跨进程文件锁；配置 `LZCORE_REDIS_URL` 的部署使用 Redis lease。应监控 `continuation_reconciliation_lag_seconds`、`continuation_reconciliation_failure_count`、`continuation_stalled` 与 `continuation_decision_mismatch`。
