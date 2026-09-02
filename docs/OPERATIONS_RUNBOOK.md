# 运维处置手册

## 服务不可达

1. 确认目标提交和工作树：`git rev-parse HEAD && git status --short`。
2. 查看 Compose 状态：`docker compose -f deployment/compose.server.yml ps`。
3. 验证 backend：`curl -fsS http://127.0.0.1:8011/api/health`。
4. 验证真实前端入口：`curl -fsSI http://127.0.0.1:5273/` 与 `curl -fsS http://127.0.0.1:5273/api/health`。
5. 需要重建时执行 `bash scripts/deploy_server_compose.sh`，不要只重启其中一个服务。

## 错误率或工具失败上升

先查看 runtime summary、trace、作业事件与工具结构化错误，区分参数/连接/策略/授权/外部服务故障。不要把单次工具失败直接归类为任务失败，也不要通过重启或重试重放未知外部写入。

## 作业与 worker

检查 `/api/jobs/<job_id>`、事件、日志和 worker status。运行中或排队作业必须先取消并等待终态；终态作业才能按 API 的确认值永久删除。队列是 at-least-once，外部写 handler 必须依赖幂等键。

## 外部写入结果未知

保持原操作冻结，检查操作账本与目标系统的只读证据。只在 read-back/reconcile 确认事实后关闭记录；不得让模型、worker 或人工“再试一次”盲目重放。

## 备份恢复

先 `python3 scripts/backup_cli.py verify <archive>`，再使用明确确认值执行 restore。恢复会保留可回退的旧数据根；恢复后重新检查 ready、worker、前端代理与关键业务读路径。
