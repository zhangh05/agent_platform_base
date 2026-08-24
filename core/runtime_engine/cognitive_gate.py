"""Deterministic safety and completion gate for cognitive runtime state."""
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

STOP_COMPLETED = "stop_completed"
STOP_NEEDS_USER_INPUT = "stop_needs_user_input"
STOP_WAITING_APPROVAL = "stop_waiting_approval"
STOP_UNKNOWN_OUTCOME = "stop_unknown_outcome"
STOP_FAILED = "stop_failed"
CONTINUE_REPLAN = "continue_replan"

@dataclass(frozen=True)
class CognitiveDecision:
    outcome: str
    reason_codes: tuple[str, ...]
    visible_summary: str
    terminal: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reason_codes": list(self.reason_codes),
            "visible_summary": self.visible_summary,
            "terminal": self.terminal,
        }

def decide_next_action(
    *, tool_results: Iterable[Any], execution_outcome: str,
    goal_assertions: Mapping[str, Any] | None,
    pending_approval: bool = False, terminal_error: str = "",
    blocking_unknowns: int = 0,
) -> CognitiveDecision:
    """Choose a safe next state without allowing a model to bypass policy."""
    results = list(tool_results or [])
    assertions = dict(goal_assertions or {})
    if pending_approval:
        return CognitiveDecision(STOP_WAITING_APPROVAL, ("pending_approval",), "存在待审批动作，等待批准后再继续。", True)
    if str(execution_outcome or "").lower() == "unknown" or any(
        bool(getattr(item, "execution_may_continue", False)) for item in results
    ):
        return CognitiveDecision(STOP_UNKNOWN_OUTCOME, ("unknown_tool_outcome",), "执行结果尚未确定，后续写操作已冻结，需先受控核对。", True)
    if int(blocking_unknowns or 0) > 0:
        return CognitiveDecision(STOP_NEEDS_USER_INPUT, ("blocking_evidence_gap",), "存在未解决的阻断性证据缺口，不能将当前结果标记为完成。", True)
    if assertions.get("required") and assertions.get("status") != "passed":
        if assertions.get("status") == "unknown":
            return CognitiveDecision(STOP_NEEDS_USER_INPUT, ("goal_assertion_unknown",), "关键完成条件尚无法确认，需要补充信息或受控核对。", True)
        return CognitiveDecision(STOP_FAILED, ("goal_assertion_failed",), "关键完成条件未满足，不能标记为完成。", True)
    if terminal_error == "replan_repeated_failed_call":
        return CognitiveDecision(
            CONTINUE_REPLAN,
            ("replan_repeated_failed_call",),
            "上一轮失败步骤被确定性门禁拒绝重放，需要选择不同的替代恢复步骤。",
            False,
        )
    if terminal_error:
        return CognitiveDecision(STOP_FAILED, ("terminal_runtime_error",), "运行时发生无法继续的错误，未误报为完成。", True)
    failed_observations = any(not bool(getattr(item, "ok", False)) for item in results)
    if failed_observations:
        # `derive_execution_outcome` has already evaluated terminal errors,
        # required assertions and successful evidence.  A complete task result
        # means failed attempts were recovered/non-critical telemetry; turning
        # it back into replan here creates a contradictory terminal state.
        if str(execution_outcome or "").lower() == "complete":
            return CognitiveDecision(
                STOP_COMPLETED,
                ("completion_with_nonblocking_tool_failure",),
                "目标已由可核验证据满足；非关键工具失败已保留在审计记录中。",
                True,
            )
        return CognitiveDecision(CONTINUE_REPLAN, ("tool_observation_gap",), "部分工具结果未满足当前目标，正在受控重新规划。", False)
    return CognitiveDecision(STOP_COMPLETED, ("completion_criteria_satisfied",), "目标、证据和安全条件已满足，可以生成最终结论。", True)
