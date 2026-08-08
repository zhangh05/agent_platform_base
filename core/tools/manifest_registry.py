"""Capability manifests for Agent Platform Base tools."""

from __future__ import annotations

from .manifest import CapabilityManifest


MANIFESTS: dict[str, CapabilityManifest] = {
    "exec.run": CapabilityManifest(
        tool_id="exec.run",
        category="exec",
        display_name="Local Exec",
        description="Local shell, Python, and slash command execution.",
        action_class="execute",
        risk_level="medium",
        side_effects="local_exec",
        idempotency="unsafe_to_retry",
        secret_fields=["code", "env_vars"],
        output_sensitivity="secret",
        timeout_seconds=120,
        allowed_callers=["turn_runner", "rest_api", "job_runner", "subagent"],
    ),
    "browser.manage": CapabilityManifest(
        tool_id="browser.manage",
        category="browser",
        display_name="Browser",
        description="Browser automation for navigation, extraction, screenshots, and interaction.",
        action_class="network",
        risk_level="medium",
        side_effects="none",
        idempotency="unsafe_to_retry",
        timeout_seconds=90,
    ),
    "web.manage": CapabilityManifest(
        tool_id="web.manage",
        category="web",
        display_name="Web",
        description="Search, fetch, weather, and deep-search public web content.",
        action_class="network",
        risk_level="low",
        side_effects="none",
        idempotency="safe_to_retry",
        timeout_seconds=90,
    ),
    "data.manage": CapabilityManifest(
        tool_id="data.manage",
        category="data",
        display_name="Data",
        description="Parse, inspect, transform, aggregate, join, and render structured data.",
        action_class="read",
        risk_level="low",
        side_effects="none",
        idempotency="safe_to_retry",
        timeout_seconds=30,
    ),
    "report.manage": CapabilityManifest(
        tool_id="report.manage",
        category="data",
        display_name="Report",
        description="Save, diff, and render report documents.",
        action_class="read",
        risk_level="low",
        side_effects="none",
        idempotency="safe_to_retry",
        writes_artifact=True,
        timeout_seconds=60,
    ),
    "knowledge.manage": CapabilityManifest(
        tool_id="knowledge.manage",
        category="knowledge",
        display_name="Knowledge",
        description="Search, read, list, chunk, import, and manage knowledge sources.",
        action_class="read",
        risk_level="medium",
        side_effects="write",
        idempotency="unknown",
        timeout_seconds=300,
    ),
    "memory.manage": CapabilityManifest(
        tool_id="memory.manage",
        category="memory",
        display_name="Memory",
        description="Search, create, update, confirm, delete, and profile memory facts.",
        action_class="write",
        risk_level="medium",
        side_effects="write",
        idempotency="unknown",
        output_sensitivity="sensitive",
        timeout_seconds=30,
    ),
    "skill.manage": CapabilityManifest(
        tool_id="skill.manage",
        category="agent",
        display_name="Skill",
        description="List, search, load, inspect skills, and call trusted MCP tools.",
        action_class="execute",
        risk_level="medium",
        side_effects="external_by_action",
        idempotency="unknown",
        allowed_callers=["turn_runner", "rest_api", "job_runner", "subagent"],
        timeout_seconds=10,
    ),
    "agent.manage": CapabilityManifest(
        tool_id="agent.manage",
        category="agent",
        display_name="Agent",
        description="Spawn/list subagents, get and merge results, cancel tasks, and inspect status.",
        action_class="execute",
        risk_level="low",
        side_effects="task_state_by_action",
        idempotency="unknown",
        allowed_callers=["turn_runner", "rest_api", "job_runner"],
        timeout_seconds=30,
    ),
    "system.manage": CapabilityManifest(
        tool_id="system.manage",
        category="system",
        display_name="System",
        description="Diagnostics, health, durable task, audit-log, run, and session operations.",
        action_class="admin",
        risk_level="medium",
        side_effects="write",
        idempotency="unsafe_to_retry",
        timeout_seconds=300,
    ),
    "text.analyze": CapabilityManifest(
        tool_id="text.analyze",
        category="text",
        display_name="Text Analyze",
        description="Redact, extract entities, and regex-match text.",
        action_class="read",
        risk_level="low",
        side_effects="none",
        idempotency="safe_to_retry",
        timeout_seconds=30,
    ),
    "workspace.file": CapabilityManifest(
        tool_id="workspace.file",
        category="workspace",
        display_name="Workspace File",
        description="List, read, edit, patch, write artifacts, glob, and soft-delete workspace files.",
        action_class="read",
        risk_level="medium",
        reads_artifact=True,
        writes_artifact=True,
        side_effects="workspace_by_action",
        idempotency="unsafe_to_retry",
        timeout_seconds=30,
    ),
    "workspace.artifact": CapabilityManifest(
        tool_id="workspace.artifact",
        category="workspace",
        display_name="Workspace Artifact",
        description="List, read, save, tag, and soft-delete workspace artifacts.",
        action_class="read",
        risk_level="low",
        reads_artifact=True,
        writes_artifact=True,
        side_effects="none",
        idempotency="unsafe_to_retry",
        timeout_seconds=30,
    ),
    "workspace.filestore": CapabilityManifest(
        tool_id="workspace.filestore",
        category="workspace",
        display_name="FileStore",
        description="Query file references or import workspace files into FileStore.",
        action_class="read",
        risk_level="low",
        side_effects="none",
        idempotency="safe_to_retry",
        timeout_seconds=20,
    ),
    "workspace.metadata.get": CapabilityManifest(
        tool_id="workspace.metadata.get",
        category="workspace",
        display_name="Workspace Metadata",
        description="Get workspace metadata and stats.",
        action_class="read",
        risk_level="low",
        side_effects="none",
        idempotency="safe_to_retry",
        timeout_seconds=10,
    ),
    "workspace.document.pdf.extract_text": CapabilityManifest(
        tool_id="workspace.document.pdf.extract_text",
        category="workspace",
        display_name="PDF Extract",
        description="Extract text from PDF files.",
        action_class="read",
        risk_level="low",
        reads_artifact=True,
        side_effects="none",
        idempotency="safe_to_retry",
        timeout_seconds=60,
    ),
}


def get_manifest(tool_id: str) -> CapabilityManifest | None:
    manifest = MANIFESTS.get(tool_id)
    if manifest is not None:
        return manifest
    try:
        from extensions.runtime import get_extension_tool_specs
        for spec, _handler in get_extension_tool_specs():
            if spec.tool_id == tool_id:
                return CapabilityManifest(
                    tool_id=spec.tool_id,
                    category=spec.category or "general",
                    display_name=spec.name or spec.tool_id,
                    description=spec.description,
                    action_class="execute" if spec.permission_action == "exec" else (
                        spec.permission_action if spec.permission_action in {"read", "write", "network"} else "read"
                    ),
                    risk_level=spec.risk_level,
                    requires_approval=spec.requires_approval,
                    side_effects="write" if spec.permission_action == "write" else "none",
                    idempotency="safe_to_retry" if spec.permission_action == "read" else "unknown",
                    timeout_seconds=spec.timeout_seconds,
                    input_schema=spec.input_schema,
                )
    except Exception:
        return None
    return None


def get_all_manifests() -> dict[str, CapabilityManifest]:
    manifests = dict(MANIFESTS)
    try:
        from extensions.runtime import get_extension_tool_specs
        for spec, _handler in get_extension_tool_specs():
            extension_manifest = get_manifest(spec.tool_id)
            if extension_manifest:
                manifests[spec.tool_id] = extension_manifest
    except Exception:
        pass
    return manifests


def validate_all() -> tuple[list[str], int]:
    """Validate all manifests. Returns (errors, count)."""
    errors = []
    for tid, manifest in MANIFESTS.items():
        for err in manifest.validate():
            errors.append(f"[{tid}] {err}")
    return errors, len(MANIFESTS)


def is_retryable(tool_id: str) -> bool:
    manifest = MANIFESTS.get(tool_id)
    return bool(manifest and not manifest.destructive and manifest.idempotency == "safe_to_retry")
