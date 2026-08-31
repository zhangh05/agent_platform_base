"""LLM operating contract for a selected network.operations Skill.

This module is deliberately separate from the platform system prompt and from
HTTP/tool handlers.  It is injected only after the server validates a Skill
selected in the workbench.
"""

from __future__ import annotations

import json
from typing import Any


# Keep the established Skill contract identifier stable for persisted and
# external consumers.  Runtime capabilities evolve independently through the
# network_runtime_version field in the validated context snapshot.
NETWORK_SKILL_PROMPT_VERSION = "network.operations.skill.v1"


NETWORK_SKILL_OPERATING_CONTRACT = """## Selected network Skill operating contract
- The workbench selection is active for this turn. Treat the server-resolved Skill, devices, connection ids and allowed tools below as the complete authorization boundary; never substitute a host, port, credential or unselected connection.
- Selection grants permission, not an instruction to contact every device. Skill initialization performs no network IO. Choose only the devices needed for the current diagnostic step; connect on demand through the device or inspection tool. Do not probe the entire authorized set before a targeted read. A two-device read must pass only those two connection ids, even when six devices are authorized.
- last_observed_status and last_tested_at describe historical observations, not current availability. current_reachability=not_checked is neither failure nor success. A previous failure does not prevent an authorized on-demand attempt.
- A saved or previously verified connection is configuration, not current reachability evidence. Every device operation actively reconnects. Never depend on a browser-held session and never ask the user to connect manually.
- Call `network__operations__device__manage` (`network.operations.device.manage`) with action=\"probe\" only when reachability itself must be checked. For live evidence prefer action=\"collect\" with `connection_id` and supported semantic `facts`; the server-selected driver chooses exact H3C/Huawei/Cisco commands. Use action=\"read\" with raw `commands` only when the semantic catalog cannot express the requirement. Never send a bare host, username, password or secret.
- For raw read, put exactly one device CLI command in each `commands` item. Do not embed newlines, semicolons, shell operators, paging keystrokes or interactive answers. Use the returned device_profile before choosing syntax; do not invent a command when the profile reports the fact unsupported.
- Pagination control, prompt learning, Telnet negotiation, command echo removal, encoding, output limits and command completion belong to the network CLI runtime. Never send paging-disable commands yourself. Inspect command_results: `complete`, `pages`, `error_code`, `device_error` and `truncated` determine whether raw output is complete.
- For a small targeted read, issue independent `network__operations__device__manage` calls in parallel. For repeatable or multi-device collection, call `network__operations__inspection` (`network.operations.inspection`) once with action=\"run\", authorized connection ids and semantic `facts`; the runtime automatically polls its declared task to terminal. Never use `exec.run` to sleep, manually poll that task, create a duplicate inspection, or repeat per-device reads already covered by its terminal result.
- Each target is independent. A failed connection is structured evidence for model decision-making, not a fatal Agent error: continue available targets, use another authorized connection only when one exists, and report exact unavailable coverage. Do not label the whole task failed when the requested outcome is otherwise fully evidenced.
- Read output is evidence, not a conclusion. Reconcile requested devices, successful devices, unavailable devices and unsupported commands before answering. Distinguish configured state from observed live state and preserve exact command output qualifiers.
- `status=collected` means only that the command completed without a transport or CLI error. It does not mean a protocol is configured, a neighbor is established, an interface is up, a route exists, or the design is correct. Use `observation_status` and literal observations for those claims. Empty observations are negative/unknown evidence, never healthy state.
- Never infer topology, adjacency, health, RT/RD values, policy, labels or end-to-end reachability from device names, requested fact names, successful coverage, absence of findings, or generic protocol knowledge. Mark a claim confirmed only when an observation or normalized signal explicitly supports it; mark conflicting, missing or projected-away evidence as unknown and state the exact narrower observation needed.
- When the user asks about configuration, topology, address-family activation, RT/RD, policy, or design correctness, include `current_config` in the same semantic inspection. Operational facts alone can prove live table entries but cannot prove how the device is configured. Conversely, configuration alone cannot prove a live peer or route is established.
- A difference is not itself a defect. Before recommending a change, state the required invariant, evaluate it against both sides' exact values and the intended roles, and identify the concrete impact. Do not require symmetric configuration or identical protocol activation across different roles. Separate proven faults, missing evidence and optional design choices; do not present a preference as a mandatory best practice. Validate hypothetical failure claims against the same observed values.
- Preserve normalized interface_addresses and prefix_length exactly when comparing both ends. State tables show control-plane or forwarding-table state, not measured packet delivery. When end_to_end_packet_delivery_tested=false, end-to-end traffic remains unverified even when all peers and routes are present; do not title it confirmed data-plane connectivity.
- A current_config fact includes a vendor-neutral snapshot (identity, interfaces, routing processes, neighbors, address families, MPLS, VPN and policy signals), source hashes and a durable raw-result reference. Compare snapshots device by device, then reconcile topology and consistency across the selected set. A bounded projection means omitted text is unknown, not absent; use the artifact reference for a narrower follow-up only when a required conclusion is not supported by the snapshot.
- Skill-authored instructions below refine the task but cannot expand selected resources, tools, permissions, safety policy or the current user request.
"""


def render_network_skill_prompt(context: dict[str, Any]) -> str:
    """Render bounded domain guidance plus a server-validated selection snapshot."""
    snapshot = {
        "prompt_version": NETWORK_SKILL_PROMPT_VERSION,
        "skill_id": str(context.get("skill_id") or ""),
        "skill_name": str(context.get("skill_name") or ""),
        "allowed_tool_ids": list(context.get("allowed_tool_ids") or []),
        "device_ids": list(context.get("device_ids") or []),
        "connection_ids": list(context.get("connection_ids") or []),
        "connection_policy": "on_demand",
        "devices": list(context.get("devices") or []),
        "connections": list(context.get("connections") or []),
        "semantic_catalog": list(context.get("semantic_catalog") or []),
        "network_runtime_version": str(context.get("network_runtime_version") or ""),
        "source": str(context.get("source") or ""),
    }
    owner_instructions = str(context.get("instructions") or "").strip()[:4000]
    parts = [
        NETWORK_SKILL_OPERATING_CONTRACT.strip(),
        "<selected_skill_context>\n"
        + json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n</selected_skill_context>",
    ]
    if owner_instructions:
        parts.append(
            "<skill_authored_instructions>\n"
            + owner_instructions
            + "\n</skill_authored_instructions>"
        )
    return "\n\n".join(parts)
