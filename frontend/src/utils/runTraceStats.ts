import type { RuntimeAuditTurn, RuntimeEvent } from "../types";

export interface RunTraceStats {
  toolCallCount: number;
  warningCount: number;
  errorCount: number;
  startedAt: string;
  finishedAt: string;
  visibleTools: string[];
}

export function traceEventType(ev: RuntimeEvent): string {
  return String(ev.event_type || ev.type || ev.name || "").toLowerCase();
}

function eventLevel(ev: RuntimeEvent): string {
  return String(ev.level || ev.status || "").toLowerCase();
}

export function eventToolId(ev: RuntimeEvent): string {
  const meta = (ev.metadata || ev.payload || {}) as Record<string, unknown>;
  if (typeof meta.canonical_tool_id === "string") return meta.canonical_tool_id;
  if (typeof meta.tool_id === "string") return meta.tool_id;
  if (typeof ev.tool_id === "string") return ev.tool_id;
  return "";
}

function eventTimestamp(ev: RuntimeEvent): string {
  const value = ev.occurred_at || ev.started_at || ev.timestamp;
  return value == null ? "" : String(value);
}

export function isTraceToolEvent(ev: RuntimeEvent): boolean {
  const type = traceEventType(ev);
  return type.includes("tool") || type.includes("approval") || !!eventToolId(ev);
}

export function isTraceWarningEvent(ev: RuntimeEvent): boolean {
  const type = traceEventType(ev);
  const level = eventLevel(ev);
  return type.includes("warn") || level === "warn" || level === "warning";
}

export function isTraceErrorEvent(ev: RuntimeEvent): boolean {
  const type = traceEventType(ev);
  const level = eventLevel(ev);
  return type.includes("error") || type.includes("fail") || level === "err" || level === "error";
}

export function isTraceLlmEvent(ev: RuntimeEvent): boolean {
  const type = traceEventType(ev);
  return type.includes("model") || type.includes("llm") || type.includes("assistant");
}

export function isTraceNodeEvent(ev: RuntimeEvent): boolean {
  const type = traceEventType(ev);
  return type.includes("node") || type.includes("agent") || ["router", "context_loader"].includes(type);
}

export function isTraceCapabilityEvent(ev: RuntimeEvent): boolean {
  const type = traceEventType(ev);
  return type.includes("capability") || type.includes("skill") || type === "capability_call";
}

export function deriveRunTraceStats(
  run: Partial<RuntimeAuditTurn> | null,
  traceEvents: RuntimeEvent[] | null | undefined,
): RunTraceStats {
  const events = Array.isArray(traceEvents) ? traceEvents : [];
  const toolIds = new Set<string>();
  let anonymousToolEvents = 0;
  let warningCount = 0;
  let errorCount = 0;
  const timestamps: string[] = [];

  for (const ev of events) {
    const stamp = eventTimestamp(ev);
    if (stamp) timestamps.push(stamp);
    if (isTraceToolEvent(ev)) {
      const toolId = eventToolId(ev);
      if (toolId) toolIds.add(toolId);
      else anonymousToolEvents += 1;
    }
    if (isTraceWarningEvent(ev)) warningCount += 1;
    if (isTraceErrorEvent(ev)) errorCount += 1;
  }

  const existingTools = Array.isArray(run?.visible_tools) ? run.visible_tools.filter(Boolean) : [];
  for (const tool of existingTools) toolIds.add(tool);

  const derivedToolCount = toolIds.size || anonymousToolEvents;
  return {
    toolCallCount: Math.max(Number(run?.tool_call_count || 0), derivedToolCount),
    warningCount: Math.max(Number(run?.warning_count || 0), warningCount),
    errorCount: Math.max(Number(run?.error_count || 0), errorCount),
    startedAt: run?.started_at || timestamps[0] || run?.created_at || "",
    finishedAt: run?.finished_at || timestamps[timestamps.length - 1] || "",
    visibleTools: Array.from(toolIds),
  };
}
