# 联智中枢

联智中枢 v2 是可二次开发的企业智能运维平台。它提供运行时、工具治理、组织与工作区隔离、制品、记忆、知识库、作业、诊断、扩展分发、跨扩展流程和前端工作台；不把任何特定行业能力写死在平台内核中。

命名约定：面向用户的产品名称统一使用“联智中枢”；框架与工程名称使用 `LZCore`，仓库、包、部署、存储、指标和配置标识使用 `lzcore`。

## 快速启动

```bash
bash start.sh
```

默认地址：

- Frontend: `http://localhost:5273`
- Backend API: `http://127.0.0.1:8011/api/health`

### 监听与认证模式

默认情况下，`start.sh` 只将前后端监听在 `127.0.0.1`，适用于本机可信开发。

如需供 LAN 访问，请显式设置 `BACKEND_HOST` 与 `FRONTEND_HOST`，并至少启用一种有效认证方式：

- API token：`LZCORE_AUTH_ENABLED=true` 且配置非空 `LZCORE_API_TOKEN`；
- 登录：同时配置 `LZCORE_LOGIN_USERNAME` 和 `LZCORE_LOGIN_PASSWORD`；
- Identity：`LZCORE_IDENTITY_ENABLED=true`。

启动脚本会拒绝无有效认证的网络监听。仅在受信任的临时开发环境中，才可显式设置 `LZCORE_ALLOW_UNAUTHENTICATED_NETWORK=true` 放行；脚本会输出危险警告。CORS 仅是浏览器跨域策略，不能替代服务端认证。

### 浏览器凭据与分离部署

浏览器优先使用登录会话。若在受控测试或临时排障中必须使用 API token，前端仅从 `sessionStorage` 读取 `LZCORE_API_TOKEN`；不得将长期 token 注入 `VITE_API_TOKEN`、`localStorage`、URL、日志或构建产物。

当 `VITE_API_BASE` 指向独立 API origin 时，HTTP API 与 WebSocket 均从该 origin 派生；页面 origin 只在未配置 API base 的同源部署中使用。反向代理必须同时转发 `/api`、`/ws/agent` 和 SSE 路径。



### Python 执行隔离

`exec.run(action=python)` 始终经 canonical ToolRuntime 和 policy-selected runner 执行。普通数据处理保持 medium 风险；破坏性主机动作由策略直接拒绝。默认本地子进程仅是 **best effort**，不是 sandbox，必须显式设置 `LZCORE_TRUSTED_LOCAL_PYTHON_EXECUTION=true` 才能在 loopback 单用户开发模式使用。非 loopback、identity 或登录模式下，Python 执行只允许使用 Docker 强隔离 runner；其不可用时返回结构化拒绝，不会回退为本地子进程。

Docker runner 使用单次容器，通过标准输入传递校验后的脚本，不挂载后端工作区或临时目录；因此裸机、Compose、命名卷和远程 Docker daemon 采用同一执行契约。容器保持无网络、只读文件系统、非 root、capability drop 及 CPU/内存/PID/文件大小限制，超时后强制移除具名容器。强隔离模式要求通过 `LZCORE_PYTHON_CONTAINER_IMAGE` 配置带 `@sha256:` digest 的固定镜像；默认不使用可变 tag。生产部署仍应审查 Docker daemon 权限、镜像供应链与宿主机隔离配置。
源码运行需要 Python 3.12+、Node.js 24 LTS。

停止服务：

```bash
bash stop.sh
```

## 保留的底座能力

| 模块 | 职责 |
| --- | --- |
| `backend/` | Flask API、WebSocket、SSE、认证、运行时入口 |
| `frontend/` | React/Vite 工作台、会话、运行记录、数据中心、记忆、知识库、诊断、设置 |
| `agent/app/` | AgentApp 门面、SessionManager、AgentThread |
| `agent/runtime/` | SSOT Runtime 适配、结果投影、任务跟踪、记忆写入 |
| `core/runtime_engine/` | QueryLoop、目标驱动恢复循环、工具调用、提示词边界与最终答复 |
| `core/tools/` | 17 个通用 canonical tools、manifest、policy、executor、脱敏 |
| `storage/` | 工作区、会话、运行记录、FileStore、记忆、运行状态 |
| `artifacts/` | 制品生命周期、来源关系和当前证据投影 |
| `jobs/` | 后台作业管理 |
| `observability/` | trace/event 记录 |
| `extensions/` | 扩展清单、生命周期、权限、配额、签名与 SDK |
| `workflows/` | 核心工具与扩展工具的 DAG 编排 |
| `deployment/` | 不可变发布槽、切换与回退 |

## 17 个通用工具

`agent.manage`, `browser.manage`, `data.manage`, `exec.run`, `knowledge.manage`, `location.manage`, `memory.manage`, `report.manage`, `skill.manage`, `system.manage`, `text.analyze`, `web.manage`, `workspace.artifact`, `workspace.document.pdf.extract_text`, `workspace.file`, `workspace.filestore`, `workspace.metadata.get`

工具名、manifest、runtime contract 和 canonical registry 必须保持一致。新业务项目要加能力时，从这四处同步扩展，不要恢复旧业务工具名。

## 二次开发方式

1. 使用 `python3 scripts/extension_cli.py create ...` 创建业务扩展。
2. 在扩展清单中声明工具、权限、路由和前端页面，不修改平台内核工具表。
3. 使用 Ed25519 签名 `.apx` 包发布到私有扩展仓库。
4. 在“应用编排”中把平台与扩展工具连接成工作区级流程。
5. 增加聚焦兼容性测试，确认工具面、API 面、权限和实际前端入口一致。

详细说明见 [目标驱动 Loop Engineering](docs/LOOP_ENGINEERING.md)、[扩展开发](docs/EXTENSIONS.md)、[流程编排](docs/WORKFLOWS.md)、[组织隔离](docs/TENANCY.md) 和 [生产运维](docs/PRODUCTION.md)。

## 工具失败与任务结果

一次只读工具失败不会直接结束用户任务。QueryLoop 会记录失败观察、建立有界恢复目标，并要求模型通过纠正参数、缩小范围或切换已授权的只读能力取得真实证据。跨工具恢复必须显式关联目标；三次安全替代仍无法取得证据时，系统收敛为明确的 `partial` 或 `blocked`，不会无限重试。

工具尝试状态与用户任务结果分别记录为 `tool_execution_outcome` 和 `execution_outcome`。因此，已由替代证据完成的任务可以是 `complete`，而写操作结果未知仍必须保持 `unknown` 并执行 read-back/reconcile。完整契约见 [Loop Engineering](docs/LOOP_ENGINEERING.md)。

## 产品能力接入原则

底座不保留旧业务兼容入口。后续任何行业功能都应作为新业务模块、新 canonical tool 和新前端页面重新接入，不能绕过通用运行时边界。

## 基础验证

```bash
PYTHONPATH=. python -m compileall -q backend agent core storage artifacts jobs
PYTHONPATH=. python - <<'PY'
from core.tools.tool_namespace import TOOL_NAMESPACE
from core.tools.canonical_registry import CANONICAL_REGISTRY
from core.tools.manifest_registry import MANIFESTS, validate_all
from core.runtime_engine.contracts import BUILTIN_CONTRACTS
print(len(TOOL_NAMESPACE), len(CANONICAL_REGISTRY), len(MANIFESTS), len(BUILTIN_CONTRACTS))
print(validate_all())
PY
```

日常修复和阶段发布均优先跑受影响路径测试；只有出现跨域基础设施变更且聚焦测试不足以覆盖风险时才运行全量回归。

### 本地整改质量门禁

对涉及运行时、安全策略、存储或工具契约的改动，提交前执行：

```bash
bash scripts/check_static_quality.sh
```

该门禁会阻止 `F821`（未定义名称）和 `F811`（重复定义），并运行启动安全、Python runner 选择及 session 跨进程事务的关键回归用例。浏览器链路另行通过 `cd frontend && npx playwright test` 在独立临时 storage root 中验证。
