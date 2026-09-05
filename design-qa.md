# LZCore Design QA

> 2026-09-05 更新：当前实现与验收范围见 [UI Refinement 报告](docs/UI_REFINEMENT_20260905.md)。下列图片属于上一轮设计记录，不代表本轮明暗主题与响应式验证。

## Scope and visual truth

- Visual direction: restrained, high-density operations SaaS with a single teal accent, quiet neutral layers, explicit typography hierarchy, and continuous information surfaces instead of a card wall.
- Source references:
  - `docs/assets/design-qa/selected-workbench-reference.png`
  - `docs/assets/design-qa/task-center-reference.png`
  - `docs/assets/design-qa/data-center-reference.png`
  - `docs/assets/design-qa/capabilities-reference.png`
  - `docs/assets/design-qa/diagnostics-reference.png`
  - `docs/assets/design-qa/settings-reference.png`
- Source dimensions: 1487 x 1058 px, light theme, desktop, illustrative populated states.
- Implementation evidence:
  - `docs/assets/design-qa/workbench-1200x900.png`
  - `docs/assets/design-qa/task-center-1200x900.png`
  - `docs/assets/design-qa/data-center-1200x900.png`
  - `docs/assets/design-qa/capabilities-1200x900.png`
  - `docs/assets/design-qa/diagnostics-1200x900.png`
  - `docs/assets/design-qa/settings-1200x900.png`
  - `docs/assets/design-qa/knowledge-1200x900.png`
  - `docs/assets/design-qa/network-operations-1200x900.png`
- Implementation viewport: 1200 x 900 px, light theme, authenticated local production build, actual local data. The task center evidence intentionally records its valid unselected initial state; selecting a row opens the detail pane.

## Full-view comparison

The final implementation preserves the selected direction across every primary surface:

- A 56 px global header and 176 px sidebar provide a stable shell without displacing page content.
- The workbench uses the available conversation width, a 192 px supporting progress rail, and a composer aligned to the conversation. Collapsing the rail expands the conversation.
- Task center uses a master-detail composition with search and status filters.
- Data center uses an asymmetric 2:1 content grid and keeps real dataset counts visible without inventing chart data.
- Capability center uses a directory-detail composition rather than equal-weight capability cards.
- Diagnostics prioritizes overall health and anomaly scanning in a compact two-column service list.
- Settings uses a provider index, focused edit surface, and persistent action row.
- Knowledge and network operations follow the same shell, spacing, control, status, and continuous-row rules.

The references contain illustrative records and richer example datasets. The implementation keeps real product state and data semantics authoritative, so literal record counts and copy are not treated as visual defects.

## Focused comparison

### Shell and navigation

- Logo, top-level navigation, utility controls, sidebar, active states, and page content now share one token system.
- The duplicate sidebar workbench shortcut was removed. `新会话` is the single primary sidebar action.
- Active navigation uses typography, a thin underline, and a quiet teal layer instead of a filled tab wall.

### Workbench

- Conversation, composer, and progress rail align to a consistent grid.
- Tool progress and completion metadata remain secondary to the assistant answer.
- Borders are reserved for input boundaries and actionable grouping; the main reading surface relies on spacing and background layers.

### Operational pages

- Tables and registries use continuous rows, restrained separators, truncation, and aligned metadata.
- Destructive actions retain explicit danger styling and existing hard-delete behavior.
- Empty, loading, success, warning, unknown, and error states retain their business meaning; visual styling does not rewrite runtime status.

### Controls and accessibility

- Buttons, inputs, selects, tabs, badges, and icon-only controls use shared sizes, radii, focus rings, and state colors.
- Clickable data rows support Enter and Space.
- Modal focus is trapped, Escape closes the dialog, focus returns to the trigger, and dialogs expose accessible labels.
- Functional glyphs were replaced with the existing Phosphor icon set. No decorative imagery is required by these data-dense application surfaces.

## Iteration history

| Severity | Finding | Resolution | Post-fix evidence |
| --- | --- | --- | --- |
| P1 | Workbench content grid collapsed after the first production build because legacy global selectors won the CSS cascade. | Route-specific workbench CSS is imported after shared styles. | `workbench-1200x900.png` |
| P2 | Long data filenames collided with size and date metadata. | Added bounded flex behavior, `min-width: 0`, and ellipsis truncation. | `data-center-1200x900.png` |
| P2 | Settings, knowledge, and network management presented repeated equal-weight cards. | Converted them to provider rows, continuous sections, and device registry rows. | `settings-1200x900.png`, `knowledge-1200x900.png`, `network-operations-1200x900.png` |
| P2 | Task center lacked fast list narrowing and did not match the target's master-detail rhythm. | Added search and status filters and standardized the 420 px master pane. | `task-center-1200x900.png` |
| P2 | Sidebar hierarchy gave secondary entries too much weight and repeated the workbench destination. | Made `新会话` the primary action and removed the duplicate destination. | All final screenshots |
| P2 | Shared interactive rows and modals had incomplete keyboard/focus behavior. | Added row keyboard activation, modal focus trapping/restoration, Escape handling, and accessible dialog names. | `frontend/src/test/uiAccessibility.test.tsx` |

## Responsive and state coverage

- Desktop evidence is captured at 1200 x 900 px.
- Shared responsive breakpoints at 920, 900, 760, and 700 px collapse asymmetric grids, master-detail layouts, and page actions without changing business behavior.
- Controls have visible hover, focus-visible, active, disabled, loading, empty, warning, error, and destructive states.
- The production build, type checker, focused interaction tests, full frontend suite, token lint, and repository quality gates are the executable acceptance evidence recorded with the release.

## Final result

本轮工程检查通过；实际浏览器证据与未覆盖状态见最新报告。用户管理的管理员会话、真实运行中的取消/恢复、设备写入以及大量数据压力场景未在本轮浏览器验收中执行，不能据此宣称所有状态均无问题。
