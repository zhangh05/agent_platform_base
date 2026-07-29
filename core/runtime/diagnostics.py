# runtime/diagnostics.py
"""Runtime diagnostics — safe, read-only diagnostic information.

Collects component status, counts, and metadata without exposing secrets.
"""

from dataclasses import dataclass, field

from storage.status_store import workspace_counts, workspace_exists


@dataclass
class ComponentStatus:
    name: str = ""
    status: str = "ok"  # ok | warning | error | unavailable
    message: str = ""
    details: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class DiagnosticReport:
    components: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "components": [c.as_dict() for c in self.components],
            "summary": self.summary,
        }


def get_diagnostics(workspace_id: str = "default") -> DiagnosticReport:
    """Collect diagnostic information for a workspace."""
    report = DiagnosticReport()
    components = []
    exists = workspace_exists(workspace_id)

    # 1. Workspace
    if exists:
        components.append(ComponentStatus("workspace", "ok",
            f"Workspace '{workspace_id}' exists"))
    else:
        components.append(ComponentStatus("workspace", "error",
            f"Workspace '{workspace_id}' not found"))

    # 2. Canonical capability and tool sources
    try:
        from agent.capabilities.catalog import list_all, list_enabled
        from core.tools.canonical_registry import CANONICAL_REGISTRY
        components.append(ComponentStatus("capabilities", "ok",
            f"{len(list_enabled())} capabilities, {len(CANONICAL_REGISTRY)} tools",
            {"capability_count": len(list_all()), "tool_count": len(CANONICAL_REGISTRY)}))
    except Exception as e:
        components.append(ComponentStatus("capabilities", "error", f"Catalog failed: {str(e)[:100]}"))

    # 3. Run stats
    try:
        counts = workspace_counts(workspace_id) if exists else {"runs": 0, "artifacts": 0, "jobs": 0}
        components.append(ComponentStatus("runs", "ok", f"{counts['runs']} run records",
            {"count": counts["runs"]}))
        components.append(ComponentStatus("artifacts", "ok", f"{counts['artifacts']} artifacts",
            {"count": counts["artifacts"]}))
        components.append(ComponentStatus("jobs", "ok", f"{counts['jobs']} jobs",
            {"count": counts["jobs"]}))
    except Exception:
        components.append(ComponentStatus("storage_counts", "warning", "Cannot read storage counts"))

    # 6. Agent runtime
    try:
        from agent.runtime_status import get_runtime_status
        agent_status = get_runtime_status()
        components.append(ComponentStatus("agent", "ok",
            f"Runtime: {agent_status.get('runtime_engine', '?')}",
            {"runtime_mode": agent_status.get("runtime_engine", "")}))
    except Exception as e:
        components.append(ComponentStatus("agent", "error", str(e)[:100]))

    # 7. Tool Runtime
    try:
        from core.tools.integration import get_default_tool_runtime_client
        client = get_default_tool_runtime_client()
        tools = client.list_tools()
        components.append(ComponentStatus("tool_runtime", "ok",
            f"{len(tools)} tools registered",
            {"tool_count": len(tools), "tools": [t["tool_id"] for t in tools]}))
    except Exception as e:
        components.append(ComponentStatus("tool_runtime", "warning", str(e)[:100]))

    # 8. LLM
    try:
        from agent.llm.runtime import get_llm_status
        llm = get_llm_status()
        components.append(ComponentStatus("llm", "ok",
            f"Enabled: {llm.get('enabled')}, Provider: {llm.get('provider', 'disabled')}",
            {"enabled": llm.get("enabled"), "provider": llm.get("provider", "disabled"),
             "safe_mode": llm.get("safe_mode", True)}))
    except Exception as e:
        components.append(ComponentStatus("llm", "warning", str(e)[:100]))

    # 9. Archive
    try:
        from core.runtime.archive import get_archive_audits
        audits = get_archive_audits(workspace_id)
        components.append(ComponentStatus("archive", "ok",
            f"{len(audits)} archive audits",
            {"audit_count": len(audits)}))
    except Exception as e:
        components.append(ComponentStatus("archive", "warning", str(e)[:100]))

    # 10. Memory
    try:
        from backend.api.memory import handle_memory_status
        components.append(ComponentStatus("memory", "ok", "Memory system available"))
    except Exception:
        components.append(ComponentStatus("memory", "warning", "Memory status unavailable"))

    report.components = components
    report.summary = {
        "total": len(components),
        "ok": sum(1 for c in components if c.status == "ok"),
        "warning": sum(1 for c in components if c.status == "warning"),
        "error": sum(1 for c in components if c.status == "error"),
    }
    return report
