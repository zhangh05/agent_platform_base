import type { ActiveTurnSnapshot, RuntimeEvent } from "../types";
import type { ChatMsg } from "../stores/workbench";

export type TaskPhaseState = "idle" | "active" | "done" | "failed";

export type TaskPhase = {
  id: "understand" | "evidence" | "analysis" | "answer";
  title: string;
  description: string;
  state: TaskPhaseState;
};

export type TaskEvidence = {
  id: string;
  title: string;
  source: string;
  status: "running" | "done" | "failed";
  summary?: string;
};

/**
 * The durable job and the live stream describe one lifecycle from different
 * transports.  A live turn always wins over a cached message result: a result
 * can be present while the server is still producing the final answer.
 */
export type TaskProgressLifecycle = {
  turnRunning?: boolean;
};

const STAGE_INDEX: Record<string, number> = {
  turn_started: 0,
  planner_started: 0,
  planner_completed: 0,
  graph_compiled: 0,
  structural_validated: 0,
  semantic_validated: 0,
  semantic_invalid: 0,
  pre_repair_started: 0,
  pre_repair_completed: 0,
  risk_assessed: 0,
  budget_ok: 0,
  execution_started: 1,
  orchestration_planned: 1,
  orchestration_layer_started: 1,
  orchestration_layer_completed: 1,
  tool_call: 1,
  tool_result: 1,
  execution_completed: 1,
  repair_attempt: 1,
  merge_completed: 2,
  response_started: 2,
  model_started: 2,
  response_completed: 3,
  turn_completed: 3,
  cognitive_initialized: 0,
  cognitive_goal_normalized: 0,
  cognitive_plan_selected: 0,
  cognitive_evidence_registered: 1,
  cognitive_gap_detected: 2,
  cognitive_decision_made: 2,
  cognitive_reflection_started: 2,
  cognitive_reflection_completed: 2,
  cognitive_stop_decided: 3,
};

const PHASE_COPY = [
  ["understand", "理解问题", "识别目标、范围与执行约束"],
  ["evidence", "收集证据", "调用合适的工具获取真实信息"],
  ["analysis", "分析判断", "关联证据并检查结果是否充分"],
  ["answer", "形成建议", "组织结论与可执行的下一步"],
] as const;

function eventType(event: RuntimeEvent): string {
  return String(event.event_type || event.type || event.name || "").toLowerCase();
}

function toolSource(toolId: string): string {
  const value = toolId.toLowerCase();
  if (value.includes("web") || value.includes("search")) return "网络检索";
  if (value.includes("knowledge")) return "知识库";
  if (value.includes("memory")) return "长期记忆";
  if (value.includes("file") || value.includes("document") || value.includes("pdf")) return "文档与附件";
  if (value.includes("device") || value.includes("network")) return "网络设备";
  if (value.includes("python") || value.includes("exec") || value.includes("data")) return "分析工具";
  if (value.includes("browser")) return "浏览器";
  if (value.includes("workspace")) return "工作区";
  return "平台工具";
}

function displayToolName(toolId: string): string {
  const aliases: Array<[RegExp, string]> = [
    [/web|search/i, "信息检索"],
    [/knowledge/i, "知识库查询"],
    [/memory/i, "记忆检索"],
    [/file|document|pdf/i, "文件分析"],
    [/device|network/i, "网络状态检查"],
    [/python|data/i, "数据分析"],
    [/exec/i, "本地检查"],
    [/browser/i, "网页检查"],
    [/workspace/i, "工作区读取"],
  ];
  return aliases.find(([pattern]) => pattern.test(toolId))?.[1]
    || toolId.split(/[.:/_-]/).filter(Boolean).slice(-2).join(" · ")
    || "工具调用";
}

export function buildTaskProgress(
  message: ChatMsg | undefined,
  snapshot?: ActiveTurnSnapshot,
  lifecycle: TaskProgressLifecycle = {},
): { phases: TaskPhase[]; evidence: TaskEvidence[]; activeIndex: number; status: string } {
  const result = message?.result;
  const events = (snapshot?.events?.length ? snapshot.events : message?.runtimeEvents?.length
    ? message.runtimeEvents
    : result?.events) || [];
  const lastStage = String(snapshot?.stage || [...events].reverse().map(eventType).find(Boolean) || "");
  // `turnRunning` is intentionally an explicit override.  The page knows both
  // the WebSocket lifecycle and the durable job lifecycle; neither an earlier
  // `result` object nor a stale active-turn snapshot may turn that into a
  // completed UI state.
  const isStreaming = lifecycle.turnRunning ?? (snapshot?.status === "running" || message?.status === "streaming");
  const failed = !isStreaming && (snapshot?.status === "failed" || message?.status === "error" || Boolean(result && !result.ok));
  const completed = !isStreaming && (snapshot?.status === "succeeded" || Boolean(result && message?.status !== "streaming"));
  const activeIndex = completed ? 3 : Math.max(0, STAGE_INDEX[lastStage] ?? (isStreaming ? 0 : 0));

  const phases: TaskPhase[] = PHASE_COPY.map(([id, title, description], index) => {
    let state: TaskPhaseState = "idle";
    if (completed) state = "done";
    else if (index < activeIndex) state = "done";
    else if (index === activeIndex && isStreaming) state = "active";
    if (failed && index === activeIndex) state = "failed";
    return { id, title, description, state };
  });

  const liveTools = snapshot?.tool_calls || [];
  const messageTools = message?.toolCalls || result?.tool_calls || [];
  const evidence = (liveTools.length ? liveTools : messageTools).map((tool, index) => {
    const toolId = String(tool.tool_id || "");
    const rawStatus = String("status" in tool ? tool.status || "" : "");
    const ok = Boolean(tool.ok);
    const status: TaskEvidence["status"] = rawStatus === "running"
      ? "running"
      : (ok || rawStatus === "done") ? "done" : "failed";
    return {
      id: String(("call_id" in tool && tool.call_id) || `${toolId}-${index}`),
      title: displayToolName(toolId),
      source: toolSource(toolId),
      status,
      summary: String(tool.summary || "").trim() || undefined,
    };
  });

  return {
    phases,
    evidence,
    activeIndex,
    status: failed ? "failed" : completed ? "succeeded" : isStreaming ? "running" : "idle",
  };
}
