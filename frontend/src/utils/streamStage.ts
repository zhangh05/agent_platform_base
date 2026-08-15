export type StreamStagePayload = {
  elapsed_ms?: unknown;
  turn_elapsed_ms?: unknown;
  stage_elapsed_ms?: unknown;
};

export type StreamProgressPatch = {
  progressText: string;
  progressElapsedMs?: number;
  stageElapsedMs?: number;
};

// Mirrors core.runtime_engine.stage_events.py. Heartbeats intentionally have no
// label: they are transport liveness signals, not observable runtime stages.
export const STREAM_STAGE_LABELS: Record<string, string> = {
  turn_started: "开始处理",
  planner_started: "正在分析任务…",
  model_started: "正在调用模型…",
  model_completed: "模型调用完成",
  planner_completed: "已规划执行图",
  graph_compiled: "构建执行图…",
  structural_validated: "图结构校验通过",
  semantic_validated: "语义校验通过",
  semantic_invalid: "语义校验发现问题",
  pre_repair_started: "自动修复阶段…",
  pre_repair_completed: "已自动修复",
  risk_assessed: "风险评估完成",
  budget_ok: "预算检查通过",
  execution_started: "开始执行工具…",
  execution_completed: "工具执行完成",
  orchestration_planned: "已生成动态执行计划",
  orchestration_layer_started: "正在执行协同步骤…",
  orchestration_layer_completed: "协同步骤执行完成",
  repair_attempt: "重试节点",
  merge_completed: "汇总执行结果",
  response_started: "整理回复…",
  response_completed: "回复已就绪",
  turn_completed: "处理完成",
};

function toElapsedMs(value: unknown): number | undefined {
  const elapsed = typeof value === "number" ? value : parseInt(String(value ?? ""), 10);
  return Number.isFinite(elapsed) && elapsed > 0 ? elapsed : undefined;
}

/** Heartbeats update the watchdog elsewhere; only real stages can alter UI progress. */
export function progressPatchForStreamStage(
  stageName: string,
  payload?: StreamStagePayload,
): StreamProgressPatch | null {
  if (stageName === "heartbeat") return null;
  const progressText = STREAM_STAGE_LABELS[stageName];
  if (!progressText) return null;

  const progressElapsedMs = toElapsedMs(payload?.turn_elapsed_ms ?? payload?.elapsed_ms);
  const stageElapsedMs = toElapsedMs(payload?.stage_elapsed_ms);
  return {
    progressText,
    progressElapsedMs,
    // Explicit undefined clears a stale duration from the prior real stage.
    stageElapsedMs,
  };
}
