import { useEffect, useRef, useState } from "react";
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

function auditRunId(turn: RuntimeAuditTurn, index: number): string {
  return turn.run_id || turn.turn_id || turn.trace_id || `run-${index + 1}`;
}

function auditRunLabel(turn: RuntimeAuditTurn, index: number): string {
  const summary = turn.user_input_summary || turn.intent || "";
  if (summary) return summary.length > 34 ? `${summary.slice(0, 34)}…` : summary;
  return `运行 ${index + 1}`;
}

export function RuntimeAudit() {
  const { currentWorkspaceId } = useSessionStore();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

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
  const parentRef = useRef<HTMLDivElement | null>(null);
  const virtualizer = useVirtualizer({
    count: events.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 120,
    overscan: 8,
    measureElement: (el) => el?.getBoundingClientRect().height ?? 120,
  });

  return (
    <div className="page" data-testid="page-audit">
      <div className="page-header">
        <div>
          <h1>
            运行审计{" "}
            <span className="ra-title-suffix">
              · Runtime Audit
            </span>
          </h1>
          <div className="subtitle">
            turn 时间线 · 模型 I/O · 工具调用 · provider 错误
          </div>
        </div>
      </div>
      <div className="split-shell">
        <aside className="ra-aside">
          <div className="ra-sidebar-card">
            <div className="section-head ra-section-head-sm">
              <IconClock size={11} /> 最近 turn
            </div>
            <AsyncView
              state={turns.state}
              onRetry={turns.reload}
              emptyText="无 turn 记录"
              emptyHint="等待 agent run 出现"
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
              <h1 className="hero-title">未选择 turn</h1>
              <p className="hero-sub">在左侧选择一个 turn，查看 trace 事件流</p>
            </div>
          ) : (
            <>
              {trace.state.kind === "loading" && (
                <div className="row-flex">
                  <span className="spinner" /> 加载 trace…
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
                      <div className="empty-text">该 turn 无 event</div>
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
                      <div
                        ref={parentRef}
                        className="list-scroll ra-events-scroll"
                        data-testid="audit-events"
                      >
                        <div
                          ref={(el) => {
                            if (el) el.style.setProperty("--ra-h", `${virtualizer.getTotalSize()}px`);
                          }}
                          className="ra-virtual-inner"
                        >
                          {virtualizer.getVirtualItems().map((vi) => {
                            const ev = events[vi.index];
                            const eventType = ev.event_type || ev.type || "unknown";
                            const details = formatEventDetail(ev);
                            const label = formatEventLabel(ev);
                            const isOk = eventType !== "turn_failed";
                            return (
                              <div
                                key={ev.event_id}
                                data-index={vi.index}
                                ref={(node) => {
                                  virtualizer.measureElement(node);
                                  if (node) node.style.setProperty("--ra-t", `translateY(${vi.start}px)`);
                                }}
                                className="card ra-event-card"
                              >
                                <div className="row-flex ra-justify-between">
                                  <span className="row-flex min-w-0">
                                    <span className={"status-dot ra-dot-sm " + (isOk ? "ok" : "err")} />
                                    <span className="text-sm">{label}</span>
                                  </span>
                                  <span className="muted text-xs mono">{formatEventTime(ev)}</span>
                                </div>
                                <details className="collapse mt-2">
                                  <summary className="ra-collapse-summary">开发诊断 · {eventType}</summary>
                                  <CodeBlock language="json">
                                    {JSON.stringify(details, null, 2)}
                                  </CodeBlock>
                                </details>
                              </div>
                            );
                          })}
                        </div>
                      </div>
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
