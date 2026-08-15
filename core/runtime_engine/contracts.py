"""Tool contracts for LZCore runtime scheduling and retry policy."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass
class ToolContract:
    name: str
    display_name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    side_effect: str = "unknown"
    risk_level: str = "low"
    idempotent: bool = False
    timeout_seconds: int = 60
    max_retries: int = 0
    concurrency_group: str | None = None
    requires_approval: bool = False
    rollback_supported: bool = False
    optional: bool = False
    priority: str = "normal"
    approval_actions: frozenset[str] = field(default_factory=frozenset)
    approval_when_truthy: frozenset[str] = field(default_factory=frozenset)
    always_read_only: bool = False
    read_only_actions: frozenset[str] = field(default_factory=frozenset)


BUILTIN_CONTRACTS: dict[str, ToolContract] = {
    "exec.run": ToolContract(
        name="exec.run",
        display_name="Local Execution",
        description="Execute local shell, Python, or slash commands.",
        input_schema={"required": ["command"], "properties": {"command": {"type": "string"}}},
        output_schema={"properties": {"stdout": {"type": "string"}, "stderr": {"type": "string"}, "exit_code": {"type": "number"}, "structured_output": {}}},
        side_effect="execute_command",
        risk_level="medium",
        idempotent=False,
        timeout_seconds=120,
        concurrency_group="shell",
    ),
    "browser.manage": ToolContract(
        name="browser.manage",
        display_name="Browser Automation",
        description="Automate browser interactions.",
        input_schema={"required": ["action"], "properties": {"action": {"type": "string"}}},
        side_effect="external_request",
        risk_level="medium",
        idempotent=False,
        timeout_seconds=90,
        concurrency_group="browser",
    ),
    "web.manage": ToolContract(
        name="web.manage",
        display_name="Web",
        description="Web search, page fetch, weather, and deep search.",
        input_schema={"required": ["action"], "properties": {"action": {"type": "string"}}},
        side_effect="external_request",
        risk_level="low",
        idempotent=True,
        timeout_seconds=90,
        concurrency_group="external_http",
        max_retries=1,
    ),
    "data.manage": ToolContract(
        name="data.manage",
        display_name="Data",
        description="Process structured data.",
        input_schema={"required": ["action"], "properties": {"action": {"type": "string"}}},
        side_effect="read",
        risk_level="low",
        idempotent=True,
        timeout_seconds=30,
        max_retries=1,
    ),
    "report.manage": ToolContract(
        name="report.manage",
        display_name="Report",
        description="Save, diff, and render reports.",
        input_schema={"required": ["action"], "properties": {"action": {"type": "string"}}},
        side_effect="write_file",
        risk_level="low",
        idempotent=True,
        timeout_seconds=60,
    ),
    "knowledge.manage": ToolContract(
        name="knowledge.manage",
        display_name="Knowledge",
        description="Search, read, import, and manage knowledge.",
        input_schema={"required": ["action"], "properties": {"action": {"type": "string"}}},
        side_effect="read",
        risk_level="low",
        idempotent=True,
        timeout_seconds=300,
        max_retries=1,
    ),
    "memory.manage": ToolContract(
        name="memory.manage",
        display_name="Memory",
        description="Search and manage memory.",
        input_schema={"required": ["action"], "properties": {"action": {"type": "string"}}},
        side_effect="mutate_local",
        risk_level="low",
        idempotent=False,
        timeout_seconds=30,
        rollback_supported=True,
    ),
    "skill.manage": ToolContract(
        name="skill.manage",
        display_name="Skill",
        description="List, search, load, and inspect skills.",
        input_schema={"required": ["action"], "properties": {"action": {"type": "string"}}},
        side_effect="read",
        risk_level="low",
        idempotent=True,
        timeout_seconds=10,
        max_retries=1,
    ),
    "agent.manage": ToolContract(
        name="agent.manage",
        display_name="Agent",
        description="Manage subagents.",
        input_schema={"required": ["action"], "properties": {"action": {"type": "string"}}},
        side_effect="read",
        risk_level="low",
        idempotent=True,
        timeout_seconds=120,
        concurrency_group="subagent",
    ),
    "system.manage": ToolContract(
        name="system.manage",
        display_name="System",
        description="Runtime diagnostics and session operations.",
        input_schema={"required": ["action"], "properties": {"action": {"type": "string"}}},
        side_effect="read",
        risk_level="low",
        idempotent=True,
        timeout_seconds=300,
        max_retries=1,
    ),
    "text.analyze": ToolContract(
        name="text.analyze",
        display_name="Text",
        description="Redact, extract, and match text.",
        input_schema={"required": ["action"], "properties": {"action": {"type": "string"}}},
        side_effect="read",
        risk_level="low",
        idempotent=True,
        timeout_seconds=30,
        max_retries=1,
    ),
    "workspace.file": ToolContract(
        name="workspace.file",
        display_name="Workspace File",
        description="Read, extract managed attachments, write, edit, glob, and delete workspace files.",
        input_schema={"required": ["action"], "properties": {"action": {"type": "string"}}},
        side_effect="write_file",
        risk_level="medium",
        idempotent=False,
        timeout_seconds=30,
        concurrency_group="filesystem",
        rollback_supported=True,
    ),
    "workspace.artifact": ToolContract(
        name="workspace.artifact",
        display_name="Workspace Artifact",
        description="List, read, save, tag, and delete artifacts.",
        input_schema={"required": ["action"], "properties": {"action": {"type": "string"}}},
        side_effect="mutate_local",
        risk_level="low",
        idempotent=False,
        timeout_seconds=30,
        rollback_supported=True,
    ),
    "workspace.filestore": ToolContract(
        name="workspace.filestore",
        display_name="FileStore",
        description="Reference and import FileStore items.",
        input_schema={"required": ["action"], "properties": {"action": {"type": "string"}}},
        side_effect="read",
        risk_level="low",
        idempotent=True,
        timeout_seconds=20,
        max_retries=1,
    ),
    "workspace.metadata.get": ToolContract(
        name="workspace.metadata.get",
        display_name="Workspace Metadata",
        description="Retrieve workspace metadata.",
        side_effect="read",
        risk_level="low",
        idempotent=True,
        timeout_seconds=10,
        max_retries=1,
    ),
    "workspace.document.pdf.extract_text": ToolContract(
        name="workspace.document.pdf.extract_text",
        display_name="PDF Text Extraction",
        description="Extract text from PDF documents.",
        input_schema={"required": ["filepath"], "properties": {"filepath": {"type": "string"}}},
        side_effect="read",
        risk_level="low",
        idempotent=True,
        timeout_seconds=60,
        max_retries=1,
    ),
}


def _sync_contracts_from_canonical_registry() -> None:
    """Copy public schemas/descriptions from canonical registry when available."""
    try:
        from core.tools.canonical_registry import CANONICAL_REGISTRY
    except Exception:
        return
    for tool_id, entry in CANONICAL_REGISTRY.items():
        contract = BUILTIN_CONTRACTS.get(tool_id)
        if contract is None:
            continue
        contract.description = entry.description or contract.description
        contract.input_schema = deepcopy(entry.input_schema or {})


_sync_contracts_from_canonical_registry()


ALWAYS_READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "web.manage",
    "data.manage",
    "text.analyze",
    "workspace.metadata.get",
    "workspace.document.pdf.extract_text",
})


READ_ONLY_ACTIONS: dict[str, frozenset[str]] = {
    "agent.manage": frozenset({"list", "get", "status"}),
    "browser.manage": frozenset({"snapshot", "extract", "wait", "network", "console"}),
    "knowledge.manage": frozenset({"search", "read", "list", "chunk"}),
    "memory.manage": frozenset({"search", "review", "profile_get"}),
    "report.manage": frozenset({"diff", "document"}),
    "skill.manage": frozenset({"list", "find", "load", "inspect", "mcp_list_tools"}),
    "system.manage": frozenset({
        "diagnostics", "health", "selfcheck", "local_info", "tasks",
        "audit_log", "run_get", "session_get",
    }),
    "workspace.artifact": frozenset({"list", "read"}),
    "workspace.file": frozenset({"list", "read", "read_image", "extract_document", "extract_document_image", "extract_document_images", "glob"}),
    "workspace.filestore": frozenset({"references"}),
}


def is_read_only_call(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    tool_metadata: dict[str, Any] | None = None,
) -> bool:
    normalized = str(tool_name or "").replace("__", ".")
    contract = get_contract(normalized)
    if contract and contract.always_read_only:
        return True
    if normalized in ALWAYS_READ_ONLY_TOOLS:
        return True
    action = str((arguments or {}).get("action") or "").lower().strip()
    if contract and action in contract.read_only_actions:
        return True
    for profile in (tool_metadata or {}).get("action_profiles") or ():
        if (
            str(profile.get("action") or "").lower() == action
            and (
                profile.get("read_only") is True
                or str(profile.get("permission_action") or "").lower() == "read"
            )
        ):
            return True
    return action in READ_ONLY_ACTIONS.get(normalized, frozenset())


def get_retry_contract(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> ToolContract | None:
    contract = get_contract(tool_name)
    if contract is None:
        return None
    if is_read_only_call(tool_name, arguments):
        return replace(
            contract,
            side_effect="read",
            idempotent=True,
            max_retries=max(1, int(contract.max_retries or 0)),
        )
    return replace(
        contract,
        side_effect=(
            contract.side_effect
            if contract.side_effect not in {"read", "none", ""}
            else "mutate_local"
        ),
        idempotent=False,
        max_retries=0,
    )


def get_contract(tool_name: str) -> ToolContract | None:
    normalized = str(tool_name or "").replace("__", ".")
    contract = BUILTIN_CONTRACTS.get(normalized)
    if contract is not None:
        return contract
    # Extension ToolSpecs are the public source of truth.  Load their
    # contracts lazily so direct RiskPolicy use has the exact same boundary as
    # QueryLoop, without creating an import-time core/extension cycle.
    try:
        from extensions.runtime import get_extension_tool_specs
        get_extension_tool_specs()
    except Exception:
        return None
    return BUILTIN_CONTRACTS.get(normalized)


def get_risk_level(tool_name: str) -> str:
    contract = get_contract(tool_name)
    return contract.risk_level if contract else "medium"


def register_contract(contract: ToolContract) -> None:
    BUILTIN_CONTRACTS[contract.name] = contract


def list_contracts() -> dict[str, ToolContract]:
    return dict(BUILTIN_CONTRACTS)
