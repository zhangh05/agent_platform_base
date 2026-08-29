# Design QA — 设备连接、Skill 运行时与设备生命周期管理

## Visual truth

- Reference screenshots:
  - `/var/folders/yg/hf791pl16b3g8n4001_tjngc0000gn/T/codex-clipboard-107605a3-2b2f-4a9f-a601-e54e80968e74.png`
  - `/var/folders/yg/hf791pl16b3g8n4001_tjngc0000gn/T/codex-clipboard-a48f0709-e163-4ec8-9a7c-cc6a791adcea.png`
  - `/var/folders/yg/hf791pl16b3g8n4001_tjngc0000gn/T/codex-clipboard-ff0bfad5-8a3a-4c5f-b184-6fb508dff6d5.png`
  - `/var/folders/yg/hf791pl16b3g8n4001_tjngc0000gn/T/codex-clipboard-a730e070-98a4-4965-ae5c-af708ea37e97.png`
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
6. Reference: a second independently named device on the same management address was rejected as `device host already exists`.
   Fix: device identity is now the normalized pair of device name and management address. The same address can serve multiple independently named devices; only a repeated name-and-address pair is rejected. Connections remain scoped to their device and may use different protocols or ports independently.
7. Reference: an expired connection caused `workbench_skill_has_no_verified_connection` before the model could reason, and one failed target could invalidate a multi-device Skill.
   Fix: Skill bindings now authorize configured connections independently of their last probe state. Selecting a Skill actively probes all selected connections in parallel and gives the model ordered per-connection activation evidence. Unavailable devices remain isolated; the model can continue with ready targets or explain the failure. Device tools reconnect on every call and represent remote unavailability as `connection_ok=false` decision evidence rather than a failed tool execution. Multi-device inspections reconnect and persist each target independently, producing `partial` when appropriate.

## Verification

- Browser DOM: the CE1 card contains exactly one `TELNET:30001` connection.
- API state: local workspace contains one CE1 connection and Skill `测试1` references that surviving connection ID.
- Browser interaction: sending `验证 Skill 标签显示` shows `Skill / 测试1` on the user bubble immediately.
- Refresh recovery: after the successful turn completed and the page reloaded, the same Skill label was restored from durable session-message metadata.
- Browser interaction: CE1 `编辑设备` restores its name, address, vendor and region into the edit form; a temporary device was registered, displayed with zero-connection guidance, permanently deleted through confirmation, and absent afterward.
- Browser DOM: CE1 exposes distinct device and connection management groups with unambiguous accessible labels.
- Browser interaction: published Skill `测试1` restores its complete edit form, exposes a reversible enable/disable action, and presents an irreversible hard-delete confirmation without mutating its devices or connections.
- Browser interaction: `同IP临时设备` was successfully registered on `100.117.194.25` alongside CE1 and CE2, then permanently deleted through the product confirmation flow; the two intended devices remained and browser error logs were empty.
- Runtime activation: local Skill `测试1` actively probed both configured connections. Both current connection refusals were returned as two independent activation records with `degraded=true`; selection resolution completed without raising an exception.
- Browser recovery: after both probes failed, refreshing the workbench still exposed and selected `测试1` with CE1 and CE2 resources; the former `workbench_skill_has_no_verified_connection` gate did not reappear and browser error logs were empty.
- Focused verification: 47 network-extension tests, 244 cross-layer backend tests, 14 frontend tests, TypeScript typecheck, CSS token validation, and the production frontend build pass.

## Result

Final result: passed. Device and published-Skill management present complete lifecycle actions, Skill invocation owns connection activation and per-target failure isolation, and connection identity plus per-turn Skill visibility remain unambiguous.
