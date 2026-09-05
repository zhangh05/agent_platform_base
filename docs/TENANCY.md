# 组织与工作区隔离

组织、用户、成员关系与工作区授权由 identity 和 workspace API 管理。每个资源访问都以服务端验证的 `workspace_id` 为边界；UI 路由、会话参数或客户端缓存不能跨越这一边界。

HTTP 中间件统一读取 path、query、JSON 与 multipart form 的工作区字段。多个来源同时存在时必须一致，否则请求以 `workspace_id_conflict` 终止。WebSocket 在解析工作台 Skill 和读取数据前绑定已认证 storage principal，并在每次 workspace 操作时重新读取用户的 enabled、role 与 workspace 列表；禁用或删除账号立即失去已有长连接的访问权。平台 API token 使用独立的 `api-token` principal，不能落入匿名存储。

在 identity 模式下，用户、组织、成员关系和角色通过 `/api/identity/*` 管理；工作区通过 `/api/workspaces/*` 管理。角色控制读取、执行、编辑和管理能力，具体工具仍执行自身的 caller、policy 与产品授权检查。

不要把“工作区可见”理解为“设备、外部系统或写配置可操作”。网络设备写入还必须满足发布 Skill 的实时服务端授权范围。
