# Agent Platform Base

Agent Platform Base 是从原业务项目剥离出来的通用本地 Agent 底座。它保留运行时、工具边界、工作区、制品、记忆、知识库、作业、审批、诊断和前端工作台；不包含任何特定行业或特定产品能力。

## 快速启动

```bash
bash start.sh
```

默认地址：

- Frontend: `http://localhost:5273`
- Backend API: `http://127.0.0.1:8010/api/health`

源码运行需要 Python 3.12+、Node.js 18+。

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

## 16 个通用工具

`agent.manage`, `browser.manage`, `data.manage`, `exec.run`, `knowledge.manage`, `memory.manage`, `report.manage`, `skill.manage`, `system.manage`, `text.analyze`, `web.manage`, `workspace.artifact`, `workspace.document.pdf.extract_text`, `workspace.file`, `workspace.filestore`, `workspace.metadata.get`

工具名、manifest、runtime contract 和 canonical registry 必须保持一致。新业务项目要加能力时，从这四处同步扩展，不要恢复旧业务工具名。

## 扩展方式

1. 在 `agent/modules/` 添加业务模块。
2. 在 `core/tools/canonical_registry.py` 注册新的 canonical tool。
3. 在 `core/tools/tool_namespace_data.py`、`core/tools/manifest_registry.py`、`core/runtime_engine/contracts.py` 同步工具元数据。
4. 在 `agent/capabilities/catalog.py` 添加业务能力描述。
5. 在 `frontend/src/config/nav.ts` 和 `frontend/src/routes.tsx` 添加业务页面。
6. 增加聚焦测试，确认工具面、API 面和前端入口一致。

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

日常修复优先跑受影响路径测试；只有发布前再做全量回归。
