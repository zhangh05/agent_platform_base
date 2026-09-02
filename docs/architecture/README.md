# 架构源码索引

| 主题 | 主要实现 |
| --- | --- |
| HTTP、WebSocket、SSE | `backend/main.py`、`backend/ws/agent_ws.py`、`backend/api/` |
| 应用门面与会话 | `agent/app/`、`agent/core/thread.py` |
| 运行时与任务状态 | `agent/runtime/`、`core/runtime_engine/` |
| 工具执行与策略 | `core/tools/` |
| 上下文、知识与记忆 | `core/context/`、`agent/runtime/memory_write/`、`storage/memory_governance.py` |
| 工作区与持久化 | `storage/`、`artifacts/`、`jobs/` |
| 扩展与网络运维 | `extensions/` |
| 前端 | `frontend/src/` |

阅读顺序：先看 `backend/main.py` 的路由注册，再看 `agent/app/` 到 `core/runtime_engine/query_loop.py` 的调用链，最后查看 `core/tools/` 与具体扩展。文档解释边界，具体字段和行为以源码与测试为准。
