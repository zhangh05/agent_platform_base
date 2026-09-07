# 联智中枢（LZCore）

联智中枢是基于 LZCore 构建的企业智能运维平台。它把对话式任务、受治理的工具调用、工作区数据、后台作业、知识与记忆、扩展能力和审计记录放在同一条可追溯的运行时链路中。

面向用户的产品名称是“联智中枢”；`LZCore` 是框架与工程名称；`lzcore` 用于仓库、配置、部署和指标标识。

## 平台边界

- 平台内核提供任务运行时、工具治理、存储边界、组织与工作区隔离、作业、扩展和前端工作台。
- 行业能力以扩展形式接入。内核不为某个业务保留旁路工具或兼容入口。
- 所有工具调用经过统一的 schema、调用方、Skill 范围、执行与审计边界。
- 单次工具失败不等于用户任务失败；外部写入结果未知也绝不自动重放。

## 本地启动

要求：Python 3.12+、Node.js 24 LTS、npm、`curl` 与 `lsof`。首次启动会按需安装依赖。

```bash
bash start.sh
```

默认地址：

- 前端：`http://127.0.0.1:5273`
- 后端健康检查：`http://127.0.0.1:8011/api/health`

停止本地服务：

```bash
bash stop.sh
```

启动脚本默认仅监听 loopback。若显式监听局域网地址，必须同时启用 API token、密码登录或 identity；脚本会拒绝无认证的网络监听，除非显式设置仅供受信任临时开发使用的 `LZCORE_ALLOW_UNAUTHENTICATED_NETWORK=true`。

## 核心执行链路

```text
浏览器 / API / WebSocket
  -> AgentApp 与 SSOTRuntimeEngine
  -> QueryLoop（模型推理、工具计划、证据与恢复目标）
  -> ToolRuntimeClient（manifest、策略、授权、执行、脱敏、审计）
  -> AgentResult、消息、运行记录、事件与制品
```

运行时的权威状态是 `TaskState`；对外结果是 `AgentResult`。`execution_outcome` 表示用户目标的完成情况，`tool_execution_outcome` 只描述工具尝试，二者不能混用。

一次 Agent 任务没有累计墙钟时限：QueryLoop、工具累计执行、长任务跟踪和子 Agent 都由目标、结构性容量与用户取消驱动，而不是由经过多少秒决定终止。单次 provider、连接或工具调用仍可按其协议返回超时事实。子 Agent 继承父任务的完整工具面；父任务选择了 Skill 时，子 Agent 继承服务端重新解析后的 Skill、设备与连接范围。

## 当前通用工具面

通用 canonical tool 数量由 `core.tools.tool_namespace.TOOL_NAMESPACE` 动态确定；当前注册表包含 17 个工具：

`agent.manage`、`browser.manage`、`data.manage`、`exec.run`、`knowledge.manage`、`location.manage`、`memory.manage`、`report.manage`、`skill.manage`、`system.manage`、`text.analyze`、`web.manage`、`workspace.artifact`、`workspace.document.pdf.extract_text`、`workspace.file`、`workspace.filestore`、`workspace.metadata.get`。

新增业务能力应通过扩展声明工具、路由和前端页面，不能直接绕过 canonical runtime。

## 文档导航

- [设计与运行时](DESIGN.md)
- [目录与归属](STRUCTURE.md)
- [开发代理约束](AGENTS.md)
- [API 参考](docs/API.md)
- [Loop Engineering](docs/LOOP_ENGINEERING.md)
- [扩展开发](docs/EXTENSIONS.md)
- [工作流](docs/WORKFLOWS.md)
- [前端](docs/FRONTEND.md)
- [生产部署](docs/PRODUCTION.md)
- [运维处置](docs/OPERATIONS_RUNBOOK.md)

## 验证

提交前至少执行与改动面匹配的测试。运行时、策略、存储或工具契约改动执行：

```bash
python scripts/verify_docs_runtime_consistency.py
```

前端改动还应执行：

```bash
cd frontend && npm test -- --run
npm run build
```

服务器使用 `deployment/compose.server.yml` 时，从仓库根目录执行 `bash scripts/deploy_server_compose.sh`，并确认 backend 健康、frontend HTTP 200、worker 正常运行。
