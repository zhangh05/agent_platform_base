/**
 * TraceDetailPanel — 追踪详情面板 (v3.3.1 美化)
 *
 * 交互式 trace 查看器：筛选、搜索、可展开 JSON。
 * Shared by RunsPage.
 */
import { useState, useMemo } from "react";
import { Badge } from "./common";
import {
  deriveRunTraceStats,
  eventToolId,
  isTraceErrorEvent,
  isTraceLlmEvent,
  isTraceNodeEvent,
  isTraceCapabilityEvent,
  isTraceToolEvent,
  isTraceWarningEvent,
  traceEventType,
} from "../utils/runTraceStats";
import type { RuntimeEvent, RuntimeAuditTurn } from "../types";

interface Props {
  traceEvents: RuntimeEvent[] | null;
  selectedRun: RuntimeAuditTurn | null;
}

type EventFilter = "all" | "tool" | "skill" | "warning" | "error" | "llm" | "node";

const FILTER_LABELS: Record<EventFilter, string> = {
  all: "全部", tool: "工具", skill: "能力", warning: "警告", error: "错误", llm: "LLM", node: "节点",
};

export function TraceDetailPanel({ traceEvents, selectedRun }: Props) {
  const [filter, setFilter] = useState<EventFilter>("all");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showMeta, setShowMeta] = useState(false);

  const toggle = (id: string) => setExpanded((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const filtered = useMemo(() => {
    if (!traceEvents) return [];
    let r = traceEvents;
    if (filter !== "all") {
      r = r.filter((e) => {
        if (filter === "tool") return isTraceToolEvent(e);
        if (filter === "skill") return isTraceCapabilityEvent(e);
        if (filter === "warning") return isTraceWarningEvent(e);
        if (filter === "error") return isTraceErrorEvent(e);
        if (filter === "llm") return isTraceLlmEvent(e);
        if (filter === "node") return isTraceNodeEvent(e);
        return true;
      });
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      r = r.filter((e) =>
        traceEventType(e).includes(q) ||
        (e.tool_id || "").toLowerCase().includes(q) ||
        (eventToolId(e) || "").toLowerCase().includes(q) ||
        (e.summary || "").toLowerCase().includes(q) ||
        (e.name || "").toLowerCase().includes(q) ||
        (e.message || "").toLowerCase().includes(q)
      );
    }
    return r;
  }, [traceEvents, filter, search]);

  const counts = useMemo(() => {
    if (!traceEvents) return {} as Record<string, number>;
    const c: Record<string, number> = { all: traceEvents.length };
    for (const e of traceEvents) {
      if (isTraceToolEvent(e)) c.tool = (c.tool || 0) + 1;
      if (isTraceCapabilityEvent(e)) c.skill = (c.skill || 0) + 1;
      if (isTraceWarningEvent(e)) c.warning = (c.warning || 0) + 1;
      if (isTraceErrorEvent(e)) c.error = (c.error || 0) + 1;
      if (isTraceLlmEvent(e)) c.llm = (c.llm || 0) + 1;
      if (isTraceNodeEvent(e)) c.node = (c.node || 0) + 1;
    }
    return c;
  }, [traceEvents]);

  const runStats = useMemo(
    () => deriveRunTraceStats(selectedRun, traceEvents),
    [selectedRun, traceEvents],
  );

  if (!selectedRun) return null;
  if (traceEvents === null && selectedRun.trace_id) {
    return (
      <div className="card trace-loading">
        正在加载 trace…
      </div>
    );
  }

  return (
    <div className="trace-panel">
      {/* ── Summary bar ── */}
      <div className="trace-summary-bar">
        <span className="trace-summary-title">Trace · {traceEvents?.length ?? 0} events</span>
        <span className="trace-summary-id">
          {String(selectedRun.trace_id || "-").substring(0, 12)}
        </span>
        <div className="trace-spacer" />
        <button className="btn sm" onClick={() => { const t = JSON.stringify(traceEvents, null, 2); navigator.clipboard?.writeText(t).catch(() => {}); }}>
          📋 复制
        </button>
      </div>

      {/* ── Quick stats ── */}
      <div className="trace-stats-bar">
        <span className="badge info">{runStats.toolCallCount} 工具</span>
        <span className="badge warn">{runStats.warningCount} 警告</span>
        <span className="badge err">{runStats.errorCount} 错误</span>
        {runStats.startedAt && (
          <span className="trace-stats-time">
            {String(runStats.startedAt)}
          </span>
        )}
      </div>

      {/* ── Tool decision fold ── */}
      {selectedRun.tool_decision && (
        <details className="trace-decision-fold">
          <summary className="trace-decision-summary">tool_decision</summary>
          <pre className="trace-decision-pre">
            {JSON.stringify(selectedRun.tool_decision, null, 2)}
          </pre>
        </details>
      )}

      {/* ── Filter bar ── */}
      <div className="segmented trace-filter-bar">
        {(Object.entries(FILTER_LABELS) as [EventFilter, string][]).map(([k, v]) => (
          <button key={k} className={filter === k ? "active" : ""} onClick={() => setFilter(k)} type="button">
            {v}{counts[k] ? ` ${counts[k]}` : ""}
          </button>
        ))}
      </div>

      <input
        className="input trace-search-input" type="text" placeholder="搜索 event…" value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      {/* ── Event list ── */}
      <div className="trace-event-list">
        {filtered.length === 0 ? (
          <div className="trace-empty-state">
            {search ? "无匹配事件" : "暂无事件"}
          </div>
        ) : (
          filtered.map((e, idx) => {
            const open = expanded.has(e.event_id);
            const rawType = e.event_type || e.type || e.name || "";
            const et = evTypeLabel(rawType, e);
            const badge = evBadge(e);
            const tId = e.tool_id || eventToolId(e);
            return (
              <div key={e.event_id} className="trace-event-item">
                <div
                  className={`trace-event-header${open ? " open" : ""}`}
                  onClick={() => toggle(e.event_id)}
                >
                  <span className="trace-event-index">{idx + 1}</span>
                  <Badge kind={badge} withDot>{et}</Badge>
                  {tId && <code className="trace-event-tool-id">{tId}</code>}
                  {e.name && !tId && <span className="trace-event-name">{e.name}</span>}
                  {e.status && <Badge kind={e.status === "error" || e.status === "failed" ? "err" : "ok"}>{e.status}</Badge>}
                  <span className="trace-event-summary">
                    {e.summary || e.message || ""}
                  </span>
                  <span className="trace-event-time">
                    {e.occurred_at ? String(e.occurred_at).substring(11, 19) : e.timestamp ? String(e.timestamp).substring(11, 19) : ""}
                  </span>
                  <span className="trace-event-toggle">{open ? "▲" : "▼"}</span>
                </div>
                {open && (
                  <div className="trace-event-detail">
                    {e.summary && !e.message && <div className="trace-detail-summary">{e.summary}</div>}
                    {e.message && <div className="trace-detail-message">{e.message}</div>}
                    {e.error && <div className="trace-detail-error">{e.error}</div>}
                    {e.duration_ms && <div className="trace-detail-duration">耗时: {e.duration_ms}ms</div>}
                    {e.approval_id && <div className="trace-detail-approval">审批: {e.approval_id} ({e.approval_status || "pending"})</div>}
                    {e.input_preview && (
                      <details className="trace-detail-fold">
                        <summary className="trace-detail-fold-summary">input</summary>
                        <pre className="trace-detail-pre">
                          {typeof e.input_preview === "string" ? e.input_preview : JSON.stringify(e.input_preview, null, 2)}
                        </pre>
                      </details>
                    )}
                    {e.output_preview && (
                      <details className="trace-detail-fold">
                        <summary className="trace-detail-fold-summary">output</summary>
                        <pre className="trace-detail-pre">
                          {typeof e.output_preview === "string" ? e.output_preview : JSON.stringify(e.output_preview, null, 2)}
                        </pre>
                      </details>
                    )}
                    <details className="trace-detail-fold">
                      <summary className="trace-detail-fold-summary">JSON</summary>
                      <pre className="trace-detail-pre-large">
                        {JSON.stringify(e, null, 2)}
                      </pre>
                    </details>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* ── Full metadata ── */}
      <div className="trace-metadata-section">
        <button className="btn sm" onClick={() => setShowMeta(!showMeta)}>
          {showMeta ? "收起 metadata" : "完整 metadata"}
        </button>
        {showMeta && (
          <div className="trace-metadata-actions">
            <pre className="trace-metadata-pre">
              {JSON.stringify({
                run_id: selectedRun.run_id, turn_id: selectedRun.turn_id, trace_id: selectedRun.trace_id,
                session_id: selectedRun.session_id, status: selectedRun.status, intent: selectedRun.intent,
                started_at: selectedRun.started_at, finished_at: selectedRun.finished_at,
                tool_call_count: runStats.toolCallCount, warning_count: runStats.warningCount,
                error_count: runStats.errorCount, events_count: traceEvents?.length ?? 0,
              }, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}

function evBadge(e: RuntimeEvent): "ok" | "err" | "warn" | "info" | "muted" {
  const et = traceEventType(e);
  const lv = (e.level || "").toLowerCase();
  if (et.includes("error") || et.includes("failed") || lv === "err" || lv === "error") return "err";
  if (et === "warning" || lv === "warn") return "warn";
  if (et.includes("finish") || et.includes("completed") || et === "tool_end") return "ok";
  if (et.includes("skill")) return "info";
  if (et.includes("tool") || et.includes("node") || et.includes("agent")) return "info";
  return "muted";
}

/** Map raw event_type to a human-readable Chinese label. */
function evTypeLabel(rawType: string, ev: RuntimeEvent): string {
  if (!rawType) return "event";
  const t = rawType.toLowerCase();
  // Tool call events
  if (t.includes("tool_call_start")) return "工具开始";
  if (t.includes("tool_call_end") || t.includes("tool_call_finish")) return "工具完成";
  if (t.includes("tool_call_fail")) return "工具失败";
  if (t.includes("tool_call")) return "工具调用";
  // Model/LLM events
  if (t.includes("model_request") || t.includes("llm_request")) return "模型请求";
  if (t.includes("model_response") || t.includes("llm_response")) return "模型响应";
  if (t.includes("model") || t.includes("llm")) return "LLM";
  // Turn lifecycle
  if (t.includes("turn_start")) return "开始处理";
  if (t.includes("turn_finish") || t.includes("turn_end")) return "处理完成";
  if (t.includes("turn_fail")) return "处理失败";
  // Context
  if (t.includes("context_built")) return "构建上下文";
  // Agent/node
  if (t.includes("agent_start")) return "Agent 启动";
  if (t.includes("agent_end")) return "Agent 结束";
  if (t.includes("node_start")) return "节点开始";
  if (t.includes("node_end")) return "节点完成";
  if (t.includes("agent") || t.includes("node")) return "Agent";
  // capability call
  if (t.includes("capability_call")) return "能力调用";
  if (t.includes("module_call")) return "模块调用";
  // Warning/error
  if (t.includes("warning")) return "警告";
  if (t.includes("error")) return "错误";
  // Intent
  if (t.includes("intent")) return "意图路由";
  if (t.includes("approval")) return "审批";
  // Fallback: use event name or summary
  if (ev?.name) return ev.name.length > 10 ? ev.name.slice(0, 10) + "…" : ev.name;
  if (ev?.summary) return ev.summary.length > 10 ? ev.summary.slice(0, 10) + "…" : ev.summary;
  // Last resort: show original type truncated
  return rawType.length > 14 ? rawType.slice(0, 14) + "…" : rawType;
}
