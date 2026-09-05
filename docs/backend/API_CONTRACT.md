# 后端 API 契约

后端是 Flask 应用，路由定义和注册在 `backend/main.py` 与 `backend/api/`。完整端点、方法和资源范围见 [../API.md](../API.md)。

通用契约：请求中的工作区资源使用认证中间件一次性解析并验证的 `workspace_id`；path、query、JSON、multipart form 中的重复值必须一致，不能由各路由自行选择来源优先级。响应中的运行时结果以 `AgentResult` 投影为准；错误应返回可操作的结构化信息，不得把内部堆栈、密钥或未脱敏工具输出暴露给客户端。HTTP、WebSocket 与 SSE 都服从同一认证和授权模型；长连接每次操作仍使用当前用户记录，而不是握手时缓存的角色和工作区列表。

新增路由时必须同步更新路由实现、前端调用、`docs/API.md`、相关 harness，并验证反向代理是否覆盖该路径。
