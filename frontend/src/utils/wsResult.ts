import type { AgentResult, ToolCallResult } from "../types";
import { sanitizeAssistantText } from "./displayText";

export interface WsDonePayload {
  final_response?: string;
  session_id?: string;
  turn_id?: string;
  trace_id?: string;
  events?: AgentResult["events"];
  tool_calls?: ToolCallResult[];
  metadata?: Record<string, unknown>;
  errors?: string[];
  warnings?: string[];
  tool_decision?: AgentResult["tool_decision"];
  no_tool_reason?: string;
}

export function agentResultFromWsDone(
  payload: WsDonePayload,
  streamedText: string,
  fallbackSessionId: string,
): AgentResult {
  const finalText = streamedText || payload.final_response || "未收到可显示的模型回复，请重试。";
  return {
    ok: !(payload.errors?.length),
    final_response: sanitizeAssistantText(finalText),
    events: payload.events || [],
    trace_id: payload.trace_id || "—",
    session_id: payload.session_id || fallbackSessionId || "—",
    turn_id: payload.turn_id || `turn-${Date.now()}`,
    tool_calls: payload.tool_calls || [],
    warnings: payload.warnings || [],
    errors: payload.errors || [],
    metadata: payload.metadata || {},
    tool_decision: payload.tool_decision,
    no_tool_reason: payload.no_tool_reason,
  };
}
