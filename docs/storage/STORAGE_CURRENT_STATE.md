# 当前存储实现

当前服务器 profile 默认使用主机上的文件系统数据根：工作区、会话、运行记录、作业、制品、知识索引和密钥均由各自的 store 管理。`deployment/compose.server.yml` 将这些环境数据作为持久化挂载保留，部署源码时不得覆盖。

运行时可信状态位于 `agent/runtime/task_state.py` 与 `storage/runtime_state_store.py`；工作区对象由 `storage/workspace_store.py` 管理；消息由 `storage/message_store.py` 管理；作业由 `jobs/` 与其 store 管理。生产 profile 可按 `docs/PRODUCTION.md` 配置 PostgreSQL、S3 和 Redis 适配器。
