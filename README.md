# Agent Platform Base

Agent Platform Base v2 是可二次开发的多应用 Agent 底座。它提供运行时、工具治理、组织与工作区隔离、制品、记忆、知识库、作业、审批、诊断、扩展分发、跨扩展流程和前端工作台；不把任何特定行业能力写死在平台内核中。

## 快速启动

```bash
bash start.sh
```

默认地址：

- Frontend: `http://localhost:5273`
- Backend API: `http://127.0.0.1:8011/api/health`

源码运行需要 Python 3.12+、Node.js 20.19+ 或 22.12+。

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
| `core/runtime_engine/` | QueryLoop、工具调用循环、提示词边界、重试与最终答复 |
| `core/tools/` | 16 个通用 canonical tools、manifest、policy、executor、脱敏 |
| `storage/` | 工作区、会话、运行记录、FileStore、记忆、运行状态 |
| `artifacts/` | 制品生命周期、来源关系和当前证据投影 |
| `jobs/` | 后台作业管理 |
| `observability/` | trace/event 记录 |
| `extensions/` | 扩展清单、生命周期、权限、配额、签名与 SDK |
| `workflows/` | 核心工具与扩展工具的 DAG 编排 |
| `deployment/` | 不可变发布槽、切换与回退 |

## 16 个通用工具

`agent.manage`, `browser.manage`, `data.manage`, `exec.run`, `knowledge.manage`, `memory.manage`, `report.manage`, `skill.manage`, `system.manage`, `text.analyze`, `web.manage`, `workspace.artifact`, `workspace.document.pdf.extract_text`, `workspace.file`, `workspace.filestore`, `workspace.metadata.get`

工具名、manifest、runtime contract 和 canonical registry 必须保持一致。新业务项目要加能力时，从这四处同步扩展，不要恢复旧业务工具名。

## 二次开发方式

1. 使用 `python3 scripts/extension_cli.py create ...` 创建业务扩展。
2. 在扩展清单中声明工具、权限、路由和前端页面，不修改平台内核工具表。
3. 使用 Ed25519 签名 `.apx` 包发布到私有扩展仓库。
4. 在“应用编排”中把平台与扩展工具连接成工作区级流程。
5. 增加聚焦兼容性测试，确认工具面、API 面、权限和实际前端入口一致。

详细说明见 [扩展开发](docs/EXTENSIONS.md)、[流程编排](docs/WORKFLOWS.md)、[组织隔离](docs/TENANCY.md) 和 [生产运维](docs/PRODUCTION.md)。

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
