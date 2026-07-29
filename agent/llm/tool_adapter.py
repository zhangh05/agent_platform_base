# agent/llm/tool_adapter.py
"""Tool adapter — convert ToolSpec to OpenAI function-calling format.

Tool name mapping for LLM function calling:
- LLM function names cannot contain dots (`.`)
- Convert `.` → `__` for LLM-safe names
- Convert `__` → `.` when mapping back to real tool_id

v3.0: the LLM-facing surface is canonical-only. The function name
and the description prefix both reference the canonical tool_id;
internal dispatch fields are never exposed to the model.
"""

from typing import List


def to_llm_tool_name(tool_id: str) -> str:
    """Convert tool_id to LLM-safe function name.

    Examples:
        "system.manage" -> "runtime__health"
        "web.manage" -> "web__search"
        "artifact_list" -> "artifact_list"  (no dots, no change)
    """
    return tool_id.replace(".", "__")


def from_llm_tool_name(llm_name: str) -> str:
    """Convert LLM-safe function name back to real tool_id.

    Examples:
        "runtime__health" -> "system.manage"
        "web__search" -> "web.manage"
        "artifact_list" -> "artifact_list"  (no double underscore, no change)
    """
    return llm_name.replace("__", ".")


def tool_spec_to_openai_function(tool: dict) -> dict:
    """Convert a single ToolSpec dict to OpenAI function definition.

    v3.0 canonical-only: the description prefix carries only the
    canonical tool_id. Internal dispatch fields are stripped before
    this runs.
    """
    metadata = tool.get("metadata") or {}
    canonical_tool_id = (
        tool.get("canonical_tool_id")
        or metadata.get("canonical_tool_id")
        or tool.get("tool_id", "")
    )
    schema = tool.get("input_schema") or {}
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    params_def = {
        "type": "object",
        "properties": {},
        "required": required,
    }

    for name, prop in properties.items():
        param = {"type": prop.get("type", "string")}
        if prop.get("description"):
            param["description"] = str(prop.get("description", ""))[:200]
        if "enum" in prop:
            param["enum"] = prop["enum"]
        if "default" in prop:
            param["default"] = prop["default"]
        params_def["properties"][name] = param

    if not params_def["properties"]:
        params_def.pop("properties")
    if not params_def.get("required"):
        params_def.pop("required")

    # Use LLM-safe name (dots -> double underscore)
    llm_name = to_llm_tool_name(canonical_tool_id)
    description = _build_tool_description(tool, metadata, canonical_tool_id)

    return {
        "type": "function",
        "function": {
            "name": llm_name,
            "description": description,
            "parameters": params_def,
        },
    }


def _build_tool_description(tool: dict, metadata: dict, canonical_tool_id: str) -> str:
    """Build a compact but actionable LLM-facing tool description."""
    base = str(tool.get("description") or tool.get("name") or canonical_tool_id)
    parts = [
        f"[tool_id={canonical_tool_id}]",
        base[:280],
    ]
    usage_hint = metadata.get("usage_hint") or tool.get("usage_hint")
    not_for = metadata.get("not_for") or tool.get("not_for")
    risk = tool.get("risk_level", "")
    approval = tool.get("requires_approval", False)
    if risk and str(risk).lower() not in {"low", "safe"}:
        parts.append(f"Risk: {risk}; approval_required={bool(approval)}.")
    if usage_hint:
        parts.append(f"Use when: {str(usage_hint)[:220]}")
    if not_for:
        parts.append(f"Do not use for: {str(not_for)[:160]}")
    boundary = _format_action_profiles(tool.get("action_profiles") or metadata.get("action_profiles"))
    if boundary:
        parts.append(f"Action boundaries: {boundary}")
    return " ".join(p for p in parts if p)[:650]


def _format_action_profiles(action_profiles) -> str:
    """Compact action-level risk/approval hints for LLM tool selection."""
    if not isinstance(action_profiles, list):
        return ""
    chunks = []
    for item in action_profiles:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        if not action:
            continue
        perm = str(item.get("permission_action") or "").strip()
        risk = str(item.get("risk_level") or "").strip()
        approval = bool(item.get("requires_approval"))
        # Keep read-only actions compact; spell out approval gates because
        # those change the model's execution plan.
        if approval:
            suffix = f"{perm or 'write'}/{risk or 'high'}/approval_required"
        else:
            suffix = perm or risk or "read"
        chunks.append(f"{action}={suffix}")
    if not chunks:
        return ""
    text = ", ".join(chunks)
    return text[:260]


def build_tool_registry_for_llm(tools: List[dict]) -> List[dict]:
    """Build OpenAI-format tool definitions from ToolSpec dicts.

    Excludes forbidden tools and optionally disabled tools.
    Returns a list ready to pass as LLMRequest.tools.
    """
    result = []
    for tool in tools:
        if tool.get("risk_level") == "forbidden":
            continue
        if not tool.get("enabled", True):
            continue
        result.append(tool_spec_to_openai_function(tool))
    return result
