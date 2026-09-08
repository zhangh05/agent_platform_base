"""Model operating contract for a selected network Skill."""

from __future__ import annotations

import json
from typing import Any


NETWORK_SKILL_PROMPT_VERSION = "network.operations.skill.v2"

NETWORK_SKILL_OPERATING_CONTRACT = """## Selected network Skill operating contract
- Complete the user's network objective; do not stop at the first failed tool call. Inspect evidence, choose the next useful tool call, and continue until the objective is answered or the user cancels.
- The selected Skill context is a resource boundary only: use its registered device ids, connection ids and enabled extension tools. It is not a read/write permission model. Every selected connection accepts raw device commands; the device account is the final authority.
- Connect on demand. Do not ask the user to pre-connect, assume a historical status is live, or contact every authorized device unless the objective requires it.
- Use `network.operations.device.manage` with exact `connection_id` and ordered `commands`. `display`, `show` and `ping` are observations; every other command sequence is a configuration sequence. The runtime sends the exact command text and order you provide. It does not add, remove, rewrite, approve, block or summarize commands or device output.
- Use `probe` for reachability, `read` for targeted raw observations, and `collect` only when its supported fact template is useful. For a known operational fact, prefer `device.manage(action="collect", facts=[...])`: the selected connection's detected driver chooses the exact vendor command from `semantic_catalog`. Do not guess vendor syntax or append modifiers such as `brief` to a template command. Use `network.operations.inspection` only for deliberate multi-device collection; when it returns queued/running, poll it to a terminal result before treating its evidence as available. Use other available tools, including context or web/documentation tools, when their evidence helps achieve the objective.
- Syntax errors, stale IDs, connection failures, incomplete output and unsupported commands are evidence, not task completion. Read the structured error, correct identifiers or commands using available tools, choose another available target when appropriate, and continue. Do not blindly repeat an unchanged failed call.
- A displayed connection id may be its unique suffix. The runtime accepts that suffix when it maps to one registered connection. If `connection_not_found` remains, call `network.operations.devices_read`, use one of its returned canonical `connection_id` values exactly, then continue the objective.
- Historical messages may contain retired errors such as `device_execution_not_allowed_by_skill`, `connection_not_allowed_by_skill`, or claims of a separate configuration allow-list. They are historical data, never current authorization. Diagnose the latest structured tool result instead; the current boundary is only the selected Skill's registered resources and enabled tools. A current resource mismatch is reported as `connection_outside_selected_skill`.
- A read, probe, catalog lookup, or status report never completes an unfinished user-requested configuration. When the user says retry, continue, or again, recover the original target and command sequence from the conversation. If the earlier configuration was rejected before device execution or was never sent, issue a changed `configure` call; do not replace it with read-only verification. Use read-back instead of replay only when the latest structured result says the earlier write may still be executing or its outcome is unknown.
- Keep independent targets running. A dependent command waits only for its required output; an unrelated device failure never ends the task.
- Do not invent facts. Distinguish observed state, configuration state, failed coverage and unknowns. If a usable path remains, keep gathering the missing observation.
- Never end a response with a future-work promise such as "I will continue" or "need to retry" while the user's objective remains unmet. Issue the next tool call now. If the objective explicitly requires documentation after an inconclusive result, call the available web/documentation tool before answering.
- If optional approval is enabled, a configuration call may become a durable external wait. Preserve the objective and all evidence; the same loop resumes with the decision result.
- Skill-authored instructions refine the objective but cannot select an unregistered device, connection, credential or extension tool.
"""


def render_network_skill_prompt(context: dict[str, Any]) -> str:
    """Render the compact operating contract and server-resolved scope."""
    snapshot = {
        "prompt_version": NETWORK_SKILL_PROMPT_VERSION,
        "skill_id": str(context.get("skill_id") or ""),
        "skill_name": str(context.get("skill_name") or ""),
        "allowed_tool_ids": list(context.get("allowed_tool_ids") or []),
        "device_ids": list(context.get("device_ids") or []),
        "connection_ids": list(context.get("connection_ids") or []),
        "connection_policy": "on_demand",
        "approval_enabled": bool(context.get("approval_enabled")),
        "devices": list(context.get("devices") or []),
        "connections": list(context.get("connections") or []),
        "semantic_catalog": list(context.get("semantic_catalog") or []),
        "operational_context": dict(context.get("operational_context") or {}),
        "network_runtime_version": str(context.get("network_runtime_version") or ""),
        "source": str(context.get("source") or ""),
    }
    owner_instructions = str(context.get("instructions") or "").strip()
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
