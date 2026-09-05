# 联智中枢 UI / UX Refinement · 2026-09-05

## A. Audit 结论与实际架构

本轮从 `71ef838` 的真实产品继续渐进改造。保留既有石墨灰、深青绿与连续信息表面；未替换 Router、Store、API client 或设计框架。

- `App.tsx` 注册页面与扩展入口，`AppLayout` / `Sidebar` 提供全局导航、会话与任务入口；`config/nav.ts` 是导航结构来源。
- Zustand 会话状态承接 workspace/session 与当前任务；API client、hooks 和类型消费 HTTP、WebSocket、SSE，工作台组件展示服务端 AgentResult、Runtime Events 与工具记录。
- Run / Task、Tool Call、证据、产物是相关但不同层级；工具结果不等于任务结果。Knowledge / Memory / Artifacts 分别对应知识检索、记忆与文件产出；资源和 Skill 在服务端授权范围内选择。
- 网络设备、连接、Skill 配置由 `extensions/network_operations/frontend` 实现，其业务端点位于扩展路由。平台 `backend/api` 的会话、执行、数据、诊断、设置及用户接口保持原样。
- Shared UI 已有 Button、Input、Select、Textarea、FormField、DataTable、DetailPanel、FilterBar、ModalShell、PageHeader。本轮复用这些组件，未创建第二套组件库。

| 优先级 | 证据与问题 | 本轮处理 |
| --- | --- | --- |
| P0 | checkout 的 Sidebar / Rail 实际 Token 为 216 / 272，与要求的 176 / 192 不一致；历史结果容器仍有局部宽度上限 | 统一尺寸并解除结果区域局部上限；保留最新全宽对话设计 |
| P0 | 输入、Skill、资源控件同排竞争；收起会话栏时隐藏了时间线入口 | Prompt 独占第一行；上下文次级；保留可访问时间线切换 |
| P1 | 助手答案、工具、进度使用过多同权重边界和成功填色 | 答案优先；执行栏使用细分隔、文字与小状态标识 |
| P1 | shared CSS 自建 Token、重复 dark aliases；扩展存在独立颜色 fallback | Token 汇总 global.css；扩展改用现有语义色；保留 CSS 加载顺序 |
| P1 | .8 缩放使触摸目标与焦点变小；设备 native dialog 按视口限制后未补偿缩放 | 移动控件物理 44px；焦点物理 2px；弹窗按缩放补偿可用视口 |
| P1 | DataTable 嵌套按钮/输入操作冒泡触发行选择；代码复制未等结果便显示成功 | 隔离控件事件；等待剪贴板结果；新增回归 |
| P2 | 页面标题偏重、元数据层级弱、健康状态偏亮、知识库 Grid 拉伸导致空隙过大 | 600 字重、元数据对比与数字对齐、异常优先排序、内容起始对齐 |
| P2 | EmptyState / 用户 / 详情关闭等仍含功能字符或 Emoji | 统一现有 Icon.tsx / Phosphor 图标 |
| P3 | 历史 global.css 较大，仍有旧尺寸与兼容式规则；有限真实数据不能代表压力状态 | 不做无关 CSS 全量重写；列为后续独立审计范围 |

## B. 统一设计方向

- Typography：页面/面板标题 600；正文保持阅读行高，ID/时间退居辅助层；数字 tabular 对齐。辅助文字提升对比，避免一律低对比极小字。
- Density：4/8/12/16/24/32 spacing 继续复用；列表连续，输入完整；不用留白填满屏幕。
- Surface / Border：中性背景层与细分隔区分区域。输入、弹窗保留边界；静态内容取消阴影和装饰入场。
- Radius / Color：沿用 8/12/16 与单一青绿 Accent；语义色独立；成功不铺满绿色，unknown 不重新解释为失败或成功。
- Navigation：Workspace + Session Navigator；新会话是主动作，工作区、最近会话与任务分层。
- Page / List / Table / Form：Master-detail、连续 registry、对齐 metadata；Provider navigator + focused editor；表格在自身容器滚动。
- Workbench：Conversation 为主，Execution 为辅；Prompt → Context/Skill → Attachments → Send 的层级明确。长回答、结果与 Composer 使用中间可用宽度。
- Motion：沿用既有短时控件反馈；取消静态内容装饰入场和多处 pulse；尊重 reduced-motion。

## C. 文件与修改计划落点

下列路径均相对仓库。没有后端、API schema、Store、Hook、类型或依赖升级。

| 文件 | 原因 / 效果 | 风险与验证 |
| --- | --- | --- |
| frontend/src/styles/global.css | 唯一 Token Source、栏宽、字色、控件/触摸/焦点 | 全局影响；明暗与全部前端测试 |
| frontend/src/styles/product-shell.css | 导航、工作区、触摸目标、菜单层级 | Drawer 实际 Escape/焦点恢复 |
| frontend/src/styles/console-system.css | 管理页、表格、表单、知识库和诊断一致性 | 八页主题/宽度检查 |
| frontend/src/layouts/Sidebar.tsx | 展示既有当前工作区 | 不伪造 workspace，不改选择逻辑 |
| frontend/src/pages/AgentWorkbench/AgentWorkbench.css | 全宽对话、Composer、Rail、Markdown、移动布局 | 390—1920 宽度测量、折叠验证 |
| frontend/src/pages/AgentWorkbench/components/WorkbenchComposer.tsx | 输入与上下文层级、标签、选中状态 | 保留发送/停止/附件/Skill 处理器 |
| frontend/src/pages/AgentWorkbench/components/WorkbenchHeader.tsx | 时间线命名、pressed 状态 | 新增回归与真实切换 |
| frontend/src/pages/AgentWorkbench/components/WorkbenchEmptyState.tsx | 工作导向文案与紧凑起点 | 不修改快捷提示的业务处理 |
| frontend/src/pages/AgentWorkbench/components/MessageRow.tsx | 真实复制成功/失败反馈 | 拒绝剪贴板写入回归 |
| frontend/src/components/common.tsx | 空状态统一图标 | 现有组件测试 |
| frontend/src/components/ui/DetailPanel.tsx | 关闭图标与可访问名称 | 现有交互测试 |
| frontend/src/components/ui/DataTable.tsx | 表头语义、嵌套交互隔离 | Enter/Space 与嵌套控件回归 |
| frontend/src/pages/Diagnostics/Diagnostics.tsx | 非健康项优先、语义图标 | 仅展示排序，不改 status |
| frontend/src/pages/UserManagement/UserManagement.tsx | 统一身份图标 | 管理员实机受权限限制；已有测试 |
| extensions/network_operations/frontend/NetworkOperations.css | 单一 Token、密集设备行、缩放弹窗 | 真设备列表、编辑弹窗只打开/关闭 |
| extensions/network_operations/frontend/NetworkOperations.tsx | 提交按钮使用 shared primary variant | 不改提交逻辑、权限或删除行为 |
| frontend/src/test/uiAccessibility.test.tsx | 嵌套交互不会触发行点击 | 新增测试，不删除既有断言 |
| frontend/src/test/uiRefinement.test.tsx | 时间线与复制失败回归 | 2 项新增测试 |
| docs/FRONTEND.md、design-qa.md、本报告 | 同步当前布局与验收边界 | 文档一致性门禁 |

## D. 主要页面

| 页面 | 本轮差异 |
| --- | --- |
| App Shell | 176px Sidebar；工作区信息、会话层级、导航字重与菜单更统一 |
| Workbench | 全宽答案/结果/输入；减弱助手容器边框；Markdown 节奏；192px Rail；收起真实释放空间；Composer 上下文次级 |
| Runs | 保留 Master-detail；标题左对齐、时间数字对齐、过滤换行、选中与辅助信息一致 |
| Data | 连续行与非对称分区；标题/文件信息换行；收敛边界；移动操作可访问 |
| Knowledge | 上传区更紧凑；元数据作为字段；Grid 不再把少量内容拉伸铺满视口 |
| Memory | 复用目录/详情和共享表格、标题、空状态、响应式规则，不改记忆规则 |
| Capabilities | 目录/详情维持，移除嵌套边框；工具说明与元信息分层 |
| Network Operations | 设备身份与连接分行；共享颜色、提交样式；窄屏编辑弹窗保持操作可达 |
| Diagnostics | 非 ok 服务排在前；健康项安静；warning/error 仅语义辅助；不更改检测事实 |
| Settings | Provider + Editor；标题、字段、辅助信息、底部动作层次一致；深色主按钮前景修正 |
| Users | 身份图标与 shared 表格/动作样式统一；未改角色、权限、状态及危险操作 |

## E. Design System

`global.css` 新增/集中：`--w-reading`、`--h-control`、`--h-control-compact`、`--h-touch`、`--weight-heading`、`--weight-label`、`--focus-width`、`--console-*` 与 `--shadow-card`。Sidebar=176、Rail=192、Header=56、Page max=1440、ui-scale=.8。

1120px 阅读 Token 用于空状态；既有真实 Conversation、Result 与 Composer 的全宽设计保持。没有 transform:scale() 缩放，没有新 Accent、Icon Library 或 CSS 框架。

## F. Responsive 与宽屏测量

浏览器实际 viewport 为物理 CSS px，布局 Token 会受 .8 缩放，因此两者不可直接等同。

| viewport | Composer 约宽 | Conversation 约宽 | Rail |
| --- | ---: | ---: | --- |
| 1920 | 1599 | 1626 | 可见 |
| 1600 | 1279 | 1306 | 可见 |
| 1440 | 1119 | 1146 | 可见 |
| 1200 | 879 | 906 | 可见 |
| 1024 | 703 | 730 | 可见 |
| 900 | 881 | 900 | 隐藏，时间线保留 |
| 760 | — | — | 隐藏，无整页溢出 |
| 390 | 371 | 390 | 隐藏，Drawer 导航 |

1440 下收起 Rail 后 Composer 约 1241px，比展开时增加约 122px。八个管理路由在 1440/390 × light/dark 共 32 个检查中没有整页横向溢出。知识库最终紧凑修正额外复核。表格内部滚动保留操作列；900 以下单列/Drawer，760 以下收紧 padding。

## G. Accessibility 与交互

新增表头 `scope=col`；嵌套输入、按钮、链接不再误触发行选择；行本身 Enter/Space 保留。Composer 显式标签，资源选择 aria-pressed；会话栏收起仍能切换时间线。

物理 2px focus ring、移动44px控件、reduced-motion 统一。真实浏览器已验证任务搜索无匹配状态与清除、选中详情、工作台时间线/折叠、移动 Drawer Escape 后焦点回到“打开导航”、设备编辑弹窗打开/关闭。未在浏览器提交设备、密钥、Skill 或任务写操作。

## H. Before / After 证据

本地 `.ui-audit/` 包含修改前九页截图、修改后明暗桌面/窄屏截图、任务选中详情与工作台宽度记录。截图含真实运维记录，仅本地保存，不进入 Git 或外部发布。

可见差异：宽屏中间有效空间增加；结果没有局部上限；输入与上下文不再竞争第一行；执行栏由高权重成功块转为安静支持内容；知识库不再拉伸稀疏分区；列表与 Provider editor 控件、字重、边界一致。

## I. 工程验证

- `npm run typecheck`：通过。
- `npm run lint:tokens`：通过，163 个 Token；12 个已有 fallback 提示，不是失败。
- `npm test -- --run`：41 个文件、158 项通过。测试代理存在 localhost:3000 ECONNRESET/ECONNREFUSED 输出；无失败测试。
- `npm run build`：通过。
- `bash scripts/check_static_quality.sh`：通过，31 项测试通过。
- `.venv/bin/python scripts/verify_docs_runtime_consistency.py`：通过。
- `.venv/bin/pytest -q harness/test_goal_loop.py harness/test_docs_consistency_script.py`：20 项通过。
- 原有 experiencePolish、uiAccessibility、workbenchComponents、router、settingsLlm、operationsPage、networkOperations 均保留并通过。
- 实际浏览器使用本地完整后端与生产前端预览，通过真实路由检查；没有 mock 页面或伪造图表。

## J. 明确的剩余边界

1. 当前浏览器会话无管理员权限，`/users` 按既有权限跳转，未绕过。用户页由源码与测试检查，不能称已完成管理员实机验收。
2. 本地 Knowledge / Memory 等真实数据较少；长列表压力、大量附件、长名称极限、所有 loading/error 状态未完整实机覆盖。
3. 没有新发起真实运维执行，streaming、stop、恢复中任务、unknown write 等依赖现有测试与未变更 contract；本轮未做线上设备写入验证。
4. Diagnostics 截图为已有缓存检测结果，不代表本轮实时设备或服务检测。
5. global.css 历史规则和少量已有 fallback 仍存在；本轮渐进合并，未做与目标无关的全量 CSS 重写或 WCAG 全站认证。
6. 后端仅运行要求的定向/门禁测试，未宣称全量后端 suite 已执行。生产部署与服务证据以交付回复为准。
