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
        "system.manage" -> "system__manage"
        "web.manage" -> "web__manage"
        "artifact_list" -> "artifact_list"  (no dots, no change)
    """
    return tool_id.replace(".", "__")


def from_llm_tool_name(llm_name: str) -> str:
    """Convert LLM-safe function name back to real tool_id.

    Examples:
        "system__manage" -> "system.manage"
        "web__manage" -> "web.manage"
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
        description = prop.get("description") or _default_param_description(name)
        if description:
            param["description"] = str(description)[:240]
        if "enum" in prop:
            param["enum"] = prop["enum"]
        if "default" in prop:
            param["default"] = prop["default"]
        if "minimum" in prop:
            param["minimum"] = prop["minimum"]
        if "maximum" in prop:
            param["maximum"] = prop["maximum"]
        if "items" in prop:
            param["items"] = prop["items"]
        params_def["properties"][name] = param

    # Optional incremental-orchestration controls are available on every
    # canonical function. They describe relationships between calls; the
    # QueryLoop strips them before invoking handlers. A normal single call can
    # omit all four fields.
    params_def["properties"].update({
        "plan_step_id": {
            "type": "string",
            "description": "Optional stable step id when coordinating multiple tools in this turn.",
        },
        "plan_depends_on": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Step ids that must finish successfully before this call can run.",
        },
        "plan_bindings": {
            "type": "object",
            "description": "Optional argument-to-result references, e.g. input_data -> steps.extract.content.",
        },
        "plan_failure": {
            "type": "string",
            "enum": ["replan", "stop", "continue"],
            "default": "replan",
            "description": "What the agent intends after this step fails; runtime still enforces safety.",
        },
    })

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
        _soft_truncate(base, 420),
    ]
    usage_hint = metadata.get("usage_hint") or tool.get("usage_hint")
    not_for = metadata.get("not_for") or tool.get("not_for")
    risk = tool.get("risk_level", "")
    approval = tool.get("requires_approval", False)
    if risk and str(risk).lower() not in {"low", "safe"}:
        parts.append(f"Risk: {risk}; approval_required={bool(approval)}.")
    if usage_hint:
        parts.append(f"Use when: {_soft_truncate(str(usage_hint), 360)}")
    if not_for:
        parts.append(f"Do not use for: {_soft_truncate(str(not_for), 180)}")
    boundary = _format_action_profiles(tool.get("action_profiles") or metadata.get("action_profiles"))
    if boundary:
        parts.append(f"Action boundaries: {boundary}")
    requirements = _format_action_requirements(canonical_tool_id, metadata)
    if requirements:
        parts.append(f"Required arguments by action: {requirements}")
    return " ".join(p for p in parts if p)[:1200]


_PARAM_DESCRIPTIONS = {
    "workspace_id": "Current workspace id; omit unless explicitly overriding.",
    "action": "Operation to perform; choose exactly one enum value.",
    "query": "Search or filter text for search/list actions.",
    "limit": "Maximum number of items to return.",
    "filepath": "Workspace-relative file path.",
    "filename": "Workspace-relative output filename.",
    "artifact_id": "Artifact id returned by workspace artifact/file tools.",
    "content": "Content to save or write.",
    "title": "Human-readable title.",
    "command": "Shell or slash command for exec.run action=shell|slash.",
    "code": "Python source for exec.run action=python.",
    "description": "Short human-readable purpose of this execution.",
    "working_dir": "Workspace-relative working directory.",
    "timeout": "Maximum runtime in seconds.",
    "url": "HTTP or HTTPS URL.",
    "selector": "CSS selector for browser interaction.",
    "ref": "Element ref from a previous browser snapshot.",
    "text": "Text input or text to analyze.",
    "script": "Browser JavaScript to evaluate.",
    "key": "Keyboard key name.",
    "value": "Value to set or store.",
    "old_string": "Exact existing text to replace.",
    "new_string": "Replacement text.",
    "patch_text": "Unified diff patch text.",
    "pattern": "Glob or regex pattern, depending on action.",
    "source_id": "Knowledge source id.",
    "chunk_id": "Knowledge chunk id.",
    "memory_id": "Memory record id.",
    "field": "Profile or memory field name.",
    "tags": "List of tag strings.",
    "instruction": "Complete standalone instruction for a subagent.",
    "subtask_id": "Subagent task id returned by spawn.",
    "child_session_id": "Compatibility alias for subtask id.",
    "parent_task_id": "Parent task id for merge.",
    "run_id": "Runtime run id.",
    "session_id": "Conversation/session id.",
    "snapshot_id": "Session snapshot id.",
    "file_id": "FileStore file id.",
    "asset_id": "Saved network asset id.",
    "asset": "Network asset object to save.",
    "asset_ids": "List of saved network asset ids.",
    "host": "Target host or IP address.",
    "port": "Target TCP port.",
    "vendor": "Network vendor/platform hint such as h3c, huawei, cisco, or generic.",
    "username": "Login username.",
    "password": "Login password; secret value is redacted by runtime.",
    "auth_method": "Authentication method.",
    "private_key": "Private key content for key authentication; secret value is redacted.",
    "passphrase": "Private key passphrase; secret value is redacted.",
    "host_key_fingerprint": "Expected SSH host-key fingerprint.",
    "accept_host_key": "Set true only when the user accepts/trusts the observed host key.",
    "commands": "Read-only commands to run on a network device.",
    "baseline_id": "Network inspection baseline id.",
    "task_id": "Runtime or extension task id.",
    "confirm": "Explicit confirmation flag for actions that support it.",
}


def _default_param_description(name: str) -> str:
    return _PARAM_DESCRIPTIONS.get(str(name or ""), "")


def _format_action_requirements(tool_id: str, metadata: dict | None = None) -> str:
    """Expose conditional action requirements in the LLM-visible description."""
    try:
        from core.tools.action_requirements import ACTION_REQUIRED_ALL, ACTION_REQUIRED_ANY
    except Exception:
        return ""

    requirements = (metadata or {}).get("action_requirements") or {}
    extension_all = requirements.get("all") if isinstance(requirements, dict) else {}
    extension_any = requirements.get("any") if isinstance(requirements, dict) else {}
    extension_all = extension_all if isinstance(extension_all, dict) else {}
    extension_any = extension_any if isinstance(extension_any, dict) else {}

    chunks: list[str] = []
    actions = sorted({
        action for (tid, action) in set(ACTION_REQUIRED_ALL) | set(ACTION_REQUIRED_ANY)
        if tid == tool_id
    } | set(extension_all) | set(extension_any))
    for action in actions:
        bits: list[str] = []
        all_fields = tuple(ACTION_REQUIRED_ALL.get((tool_id, action), ())) + tuple(extension_all.get(action) or ())
        if all_fields:
            bits.append("+".join(all_fields))
        for alternatives in tuple(ACTION_REQUIRED_ANY.get((tool_id, action), ())) + tuple(extension_any.get(action) or ()):
            bits.append(" or ".join(alternatives))
        if bits:
            chunks.append(f"{action}=>{'; '.join(bits)}")
    if not chunks:
        return ""
    text = ", ".join(chunks)
    return _soft_truncate(text, 360)


def _soft_truncate(text: str, limit: int) -> str:
    """Truncate without cutting English identifiers in half when practical."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    for separator in ("。", "；", ";", ".", ",", "，", " "):
        pos = cut.rfind(separator)
        if pos >= max(40, limit // 2):
            return cut[: pos + (0 if separator == " " else 1)].rstrip()
    return cut.rstrip("_-. ")


def _format_action_profiles(action_profiles) -> str:
    """Compact action-level risk/approval hints for LLM tool selection."""
    if not isinstance(action_profiles, list):
        return ""
    chunks: list[tuple[str, str]] = []
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
        chunks.append((action, suffix))
    if not chunks:
        return ""
    if len(chunks) <= 12:
        return ", ".join(f"{action}={suffix}" for action, suffix in chunks)
    grouped: dict[str, list[str]] = {}
    for action, suffix in chunks:
        grouped.setdefault(suffix, []).append(action)
    return "; ".join(
        f"{suffix}:[{','.join(actions)}]"
        for suffix, actions in sorted(grouped.items())
    )


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
