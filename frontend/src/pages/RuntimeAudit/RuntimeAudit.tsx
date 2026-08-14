import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { runtimeAuditApi } from "../../api";
import {
  useAsync,
  AsyncView,
  Badge,
  CodeBlock,
  InlineCode,
} from "../../components/common";
import { useSessionStore } from "../../stores/session";
import type { RuntimeAuditTurn, RuntimeEvent } from "../../types";
import { IconAlert, IconClock } from "../../components/Icon";
import { APP_EVENTS } from "../../utils/appEvents";
import { formatEventTime, formatEventDetail, formatEventLabel } from "../../utils/runEvent";
import { formatDate } from "../../utils/format";

const STATUS_LABEL: Record<string, string> = {
  ok: "成功",
  failed: "失败",
  running: "运行中",
  timeout: "超时",
  cancelled: "取消",
};

const EVENT_VIRTUALIZATION_THRESHOLD = 80;

function auditRunId(turn: RuntimeAuditTurn, index: number): string {
  return turn.run_id || turn.turn_id || turn.trace_id || `run-${index + 1}`;
}

function auditRunLabel(turn: RuntimeAuditTurn, index: number): string {
  const summary = turn.user_input_summary || turn.intent || "";
  if (summary) return summary.length > 34 ? `${summary.slice(0, 34)}…` : summary;
  return `运行 ${index + 1}`;
}

function auditEventKey(event: RuntimeEvent, index: number): string {
  return String(
    event.event_id
    || [event.event_type || event.type || "unknown", event.occurred_at || event.timestamp || "", index].join(":"),
  );
}

export function RuntimeAudit() {
  const { currentWorkspaceId } = useSessionStore();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [expandedEventKey, setExpandedEventKey] = useState<string | null>(null);

  const turns = useAsync<{ runs: RuntimeAuditTurn[] }>(
    (s) =>
      currentWorkspaceId
        ? runtimeAuditApi.recent(currentWorkspaceId, s)
        : Promise.resolve({ runs: [] }),
    [currentWorkspaceId],
    (d) => (d.runs ?? []).length === 0,
  );

  const trace = useAsync<{ events: RuntimeAuditTurn["events"] }>(
    (s) =>
      currentWorkspaceId && selectedRunId
        ? runtimeAuditApi.trace(currentWorkspaceId, selectedRunId, s)
        : Promise.resolve({ events: [] }),
    [currentWorkspaceId, selectedRunId],
  );

  useEffect(() => {
    const onRunCompleted = () => turns.reload();
    window.addEventListener(APP_EVENTS.RUN_COMPLETED, onRunCompleted);
    return () => window.removeEventListener(APP_EVENTS.RUN_COMPLETED, onRunCompleted);
  }, [turns]);

  // Virtualize the (potentially large) trace-event list so scrolling stays smooth.
  const events = trace.state.kind === "success" ? trace.state.data.events : [];
  const shouldVirtualizeEvents = events.length > EVENT_VIRTUALIZATION_THRESHOLD;
  const parentRef = useRef<HTMLDivElement | null>(null);
  const virtualizer = useVirtualizer({
    count: shouldVirtualizeEvents ? events.length : 0,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 120,
    overscan: 8,
    measureElement: (el) => el?.getBoundingClientRect().height ?? 120,
  });

  useEffect(() => {
    setExpandedEventKey(null);
  }, [selectedRunId]);

  const renderEvent = (ev: RuntimeEvent, index: number, virtualStart?: number) => {
    const eventType = ev.event_type || ev.type || "unknown";
    const details = formatEventDetail(ev);
    const label = formatEventLabel(ev);
    const isOk = eventType !== "turn_failed";
    const eventKey = auditEventKey(ev, index);
    const expanded = expandedEventKey === eventKey;
    const virtual = virtualStart != null;
    return (
      <div
        key={eventKey}
        data-index={virtual ? index : undefined}
        data-testid={`audit-event-${eventKey}`}
        ref={virtual ? virtualizer.measureElement : undefined}
        style={virtual ? ({ "--ra-t": `translateY(${virtualStart}px)` } as CSSProperties) : undefined}
        className={`card ${virtual ? "ra-event-card" : "ra-event-card-static"}`}
      >
        <button
          type="button"
          className="ra-event-toggle"
          aria-expanded={expanded}
          data-testid={`audit-event-toggle-${eventKey}`}
          onClick={() => setExpandedEventKey((current) => current === eventKey ? null : eventKey)}
        >
          <span className="row-flex min-w-0">
            <span className={"status-dot ra-dot-sm " + (isOk ? "ok" : "err")} />
            <span className="text-sm">{label}</span>
          </span>
          <span className="muted text-xs mono">{formatEventTime(ev)}</span>
        </button>
        {expanded ? (
          <div className="ra-event-detail" data-testid={`audit-event-detail-${eventKey}`}>
            <div className="ra-collapse-summary">开发诊断 · {eventType}</div>
            <CodeBlock language="json">
              {JSON.stringify(details, null, 2)}
            </CodeBlock>
          </div>
        ) : null}
      </div>
    );
  };

  return (
    <div className="page" data-testid="page-audit">
      <div className="page-header">
        <div>
          <h1>执行详情</h1>
          <div className="subtitle">
            查看每次任务的处理步骤、模型请求、工具调用和错误
          </div>
        </div>
      </div>
      <div className="split-shell">
        <aside className="ra-aside">
          <div className="ra-sidebar-card">
            <div className="section-head ra-section-head-sm">
              <IconClock size={11} /> 最近执行
            </div>
            <AsyncView
              state={turns.state}
              onRetry={turns.reload}
              emptyText="暂无执行记录"
              emptyHint="完成一次任务后会显示在这里"
            >
              {(d) => (
                <div className="list list-scroll" data-testid="audit-turn-list">
                  {(d.runs ?? []).map((t, i) => {
                    const runId = auditRunId(t, i);
                    const label = auditRunLabel(t, i);

                    return (
                      <button
                        key={runId}
                        type="button"
                        className={
                          "list-item ra-turn-item" +
                          (selectedRunId === runId ? " active" : "")
                        }
                        onClick={() => setSelectedRunId(runId)}
                        data-testid={`turn-${runId}`}
                      >
                        <span className="title text-sm ra-turn-title">
                          问题摘要：{label}
                        </span>
                        <span className="row-flex ra-badge-row">
                          <Badge
                            kind={
                              t.status === "ok"
                                ? "ok"
                                : t.status === "failed"
                                  ? "err"
                                  : "warn"
                            }
                          >
                            结果：{STATUS_LABEL[t.status] || t.status}
                          </Badge>
                          <span className="text-xs muted">时间：{auditTime(t)}</span>
                        </span>
                        <details className="collapse w-full">
                          <summary className="text-xs muted">技术详情</summary>
                          <div className="text-xs muted mono ra-mt-2px">
                            {runId}
                          </div>
                        </details>
                      </button>
                    );
                  })}
                </div>
              )}
            </AsyncView>
          </div>
        </aside>
        <section
          className="split-detail ra-detail-override"
          data-testid="audit-detail"
        >
          {!selectedRunId ? (
            <div className="hero ra-hero-sm">
              <div className="hero-mark">审</div>
              <h1 className="hero-title">未选择执行记录</h1>
              <p className="hero-sub">在左侧选择一条记录，查看详细处理过程</p>
            </div>
          ) : (
            <>
              {trace.state.kind === "loading" && (
                <div className="row-flex">
                  <span className="spinner" /> 正在加载执行详情…
                </div>
              )}
              {trace.state.kind === "error" && (
                <div className="text-sm row-flex ra-error-msg">
                  <IconAlert size={11} /> {trace.state.error.message}
                </div>
              )}
              {trace.state.kind === "success" && (
                <>
                  <div className="row-flex mb-3">
                    <span className="text-sm ra-semibold">运行详情</span>
                    <span className="muted text-sm">
                      {trace.state.data.events.length} 个事件
                    </span>
                    <details className="collapse ml-auto">
                      <summary className="text-xs muted">技术详情</summary>
                      <InlineCode>{selectedRunId}</InlineCode>
                    </details>
                  </div>
                  {trace.state.data.events.length === 0 ? (
                    <div className="empty">
                      <div className="empty-icon">○</div>
                      <div className="empty-text">本次执行没有详细过程记录</div>
                    </div>
                  ) : (
                    <>
                      {(() => {
                        // Extract failure summary for failed turns
                        const failedEv = trace.state.data.events.find(
                          (ev: RuntimeEvent) => ev.event_type === "turn_failed" || ev.type === "turn_failed",
                        );
                        const failedDetails = failedEv ? formatEventDetail(failedEv) : {};
                        const error = failedDetails.error || failedEv?.summary || failedDetails || "";
                        // Extract timeout duration if available
                        const timeoutSecs = (() => {
                          const modelReq = trace.state.data.events.find(
                            (ev: RuntimeEvent) => ev.type === "model_request" || ev.event_type === "model_request",
                          );
                          const modelResp = trace.state.data.events.find(
                            (ev: RuntimeEvent) => ev.type === "model_response" || ev.event_type === "model_response",
                          );
                          const reqTime = modelReq ? formatEventTime(modelReq) : "";
                          const respTime = modelResp ? formatEventTime(modelResp) : "";
                          if (reqTime && respTime) {
                            const t0 = new Date(reqTime).getTime();
                            const t1 = new Date(respTime).getTime();
                            if (t0 && t1) return Math.round((t1 - t0) / 1000);
                          }
                          return null;
                        })();
                        return failedEv ? (
                          <div
                            className="card mb-3 ra-failure-card"
                            data-testid="audit-failure-summary"
                          >
                            <strong>失败原因</strong>
                            <span className="text-sm ml-2">
                              {String(error).slice(0, 200)}
                            </span>
                            {timeoutSecs != null && (
                              <span className="text-sm ml-2">
                                · 耗时 {timeoutSecs}s
                              </span>
                            )}
                          </div>
                        ) : null;
                      })()}
                      {shouldVirtualizeEvents ? (
                        <div
                          ref={parentRef}
                          className="list-scroll ra-events-scroll"
                          data-testid="audit-events"
                        >
                          <div
                            className="ra-virtual-inner"
                            style={{ height: `${virtualizer.getTotalSize()}px` }}
                          >
                            {virtualizer.getVirtualItems().map((vi) => renderEvent(events[vi.index], vi.index, vi.start))}
                          </div>
                        </div>
                      ) : (
                        <div className="list-scroll ra-events-scroll" data-testid="audit-events">
                          {events.map((ev, index) => renderEvent(ev, index))}
                        </div>
                      )}
                    </>
                  )}
                </>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

function auditTime(turn: RuntimeAuditTurn): string {
  const value = turn.created_at || turn.started_at || turn.finished_at;
  return value ? formatDate(value, "time") : "—";
}
