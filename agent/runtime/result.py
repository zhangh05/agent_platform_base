# agent/runtime/result.py
"""AgentResult — final output of a turn execution.

v2.1.2: Added tool_decision field for tool-use transparency.
v3.10: Added content_parts for structured inline rendering.
"""

from dataclasses import dataclass, field


@dataclass
class AgentResult:
    ok: bool = False
    final_response: str = ""
    events: list = field(default_factory=list)
    trace_id: str = ""
    session_id: str = ""
    turn_id: str = ""
    tool_calls: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    error_type: str = ""
    # v2.1.2: Tool decision transparency
    tool_decision: dict = field(default_factory=dict)
    no_tool_reason: str = ""

    # v3.10: Structured content parts for inline rendering.
    # Each part: {"type":"text","text":"..."} or
    #            {"type":"tool_call","tool_id":"...","ok":true,"summary":"..."}
    content_parts: list = field(default_factory=list)

    def to_dict(self) -> dict:
        # Collect artifact_ids from tool_calls for job/artifact tracking
        output_artifacts = []
        for tc in (self.tool_calls or []):
            if isinstance(tc, dict):
                for art in (tc.get("artifacts") or []):
                    if isinstance(art, dict) and art.get("artifact_id"):
                        output_artifacts.append(art["artifact_id"])
        return {
            "ok": self.ok,
            "final_response": self.final_response,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "trace_id": self.trace_id,
            "events": self.events,
            "tool_calls": self.tool_calls,
            "warnings": self.warnings,
            "errors": self.errors,
            "metadata": self.metadata,
            "error_type": self.error_type,
            "tool_decision": self.tool_decision,
            "no_tool_reason": self.no_tool_reason,
            "timeline_summary": self.metadata.get("timeline_summary", {}),
            "content_parts": self.content_parts,
            "output_artifacts": output_artifacts,
        }
