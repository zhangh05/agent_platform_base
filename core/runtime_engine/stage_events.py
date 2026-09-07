"""
SSOT Runtime stage event names.

Used as the ``type`` field on each emit so the WebSocket real-time
callback surfaces them under the matching ``msg.name`` on the frontend.

Frontend expected matchers (frontend AgentWorkbench):

  case "planner_started":     "正在分析任务…"
  case "planner_completed":   "已规划 N 个工具调用…"
  case "graph_compiled":      "构建执行图 (N 节点)"
  case "structural_validated": "图结构校验通过"
  case "semantic_validated":  "语义校验通过"
  case "semantic_invalid":    "语义校验失败: <errors>"
  case "pre_repair_started":  "自动修复阶段"
  case "pre_repair_completed":"已修复 <N> 处"
  case "risk_assessed":       "风险评估: <level>"
  case "budget_ok":           "预算检查通过"
  case "execution_started":   "开始执行工具…"
  case "execution_completed": "工具执行完成 N/N"
  case "repair_attempt":      "重试节点 <id>"
  case "merge_completed":     "汇总执行结果"
  case "response_started":    "整理回复…"
  case "response_completed":  "回复已就绪"
  case "heartbeat":           "已等待 Xs"
"""

# Stage events — type string sent on the realtime channel
PLANNER_STARTED = "planner_started"
PLANNER_COMPLETED = "planner_completed"
MODEL_STARTED = "model_started"
MODEL_COMPLETED = "model_completed"
GRAPH_COMPILED = "graph_compiled"
STRUCTURAL_VALIDATED = "structural_validated"
SEMANTIC_VALIDATED = "semantic_validated"
SEMANTIC_INVALID = "semantic_invalid"
PRE_REPAIR_STARTED = "pre_repair_started"
PRE_REPAIR_COMPLETED = "pre_repair_completed"
RISK_ASSESSED = "risk_assessed"
BUDGET_OK = "budget_ok"
EXECUTION_STARTED = "execution_started"
EXECUTION_COMPLETED = "execution_completed"
ORCHESTRATION_PLANNED = "orchestration_planned"
ORCHESTRATION_LAYER_STARTED = "orchestration_layer_started"
ORCHESTRATION_LAYER_COMPLETED = "orchestration_layer_completed"
REPAIR_ATTEMPT = "repair_attempt"
MERGE_COMPLETED = "merge_completed"
RESPONSE_STARTED = "response_started"
RESPONSE_COMPLETED = "response_completed"
TURN_STARTED = "turn_started"
TURN_COMPLETED = "turn_completed"

# Heartbeat
HEARTBEAT = "heartbeat"


# Frontend-readable friendly labels (Chinese). Keep these stable — the
# frontend (AgentWorkbench) maps the keys above to the same labels, so
# changing them requires a coordinated frontend update.
STAGE_LABELS: dict[str, str] = {
    TURN_STARTED: "轮次开始",
    PLANNER_STARTED: "正在分析任务…",
    PLANNER_COMPLETED: "分析完成",
    MODEL_STARTED: "正在调用模型…",
    MODEL_COMPLETED: "模型调用完成",
    GRAPH_COMPILED: "构建执行图…",
    STRUCTURAL_VALIDATED: "图结构校验通过",
    SEMANTIC_VALIDATED: "语义校验通过",
    SEMANTIC_INVALID: "语义校验发现问题",
    PRE_REPAIR_STARTED: "自动修复阶段…",
    PRE_REPAIR_COMPLETED: "已自动修复",
    RISK_ASSESSED: "风险评估完成",
    BUDGET_OK: "预算检查通过",
    EXECUTION_STARTED: "开始执行工具…",
    EXECUTION_COMPLETED: "工具执行完成",
    ORCHESTRATION_PLANNED: "已生成动态执行计划",
    ORCHESTRATION_LAYER_STARTED: "正在执行协同步骤",
    ORCHESTRATION_LAYER_COMPLETED: "协同步骤执行完成",
    REPAIR_ATTEMPT: "重试节点",
    MERGE_COMPLETED: "汇总执行结果",
    RESPONSE_STARTED: "整理回复…",
    RESPONSE_COMPLETED: "回复已就绪",
    TURN_COMPLETED: "轮次完成",
    HEARTBEAT: "仍在处理…",
    "cognitive_initialized": "已建立认知状态",
    "cognitive_goal_normalized": "已规范化任务目标",
    "cognitive_plan_selected": "已选择受控执行计划",
    "cognitive_evidence_registered": "已登记有效观察",
    "cognitive_gap_detected": "发现待核对信息",
    "cognitive_decision_made": "已作出受控决策",
    "cognitive_reflection_started": "正在复核回复质量",
    "cognitive_reflection_completed": "回复质量复核完成",
    "cognitive_model_state_recorded": "模型决策状态已记录",
}


def label_for(event_type: str) -> str:
    """Return the friendly Chinese label for an event_type, or the
    event_type itself if unknown."""
    return STAGE_LABELS.get(event_type, event_type)
