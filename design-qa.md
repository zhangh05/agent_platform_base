# Design QA — 设备连接、Skill 标识与设备生命周期管理

## Visual truth

- Reference screenshots:
  - `/var/folders/yg/hf791pl16b3g8n4001_tjngc0000gn/T/codex-clipboard-107605a3-2b2f-4a9f-a601-e54e80968e74.png`
  - `/var/folders/yg/hf791pl16b3g8n4001_tjngc0000gn/T/codex-clipboard-a48f0709-e163-4ec8-9a7c-cc6a791adcea.png`
  - `/var/folders/yg/hf791pl16b3g8n4001_tjngc0000gn/T/codex-clipboard-ff0bfad5-8a3a-4c5f-b184-6fb508dff6d5.png`
- Implementation routes:
  - `http://127.0.0.1:5273/extensions/network.operations/manage`
  - `http://127.0.0.1:5273/workbench`
- Implementation capture: `/tmp/lzcore-workbench-skill-badge.png`
- Device-management capture: `/tmp/lzcore-device-management-full.png`
- State checked: CE1 连接列表、Skill 配置可用连接、带 Skill 的用户消息、刷新后的持久化消息、设备编辑和硬删除闭环。

## Findings and fixes

1. Reference: one CE1 endpoint appeared twice in both the registered-device and Skill connection lists.
   Fix: one logical endpoint is now identified by device, protocol, and port. Repeated saves update it; legacy duplicates are merged and Skill references are rewritten before the duplicate is hard-deleted.
2. Reference: the composer showed the selected Skill, but the submitted user message did not state which Skill governed that turn.
   Fix: each user bubble now carries a compact `Skill / name` label. The server writes a validated Skill snapshot with the message, so the label survives refresh and cannot depend on the current composer selection.
3. Reference: registered-device rows exposed no understandable management hierarchy; device and connection controls both used generic “编辑/删除” labels at the far edge.
   Fix: each device is now a management card with a fixed header and explicit `编辑设备` / `永久删除设备` controls. Connections have their own section and explicit `添加连接` / `编辑连接` / `测试连接` / `永久删除连接` controls.
4. Reference: destructive scope was unclear.
   Fix: device deletion confirmation states the number of connections, Skill reconciliation behavior, hard-delete semantics, and irreversibility before execution.
5. Reference: published Skill rows still exposed generic “编辑/删除” actions and hid their effective resource boundary.
   Fix: every published Skill now has explicit `编辑 Skill`, `启用/停用 Skill`, and `永久删除 Skill` controls plus readable device, connection, capability, and status details. Hard deletion explicitly preserves devices and connections.

## Verification

- Browser DOM: the CE1 card contains exactly one `TELNET:30001` connection.
- API state: local workspace contains one CE1 connection and Skill `测试1` references that surviving connection ID.
- Browser interaction: sending `验证 Skill 标签显示` shows `Skill / 测试1` on the user bubble immediately.
- Refresh recovery: after the successful turn completed and the page reloaded, the same Skill label was restored from durable session-message metadata.
- Browser interaction: CE1 `编辑设备` restores its name, address, vendor and region into the edit form; a temporary device was registered, displayed with zero-connection guidance, permanently deleted through confirmation, and absent afterward.
- Browser DOM: CE1 exposes distinct device and connection management groups with unambiguous accessible labels.
- Browser interaction: published Skill `测试1` restores its complete edit form, exposes a reversible enable/disable action, and presents an irreversible hard-delete confirmation without mutating its devices or connections.
- Focused verification: 40 network-extension tests, 11 workbench merge tests, TypeScript typecheck, CSS token validation, and the production frontend build pass.

## Result

Final result: passed. Device and published-Skill management now present complete and explicit lifecycle actions, while connection identity and per-turn Skill visibility remain unambiguous.
