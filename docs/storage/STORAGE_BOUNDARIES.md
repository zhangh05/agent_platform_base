# 存储边界

## 数据根

`storage/` 提供记录、会话、工作区、文件、密钥、记忆和运行记录的持久化边界。工作区数据位于 `workspaces/`；应用级运行状态位于 `workspaces/_runtime/`；provider 配置位于 `config/providers/`。这些均为环境数据，不提交到 Git。

## 规则

- 业务代码使用对应 store，不自行拼接工作区路径或散落读写 JSON。
- 所有存取操作验证 workspace 边界，并使用原子写入和受限文件名。
- 密钥使用 `storage/secret_store.py` 的加密存储，不能写入普通记录、日志、trace 或文档。
- FileStore、制品、知识源和报告保留来源关系；删除、归档、恢复和 retention 走对应 runtime/API 生命周期。
- 备份和恢复使用 `scripts/backup_cli.py`，恢复前验证归档完整性与路径安全。

## 运行时状态

`agent/runtime/task_state.py` 与 `storage/runtime_state_store.py` 管理可信任务状态；会话消息、run、job、artifact 和 audit 各有自己的 store。不要引入第二套“镜像状态”以同步这些事实。
