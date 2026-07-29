import { formatDate } from "./format";

export function formatEventTime(ev: any): string {
  const v = ev.occurred_at || ev.timestamp;
  if (v == null) return "—";
  return formatDate(v, "compact");
}

export function formatEventDetail(ev: any): Record<string, unknown> {
  return ev.payload || ev.metadata || {};
}

export function formatEventLabel(ev: any): string {
  const type = ev.event_type || ev.type || "unknown";
  const payload = ev.payload || ev.metadata || {};
  const toolId = typeof payload?.canonical_tool_id === "string"
    ? payload.canonical_tool_id
    : (typeof payload?.tool_id === "string" ? payload.tool_id : "?");
  const map: Record<string, string> = {
    turn_started: "开始处理请求",
    context_built: "构建上下文",
    model_request_started: "发起模型请求",
    model_response_received: "模型返回响应",
    tool_call_started: `调用工具：${toolId}`,
    tool_call_finished: `工具完成：${toolId}`,
    tool_call_failed: `工具失败：${toolId}`,
    assistant_message: "生成回复",
    turn_finished: "处理完成",
    turn_failed: `处理失败：${String(payload?.error || payload || "").slice(0, 60)}`,
    agent_start: ev?.summary || "开始处理请求",
    agent_end: ev?.summary || "处理完成",
    node_start: `开始节点：${ev?.name || "?"}`,
    node_end: `完成节点：${ev?.name || "?"}`,
    intent_routed: ev?.summary || "完成意图路由",
    capability_call_start: `开始能力：${ev?.name || "?"}`,
    capability_call_end: `完成能力：${ev?.name || "?"}`,
    module_call_start: `开始模块：${ev?.name || "?"}`,
    module_call_end: `完成模块：${ev?.name || "?"}`,
  };
  return map[type] || ev?.summary || ev?.name || type;
}
