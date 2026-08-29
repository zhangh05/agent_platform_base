"""LLM operating contract for a selected network.operations Skill.

This module is deliberately separate from the platform system prompt and from
HTTP/tool handlers.  It is injected only after the server validates a Skill
selected in the workbench.
"""

from __future__ import annotations

import json
from typing import Any


NETWORK_SKILL_PROMPT_VERSION = "network.operations.skill.v1"


NETWORK_SKILL_OPERATING_CONTRACT = """## Selected network Skill operating contract
- The workbench selection is active for this turn. Treat the server-resolved Skill, devices, connection ids and allowed tools below as the complete authorization boundary; never substitute a host, port, credential or unselected connection.
- A saved or previously verified connection is configuration, not current reachability evidence. Every device operation actively reconnects. Never depend on a browser-held session and never ask the user to connect manually.
- Call `network__operations__device__manage` (`network.operations.device.manage`) with action=\"probe\" when reachability itself must be checked. Use action=\"read\" with `connection_id` and a `commands` array when the request needs live device output. Do not send a bare host, username, password or secret.
- Put exactly one device CLI command in each `commands` item. Do not embed newlines, semicolons, shell operators or interactive answers. H3C/Huawei read commands normally start with `display`; Cisco read commands normally start with `show`. Choose commands from the user's goal and known vendor; do not invent syntax when vendor or command support is uncertain.
- Pagination control, prompt detection, Telnet negotiation, encoding and command flushing belong to the connection driver. Do not add paging-disable commands merely to make output complete; inspect the returned per-command output and error fields.
- For a small targeted read, issue independent `network__operations__device__manage` calls in parallel. For repeatable or multi-device collection, call `network__operations__inspection` (`network.operations.inspection`) with action=\"run\", the authorized connection ids and commands, then poll the same returned task_id with action=\"get\" until terminal. Never create a duplicate inspection just because it is still running.
- Each target is independent. A failed connection is structured evidence for model decision-making, not a fatal Agent error: continue available targets, use another authorized connection only when one exists, and report exact unavailable coverage. Do not label the whole task failed when the requested outcome is otherwise fully evidenced.
- Read output is evidence, not a conclusion. Reconcile requested devices, successful devices, unavailable devices and unsupported commands before answering. Distinguish configured state from observed live state and preserve exact command output qualifiers.
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
        "ready_connection_ids": list(context.get("ready_connection_ids") or []),
        "connection_activation": list(context.get("connection_activation") or []),
        "degraded": bool(context.get("degraded")),
        "devices": list(context.get("devices") or []),
        "connections": list(context.get("connections") or []),
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
