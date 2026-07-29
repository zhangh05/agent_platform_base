# agent/core/turn_context.py
"""TurnContext — snapshot of runtime state for a single turn."""

from dataclasses import dataclass, field


@dataclass
class TurnContext:
    turn_id: str = ""
    session_id: str = ""
    workspace_id: str = ""
    trace_id: str = ""
    requested_by: str = "turn_runner"
    user_input: str = ""
    model_config: dict = field(default_factory=dict)
    runtime_snapshot: dict = field(default_factory=dict)
    skill_snapshot: dict = field(default_factory=dict)
    module_snapshot: dict = field(default_factory=dict)
    tool_router: object = None
    safe_context: dict = field(default_factory=dict)
    history_window: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    visible_tool_ids: list = field(default_factory=list)
    scene_decision: object = None
    evidence_bundle: object = None
    context_frame: object = None
    runtime_state: object = None
