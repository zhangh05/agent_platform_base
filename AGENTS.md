# LZCore 开发协作约定

本文件面向在此仓库修改代码的开发者和自动化代理。它描述现行工程约束，不替代代码、测试或部署配置。

## 命名

- 用户界面、报告与产品文档使用“联智中枢”。
- 框架和工程使用 `LZCore`；仓库、配置、存储、部署和指标使用 `lzcore`。
- 不恢复已废弃的产品名称、工具别名或兼容路径。

## 不可突破的边界

1. 平台保持领域中立；行业对象、领域语义和业务 UI 放入扩展。
2. 任何工具必须经 `ToolRuntimeClient.invoke()`；禁止直接调用 handler 绕过 manifest、策略、授权、脱敏和审计。
3. 跨数据边界的请求必须使用已验证的 `workspace_id`；前端不得伪造默认工作区。
4. 工具失败、任务结果和外部写入未知是不同状态。只读失败可以在有界、关联证据目标下恢复；写入未知只能 read-back/reconcile，不能自动重放。
5. 网络配置权限由服务端在调用时依据已发布 Skill 的资源范围重新核定；设备账号决定命令最终权限。
6. 密钥、令牌、密码、私钥和原始敏感输出不得进入 Git、日志、trace、文档样例或浏览器持久化存储。
7. Markdown 仅描述代码事实。修改代码时同步更新相关 Markdown；不得修改运行时代码以满足过时文档。

## 主链路与归属

```text
HTTP / WebSocket -> backend/ -> agent/app/ -> agent/runtime/
                 -> core/runtime_engine/ -> core/tools/
                 -> storage/、artifacts/、jobs/、observability/
```

- `core/runtime_engine/`：QueryLoop、目标、证据、预算与最终结果投影。
- `core/tools/`：canonical tool、manifest、policy、executor、redaction。
- `extensions/`：扩展清单、业务工具、业务路由和扩展前端。
- `agent/capabilities/catalog.py`：能力目录，只供推荐与展示，不注册工具或授权。
- `storage/`：持久化与文件边界；`workspaces/`、`logs/`、`config/providers/` 是本机数据，不提交。
- `frontend/src/stores/`：浏览器侧状态；不得复制服务端权限或运行时判定。

## 修改检查表

- 工具：更新 canonical registry、manifest、contract、policy、测试与必要文档。
- API：更新路由实现、前端调用、`docs/API.md` 和 API 契约。
- 扩展：保持工具、路由、前端都在扩展命名空间内，并更新扩展文档。
- 状态机：检查创建、读取、更新、取消、终态、删除和恢复的完整生命周期。
- 文档：核验每个路径、端点、环境变量、上限和命令确实存在。

## 常用验证

```bash
python scripts/verify_docs_runtime_consistency.py
.venv/bin/pytest -q harness/test_goal_loop.py harness/test_docs_consistency_script.py
cd frontend && npm test -- --run && npm run build
```

生产交付默认包括本地验证、Git 提交、推送、Compose 部署与实际服务验证。不要把“测试通过”表述为“页面、设备或外部系统已经验证”。
