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

from copy import deepcopy
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
        "additionalProperties": bool(schema.get("additionalProperties", True)),
    }

    for name, prop in properties.items():
        # Preserve the complete public JSON-Schema constraint surface.  The
        # previous hand-picked projection dropped minItems/maxItems and nested
        # constraints, so the model was shown a weaker contract than runtime.
        param = deepcopy(prop)
        if "type" not in param and not any(
            key in param for key in ("enum", "oneOf", "anyOf", "allOf")
        ):
            param["type"] = "string"
        description = prop.get("description") or _default_param_description(name)
        if description:
            param["description"] = _soft_truncate(str(description), 420)
        params_def["properties"][name] = param

    # Optional incremental-orchestration controls are available on every
    # canonical function. They describe relationships between calls; the
    # QueryLoop strips them before invoking handlers. A normal single call can
    # omit all four fields.
    params_def["properties"].update({
        "plan_step_id": {
            "type": "string",
            "description": (
                "Optional stable logical step id for multi-tool coordination. "
                "A failed step may reuse its id only with corrected arguments; "
                "a successful step id is immutable."
            ),
        },
        "plan_depends_on": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Step ids that must finish successfully before this call can run.",
        },
        "plan_bindings": {
            "type": "object",
            "description": (
                "Optional destination-argument to source-result references. "
                "Use steps.<id>.output for the whole successful output, or a source tool's "
                "published referenceable field such as steps.<id>.output.rows."
            ),
        },
        "plan_failure": {
            "type": "string",
            "enum": ["replan", "stop", "continue"],
            "default": "replan",
            "description": (
                "Failure policy: replan corrects or replaces the failed step, "
                "continue runs only independent branches, and stop ends tool execution. "
                "Runtime still enforces safety."
            ),
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
    rendered_base = _soft_truncate(base, 420)
    parts = [
        f"[tool_id={canonical_tool_id}]",
        rendered_base,
    ]
    usage_hint = metadata.get("usage_hint") or tool.get("usage_hint")
    not_for = metadata.get("not_for") or tool.get("not_for")
    risk = tool.get("risk_level", "")
    approval = tool.get("requires_approval", False)
    if risk and str(risk).lower() not in {"low", "safe"}:
        parts.append(f"Risk: {risk}; approval_required={bool(approval)}.")
    boundary = _format_action_profiles(tool.get("action_profiles") or metadata.get("action_profiles"))
    if boundary:
        parts.append(f"Action boundaries: {boundary}")
    requirements = _format_action_requirements(canonical_tool_id, metadata)
    if requirements:
        parts.append(f"Required arguments by action: {requirements}")
    bindings = _format_bindable_inputs(metadata)
    if bindings:
        parts.append(f"Safe result bindings: {bindings}")
    outputs = _format_referenceable_outputs(metadata)
    if outputs:
        parts.append(f"Referenceable result fields: {outputs}")
    normalized_base = " ".join(base.split())
    normalized_rendered = " ".join(rendered_base.split())
    normalized_usage = " ".join(str(usage_hint or "").split())
    if usage_hint and normalized_usage not in normalized_rendered:
        if normalized_usage.startswith(normalized_rendered):
            usage_remainder = normalized_usage[len(normalized_rendered):].lstrip(" .;,，；。")
            if usage_remainder:
                parts.append(f"Additional guidance: {_soft_truncate(usage_remainder, 360)}")
        elif normalized_usage[:160] != normalized_base[:160]:
            parts.append(f"Use when: {_soft_truncate(str(usage_hint), 360)}")
    normalized_not_for = " ".join(str(not_for or "").split())
    if not_for and normalized_not_for[:100] not in normalized_base:
        rendered_not_for = _soft_truncate(str(not_for), 320)
        if normalized_not_for.lower().startswith(("do not ", "never ")):
            parts.append(rendered_not_for)
        else:
            parts.append(f"Do not use for: {rendered_not_for}")
    # Keep the final boundary intact. Raw slicing used to cut prohibitions and
    # identifiers mid-sentence for richer tools such as workspace.file.
    return _soft_truncate(" ".join(p for p in parts if p), 2400)


def _format_bindable_inputs(metadata: dict | None = None) -> str:
    """Expose only destination inputs explicitly authorized for result binding."""
    declared = (metadata or {}).get("bindable_inputs") or {}
    if not isinstance(declared, dict):
        return ""
    chunks = []
    for action, fields in sorted(declared.items()):
        if not isinstance(fields, (list, tuple)):
            continue
        names = [str(field) for field in fields if str(field)]
        if names:
            chunks.append(f"{action}=>{'+'.join(names)}")
    return _soft_truncate(", ".join(chunks), 360)


def _format_referenceable_outputs(metadata: dict | None = None) -> str:
    """Publish compact action-specific source paths for same-batch composition."""
    declared = (metadata or {}).get("referenceable_outputs") or {}
    if not isinstance(declared, dict):
        return ""
    chunks = []
    for action, fields in sorted(declared.items()):
        if not isinstance(fields, (list, tuple)):
            continue
        names = [str(field) for field in fields if str(field)]
        if names:
            chunks.append(f"{action}=>{'+'.join(names)}")
    return _soft_truncate(", ".join(chunks), 520)


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
    "parent_task_id": "Parent task id for merge.",
    "run_id": "Runtime run id.",
    "session_id": "Conversation/session id.",
    "snapshot_id": "Session snapshot id.",
    "file_id": "FileStore file id.",
    "asset_id": "Optional producer or domain asset id used to associate a workspace artifact.",
    "connection_ids": "List of server-verified network connection ids authorized for the selected Skill.",
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
