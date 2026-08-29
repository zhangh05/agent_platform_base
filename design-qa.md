# Design QA — 连接唯一性与对话 Skill 标识

## Visual truth

- Reference screenshots:
  - `/var/folders/yg/hf791pl16b3g8n4001_tjngc0000gn/T/codex-clipboard-107605a3-2b2f-4a9f-a601-e54e80968e74.png`
  - `/var/folders/yg/hf791pl16b3g8n4001_tjngc0000gn/T/codex-clipboard-a48f0709-e163-4ec8-9a7c-cc6a791adcea.png`
  - `/var/folders/yg/hf791pl16b3g8n4001_tjngc0000gn/T/codex-clipboard-ff0bfad5-8a3a-4c5f-b184-6fb508dff6d5.png`
- Implementation routes:
  - `http://127.0.0.1:5273/extensions/network.operations/manage`
  - `http://127.0.0.1:5273/workbench`
- Implementation capture: `/tmp/lzcore-workbench-skill-badge.png`
- State checked: CE1 连接列表、Skill 配置可用连接、带 Skill 的用户消息、刷新后的持久化消息。

## Findings and fixes

1. Reference: one CE1 endpoint appeared twice in both the registered-device and Skill connection lists.
   Fix: one logical endpoint is now identified by device, protocol, and port. Repeated saves update it; legacy duplicates are merged and Skill references are rewritten before the duplicate is hard-deleted.
2. Reference: the composer showed the selected Skill, but the submitted user message did not state which Skill governed that turn.
   Fix: each user bubble now carries a compact `Skill / name` label. The server writes a validated Skill snapshot with the message, so the label survives refresh and cannot depend on the current composer selection.

## Verification

- Browser DOM: the CE1 card contains exactly one `TELNET:30001` connection.
- API state: local workspace contains one CE1 connection and Skill `测试1` references that surviving connection ID.
- Browser interaction: sending `验证 Skill 标签显示` shows `Skill / 测试1` on the user bubble immediately.
- Refresh recovery: after the successful turn completed and the page reloaded, the same Skill label was restored from durable session-message metadata.
- Focused verification: 40 network-extension tests, 11 workbench merge tests, TypeScript typecheck, and the production frontend build pass.

## Result

Final result: passed. Connection identity is unambiguous and the selected Skill is visible on the exact user turn it governed, including after refresh.
