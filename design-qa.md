# Design QA — 网络设备与 Skill 连接表单

## Visual truth

- Reference screenshots:
  - `/var/folders/yg/hf791pl16b3g8n4001_tjngc0000gn/T/codex-clipboard-6c134029-a857-4e5d-bff4-bb696d101af8.png`
  - `/var/folders/yg/hf791pl16b3g8n4001_tjngc0000gn/T/codex-clipboard-816164da-84db-49cb-89f5-5ce2edcf34c9.png`
  - `/var/folders/yg/hf791pl16b3g8n4001_tjngc0000gn/T/codex-clipboard-cb124cc8-a9a5-44c9-86a1-87cc6d84bbd8.png`
- Implementation route: `http://127.0.0.1:5273/extensions/network.operations/manage`
- Implementation capture: `/tmp/lzcore-network-operations-after.png`
- State checked: 设备与连接页、CE1 Telnet 连接已验证、连接编辑态。

## Findings and fixes

1. Reference: 输入框和下拉框边界过浅，表单填写区域不易识别。
   Fix: all form inputs, selects, textareas, and inline inputs now use a visible neutral border, white background, hover emphasis, and teal focus ring.
2. Reference: yellow feedback notice remains on screen too long.
   Fix: feedback notices now expire 4.5 seconds after their latest value is shown.
3. Runtime: the CE1 target is reachable only when the local Tailscale source address is selected; the default macOS route sends the target through `en1` and times out.
   Fix: connection profiles now support an optional validated local source IP and both SSH and Telnet transports bind to it.

## Verification

- Browser DOM: source-address field is present and connection edit state restores `100.124.182.34`.
- Browser computed styles: form controls have a visible `#aebfc2` solid border and white background; no console warnings or errors were recorded.
- Browser interaction: connection test reports `连接测试完成`; the notice disappears after the configured timeout.
- Live probe: CE1 `100.117.194.25:30001` over Telnet reaches the H3C prompt through source `100.124.182.34`; status is `connected` and `verified=true`.
- Focused automated tests and TypeScript checks pass.

## Result

Passed. The requested visual affordance, transient feedback, and real local connection path are verified without changing the product layout or the host routing table.
