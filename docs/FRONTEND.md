# 前端架构与 Design System

联智中枢前端位于 `frontend/`，使用 React 18、TypeScript、Vite、Zustand 和 Phosphor Icons。前端负责呈现业务状态、收集用户输入和调用后端 API；运行时决策、权限判定、Skill 授权、任务状态与审计事实始终以后端为准。

## 信息架构

顶层导航由 `frontend/src/config/nav.ts` 定义，路由装配位于 `frontend/src/app/App.tsx`：

| 导航域 | 路由 | 主要职责 | 页面结构 |
| --- | --- | --- | --- |
| 工作台 | `/workbench` | 会话、Skill 与设备选择、工具过程、最终答复 | 左侧会话导航、中部对话、右侧窄进度轨 |
| 任务 | `/runs` | 任务记录、运行事件、证据与终态删除 | 左侧任务列表、右侧详情 |
| 资料中心 | `/data`、`/knowledge`、`/memory` | 文件、任务产出、知识源和长期记忆 | 2:1 概览或连续内容分区 |
| 能力中心 | `/capabilities`、扩展路由 | 平台能力、工具目录、设备与 Skill 管理 | 能力目录与详情；管理对象使用紧凑列表 |
| 系统管理 | `/diagnostics`、`/settings`、`/users` | 系统健康、模型服务、用户权限 | 异常优先状态列表或主从编辑器 |

扩展前端由扩展清单的 `frontend_routes` 注册。例如网络设备与 Skill 管理页由 `extensions/network_operations/extension.json` 声明，并由 `extensions/network_operations/frontend/NetworkOperations.tsx` 实现。扩展页面必须复用平台 Token 与共享组件，不能建立独立配色或重复交互契约。

## 数据与状态边界

```text
route state + Zustand store
  -> API client / WebSocket / SSE
  -> backend API
  -> AgentResult / runtime event / workspace resource
  -> UI projection
```

- 登录态使用 HttpOnly Cookie。受控 token 流只允许从 `sessionStorage` 读取，不得写入 URL、`localStorage`、构建变量或日志。
- 分离部署时，`VITE_API_BASE` 同时决定 HTTP、WebSocket 和 SSE 的 API origin。
- 前端显示服务端给出的 `execution_outcome`、`tool_execution_outcome`、恢复目标和结构化错误，不自行推断或改写任务事实。
- 单个工具失败不能渲染成整个任务失败。外部写入结果未知时，界面必须表达为待 read-back/reconcile，且不能提供重放原写操作的入口。
- 浏览器不能自行扩大 `workspace_id`、设备范围、连接范围、Skill 工具范围或 `configuration_write` 权限。
- 任务与 trace 等异步详情必须以请求序号和当前资源 ID 双重校验，旧请求不得覆盖后来选择；URL 深链恢复选择是幂等操作，不能复用“再次点击关闭”的交互语义。
- 附件上传失败时保留输入草稿、自动元数据和失败附件；部分成功只移除已上传附件，允许用户重试剩余项。

## Design System

### 视觉原则

当前界面采用克制的石墨灰与深青绿色体系。正常信息保持安静，颜色主要用于当前选择、可执行主操作和语义状态。页面优先通过间距、文字层级和背景层区分信息；只有需要建立边界或表达交互区域时才使用描边，浮层之外不依赖阴影。

禁止在业务页面中引入第二套品牌色、蓝紫渐变、装饰性毛玻璃、大面积胶囊组件或无意义卡片墙。图标统一来自 `@phosphor-icons/react`，通过 `frontend/src/components/Icon.tsx` 暴露稳定名称；不要用 emoji、文本符号、CSS 图形或手写 SVG 代替产品图标。

### Token 来源

全局 Token 的唯一来源是 `frontend/src/styles/global.css`：

- 间距：`--space-1/2/3/4/6/8/12/16`，对应 4、8、12、16、24、32、48、64px。
- 全局密度：`--ui-scale` 是页面、Portal、图标、字号与既有像素规则的唯一视觉比例边界；当前为 `0.8`。不得使用 `transform: scale()` 实现页面缩放，以免破坏滚动、固定定位与点击命中区域；所有固定视口壳须以 `calc(100vh / var(--ui-scale))` 保持物理视口高度。
- 圆角：`--r-8`、`--r-12`、`--r-16`，对应 8、12、16px；胶囊只用于状态标签。
- 字体：正文 14px；标题按 16、18、20、24px 建立层级；10–12px 只用于辅助信息、时间和机器标识。
- 布局：Header 56px、Sidebar 192px、工作台进度轨 232px、普通页面最大宽度 1440px、对话阅读宽度 980px。工作台对话与输入框须优先使用中间列可用宽度，不能因过时的阅读宽度上限制造无意义的左右空隙。
- 动效：交互过渡使用 110–240ms；340ms 仅用于较大的视图切换。`prefers-reduced-motion` 下必须关闭非必要动画。
- 语义色：`--ok`、`--warn`、`--danger`、`--info` 只表达对应业务语义，不能作为装饰色。

深色模式通过同一组语义 Token 重映射，不允许页面组件硬编码另一套主题。

### CSS 层级

入口 `frontend/src/main.tsx` 按以下顺序加载样式：

1. `styles/global.css`：Token、基础元素和历史兼容选择器。
2. `styles/product-shell.css`：Header、导航、Sidebar 等应用外壳。
3. `styles/console-system.css`：共享页面构图与管理台页面规范。
4. `pages/AgentWorkbench/AgentWorkbench.css`：工作台专用两栏构图，必须最后加载，防止兼容选择器改变工作台 Grid 生命周期。

新增页面应优先复用前三层，不得在组件中复制 Token。页面专用规则只处理该页面独有的构图，不重新定义颜色、按钮、输入框或通用状态。

## 页面构图规范

- 普通页面使用 `.page`、`.page-header` 和 `.page-body`。主体在可用空间内居中，最大宽度为 1440px，不挤压左侧导航。
- 工作台以对话为 Primary Content，执行进度为窄 Supporting Content。消息正文限制阅读宽度，输入区与消息列对齐。
- 任务、能力、设置和用户管理优先使用主从布局：左侧用于选择对象，右侧用于查看或编辑，避免跳页丢失上下文。
- Dashboard 不默认使用等宽卡片矩阵。汇总与治理信息优先使用 2fr/1fr 等非对称 Grid。
- 数据列表、设备列表和服务商列表采用连续行与分隔线；只有独立任务、表单、对话或空状态需要完整容器。
- Primary Content 使用最高文字对比度；Secondary Content 使用 `--text-2`；说明和辅助状态使用 `--text-3`；时间、标识和元数据使用 `--text-4`。

## 共享组件契约

共享组件位于 `frontend/src/components/ui/`：

- `Button`：支持 default、primary、ghost、danger 和 danger-ghost。图标按钮必须提供 `aria-label` 或 `aria-labelledby`。
- `Input`、`Select`、`Textarea`、`FormField`：输入区域必须具有可见描边、标签和统一 focus ring。
- `DataTable`：可点击行同时支持 Enter 与 Space，并暴露键盘焦点；空数据使用统一 `EmptyState`。
- `ModalShell` 与 `PortalModal`：打开后移动焦点、限制 Tab 循环、支持 Escape 关闭，并在关闭后恢复触发器焦点。
- `PageHeader`、`FilterBar`、`DetailPanel`：建立标题、筛选和详情的统一层级，页面不重复创建近似组件。

## 状态与反馈

每个异步界面都必须覆盖以下状态：

| 状态 | 表达规则 |
| --- | --- |
| Loading | 保持布局稳定，使用共享骨架或明确加载文本；禁止空白闪烁 |
| Empty | 说明为什么为空，并在存在下一步时提供一个明确操作 |
| Error | 显示可理解原因和可恢复操作；危险色只用于真实错误 |
| Hover | 轻微背景变化，不能造成位移抖动 |
| Focus | 2px 可见 focus ring，键盘路径与鼠标路径等价 |
| Active/Selected | 使用深青色文字、浅色背景或左侧 3px 指示线，不叠加多重强调 |
| Disabled | 保留可读标签，降低对比度并取消交互反馈 |
| Success/Warning/Danger | 由业务结果驱动；不能把局部工具结果提升为任务终态 |

删除设备、连接、Skill、会话和终态任务时，界面保持各自既有的硬删除语义与确认流程，不得用视觉重构改成归档或软删除。

## 响应式与可访问性

- 900px 及以下，Sidebar 变为可关闭抽屉；主从布局改为单列，工作台进度轨隐藏但完整时间线仍可访问。
- 760px 及以下，页面边距和表格密度收紧，横向表格保留滚动容器，不裁切操作列。
- 交互元素使用原生 `button`、`a`、`input`、`select` 和 `dialog` 语义；非原生可点击元素必须补齐角色、焦点和键盘事件。
- 路由切换后焦点进入 `#main`；移动导航与模态框都必须支持焦点闭环和焦点恢复。
- 所有图标按钮、状态图标和表单字段必须具有可访问名称。颜色不能成为唯一状态线索。
- 长文件名、设备名、模型名和机器标识必须在布局中截断或换行，不能覆盖大小、时间和操作控件。

## 工作台组件边界

`frontend/src/pages/AgentWorkbench/AgentWorkbench.tsx` 负责工作台数据与发送生命周期。以下组件分别负责独立视图职责：

- `WorkbenchHeader`：当前会话、模型状态、视图切换和导出。
- `WorkbenchComposer`：Skill、设备、附件和输入发送。
- `WorkbenchEmptyState`：无会话内容时的起始引导。
- `TaskProgressPanel`：实时阶段与证据摘要。
- `ResultInline`：服务端结果与执行详情投影。
- `ThinkingBlock`：受控推理内容展示。

组件不能复制服务端状态判定。异步操作只有在真实成功后才显示成功反馈，连接失败、工具失败和恢复过程必须保留其原始业务范围。

## 验证

```bash
cd frontend
npm run typecheck
npm run lint:tokens
npm test -- --run
npm run build
```

涉及布局或交互的变更还必须在真实浏览器中验证：

- 工作台、任务、资料、能力、系统状态和设置主路径；
- 桌面与窄屏布局；
- 空、加载、错误、选中、禁用和弹窗状态；
- 键盘导航、焦点闭环和长文本溢出；
- 浏览器实际请求、代理、认证和服务端响应。

截图只能证明某一时刻的视觉结果，不能替代真实交互、接口响应或服务端运行验证。
