# LZCore 目录与代码归属

本文是当前源码树的导航，不列出本机生成目录、依赖缓存或历史兼容目录。

```text
agent/          AgentApp、LLM 适配、会话与 SSOT Runtime 适配
artifacts/      制品生命周期和来源关系
backend/        Flask API、WebSocket、SSE、认证与服务入口
config/         示例配置及本机 provider 配置根
core/           运行时引擎、上下文、工具、诊断、保留与归档
deployment/     Dockerfile、Compose、反向代理、观测与发布槽
extensions/     扩展注册表与 bundled 业务扩展
frontend/       React/Vite 工作台
harness/        后端契约、架构和集成测试
jobs/           作业模型、队列、worker 与生命周期
observability/  trace 与运行事件
prompts/        提示词注册表、模板与渲染器
scripts/        开发、校验、部署、备份和发布脚本
storage/        工作区、会话、记录、文件、记忆和密钥存储
workflows/      工作流定义、校验与执行
```

## 所有权规则

- `backend/` 只负责传输、认证和 API 适配；任务语义在 runtime。
- `core/runtime_engine/` 管理模型循环、目标、证据、领域无关 Observation / Reference 合同和结果，不保存领域工具实现。
- `core/tools/` 是唯一公共工具执行边界。
- `extensions/<id>/` 保存该扩展的对象、工具、路由、驱动和前端贡献。
- `storage/` 负责持久化边界；业务层不得自行散落读写 workspace 文件。
- `frontend/` 展示服务端状态并发起已定义请求；不得在浏览器重建授权、恢复或审计规则。

## 不提交的运行数据

`workspaces/`、`logs/`、`config/providers/`、本地 `.env*`、虚拟环境、前端构建结果和测试产物均是环境状态，不属于源码。部署时保留这些数据根，不能用源码同步覆盖。

## 已移除的概念

当前树不包含工具别名、兼容 shim、交互式审批子系统或基于审批的后台续跑。不要重新创建对应模块、路由、UI 或环境变量。
