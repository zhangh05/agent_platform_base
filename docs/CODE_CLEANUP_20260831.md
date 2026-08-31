# 旧代码清理审计（2026-08-31）

## 范围与判据

审计基线：`63574b15d989c6af7a5917266bca680b0c12f24c`。
扫描全库 737 个 Git 跟踪文件，其中 509 个 Python 文件；前端同时核对扩展目录。
通过 Python AST、TypeScript 符号引用、全库文本引用、路由/工具注册入口、CSS 使用方和依赖声明交叉核对候选项。
删除依据是入口不可达、实现已被替代或调用方已消失，而不是文件名包含 legacy、行数多或年代久。
动态注册入口、对外 API、历史任务恢复及安全边界不按“仓库内直接引用少”判定为废弃。

## 完整删除的 22 个文件

- `agent/audit/{__init__,events,rollout,trace}.py`：未接入当前运行时的内存审计链，保留当前持久审计/Trace。
- `agent/llm/context_builder.py`：旧摘要拼接入口，当前使用统一上下文与提示词组装。
- `agent/modules/artifact/{__init__,service}.py`：未注册的旧制品工具实现，保留当前制品存储与工具入口。
- `agent/modules/knowledge/tools.py`：未注册的旧知识工具声明，包含与当前硬删除语义冲突的旧说明。
- `agent/task.py`：旧临时任务对象，当前使用持久任务状态。
- `agent/tools/{__init__,schemas}.py`：第二套旧 ToolSpec 定义，当前以 canonical registry 为准。
- `backend/core/paths.py`：无调用的路径封装。
- `core/tools/{action_class,registry_helpers}.py`：旧动作启发式分类与工具目录辅助入口，保留当前动作契约。
- `observability/{trace,timeline}.py`：未接入 QueryLoop 的旧流水线跟踪实现。
- `storage/{remote_store,session_meta_store}.py`：无消费者的旧连接/会话元数据存储入口。
- `scripts/audit_{context_runtime,prompt_runtime,job_runtime_security,shared}.py`：检查旧结构的审计脚本；保留 CI 使用的契约、安全及集成检查。

## 合并与裁剪

- 删除未被调用的最终答复关键词质量门禁、第二套并行 execute_layer、旧 LLM 修复计数入口。
- 删除 Python AST 校验器中被后续定义覆盖的空实现；有效校验器和隔离执行策略不变。
- 删除旧文件/记忆工具包装器、浏览器表单辅助入口、知识摘要封装、重复保留策略常量等无调用代码。
- 移除 shared 对 shared_web 的通配重导出，消除无用循环导入；调用方使用明确导入。
- 删除前端无消费者的 API 包装器、类型、Field/Collapsible 和路由预加载总入口。
- 删除旧终端、脚本管理器、报文分析、工作流编辑器、旧审计页面等已退役组件的样式；保留混合区块中的现用样式。
- 移除无调用依赖：两个 xterm 包、Scapy、readability-lxml；同步 npm 锁文件及 Windows/macOS 启动依赖探测。
- 前端不再凭答复长短或“收到/已完成”等关键词覆盖后端最终答复；仅在最终答复缺失时保留流式草稿。

## 明确保留

- 设备、连接、区域、Skill、工作台、现用管理页面，以及各对象的编辑和永久删除能力。
- 硬删除语义、凭据隔离、写操作结果未知时的核对机制、审批和重试边界。
- 历史任务消费者仍在使用的资产/调度兼容入口，以及可核验的旧 FileStore 回收记录修复工具。
- 仍作为公开能力存在的后端 API，即使其旧前端包装器已经没有调用方。
- 多模型协议支持、实际生效的提示词缓存、上下文组装、容器隔离和受控本地执行策略。

未删除用户设备记录、会话、附件、凭据或运行数据。删除的源码可从 Git 历史恢复。

## 验证

- 后端全量：1574 passed，8 skipped；跳过项需 Docker/分布式测试基础设施，继续由 CI 验证。
- 前端：147 项单测通过；生产构建通过；现有 Playwright 21 项流程通过。
- 本机浏览器只读核查：网络设备与 Skill 页面、侧栏、表单描边及六台已登记设备的管理入口正常。
- 工具/扩展、命名、文档一致性、CSS token、平台运行契约及 Ruff 检查通过。
- 当前工具契约保持 17 个核心工具与 4 个网络扩展工具，不以减少工具能力换取代码减少。
- 新增回归覆盖最终答复权威性、退役执行入口不可用和 AST 校验器只有一份有效定义。

这是全库扫描后对已确认旧代码的清理，不把静态扫描结果解释为“所有未来场景均无死代码”的证明。
