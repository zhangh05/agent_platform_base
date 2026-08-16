import { memo, useState } from "react";
import { useSessionStore } from "../../../stores/session";
import { useToastStore } from "../../../stores/toast";
import { knowledgeApi, memoryApi } from "../../../api";
import { apiRequest } from "../../../api/client";
import { IconBolt } from "../../../components/Icon";
import { TaskTrackingCard } from "../../../components/TaskTrackingCard";
import { toolLabel } from "../../../utils/displayText";
import { isApiError } from "../../../types";
import type { AgentResult, SourceSummary, ToolCallResult } from "../../../types";

interface ResultInlineProps {
  result: AgentResult | undefined;
  fallbackText: string;
  onRetryOriginal?: () => void;
  onRetryAlternative?: () => void;
}

function retryStats(result?: AgentResult) {
  const summary = result?.metadata?.retry_summary || {};
  const events = result?.metadata?.retry_events || [];
  return {
    summary,
    events,
    attempts: Number(summary.retry_attempts || 0),
    succeeded: Number(summary.retry_succeeded || 0),
    failed: Number(summary.retry_failed || 0),
    blocked: Number(summary.retry_blocked || 0),
  };
}

function validationCorrectionStats(result?: AgentResult) {
  const summary = result?.metadata?.validation_correction_summary || {};
  return {
    attempts: Number(summary.attempts || 0),
    exhausted: Boolean(summary.exhausted),
  };
}

function retryBlockedLabel(reason?: string): string {
  const value = String(reason || "");
  if (value === "non_idempotent" || value === "execute_command_not_retryable" || value.includes("side_effect_not_retryable")) {
    return "未原样重放，避免重复副作用";
  }
  return `未自动重试：${value || "不满足安全重试条件"}`;
}

type TrackingSummary = NonNullable<AgentResult["metadata"]["tracking_summary"]>;
type TrackingEvent = NonNullable<AgentResult["metadata"]["tracking_events"]>[number];

function trackingStats(result?: AgentResult) {
  const summary: TrackingSummary = result?.metadata?.tracking_summary ?? ({} as TrackingSummary);
  const events: TrackingEvent[] = result?.metadata?.tracking_events ?? [];
  return {
    summary,
    events,
    taskId: String(summary.task_id || ""),
    status: String(summary.status || ""),
    done: Boolean(summary.done || summary.terminal),
    mode: String(summary.mode || ""),
    nextPollSeconds: Number(summary.next_poll_seconds || 0),
    suggestedNextAction: String(summary.suggested_next_action || ""),
    progress: summary.progress || ({} as Record<string, unknown>),
    taskSummary: summary.summary || ({} as Record<string, unknown>),
    stallRisk: Boolean(summary.stall_risk),
  };
}

function toolCallSummary(calls: ToolCallResult[]): string {
  const failed = calls.filter((tc) => !tc.ok).length;
  const recovered = calls.some((tc) => !tc.ok && calls.some((other) => other.ok && other.tool_id === tc.tool_id));
  const primary = calls.find((tc) => tc.ok) ?? calls[0];
  const label = primary ? toolLabel(primary.tool_id) : "工具调用";
  if (failed > 0 && recovered) return `${label}已完成，${failed} 次内部重试已自动恢复`;
  if (failed > 0) return `${label}需要关注，${failed} 次调用未完成`;
  return `${label}已完成`;
}

export const ResultInline = memo(function ResultInline({
  result,
  fallbackText,
  onRetryOriginal,
  onRetryAlternative,
}: ResultInlineProps) {
  const { currentWorkspaceId } = useSessionStore();
  const toast = useToastStore((s) => s.show);
  const [saving, setSaving] = useState<"" | "memory" | "knowledge">("");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const summaries: SourceSummary[] = (result?.metadata?.context_sources ?? result?.metadata?.source_summary ?? []);
  const isFailed = Boolean(result && !result.ok);
  const hasFailedTool = ((result?.tool_calls) ?? []).some((tc) => !tc.ok);
  const finalText = (result?.final_response || fallbackText || "").trim();
  const retry = retryStats(result);
  const validationCorrection = validationCorrectionStats(result);
  const toolRecoveryEvents = result?.metadata?.tool_recovery_events || [];
  const tracking = trackingStats(result);
  const toolCalls = result?.tool_calls ?? [];
  const actionCount = toolCalls.length;
  const failedToolCount = toolCalls.filter((tc) => !tc.ok).length;
  const successToolCount = toolCalls.filter((tc) => tc.ok).length;
  const contextCompacted = Boolean(result?.metadata?.context_compacted);
  const outputTruncated = Boolean(result?.metadata?.output_truncated);
  const truncationReason = String(result?.metadata?.output_truncation_reason || "");
  const executionOutcome = result?.metadata?.execution_outcome;
  const isUnknownOutcome = executionOutcome === "unknown";
  const unknownOutcome = result?.metadata?.unknown_outcome;
  const showActionTrace = !!result && (actionCount > 0 || retry.events.length > 0 || validationCorrection.attempts > 0 || toolRecoveryEvents.length > 0 || tracking.taskId || isFailed || isUnknownOutcome);

  // Nothing to show — no result and no fallback text
  if (!result && !fallbackText) return null;

  async function rememberAnswer() {
    if (!finalText) { toast({ kind: "warning", title: "无法保存", body: "当前回答内容为空" }); return; }
    if (!currentWorkspaceId) { toast({ kind: "warning", title: "未选择工作区", body: "请先在左侧选择工作区" }); return; }
    if (saving) return;
    setSaving("memory");
    try {
      const res = await memoryApi.create({
        workspace_id: currentWorkspaceId,
        title: finalText.slice(0, 42) || "本次结论",
        content: finalText,
        memory_type: "knowledge_note",
        tags: ["agent_answer", "confirmed"],
        user_confirmed: true,
      });
      // Also save to unified files for File Manager visibility
      try {
        const file = new File([finalText], `${finalText.slice(0, 30)}.txt`, { type: "text/plain" });
        const form = new FormData();
        form.append("file", file);
        form.append("artifact_type", "memory");
        form.append("title", finalText.slice(0, 42) || "本次结论");
        form.append("workspace_id", currentWorkspaceId);
        await apiRequest({ method: "POST", url: `/workspaces/${currentWorkspaceId}/artifacts/upload`, data: form });
      } catch {}
      if (res.conflict) {
        toast({ kind: "warning", title: "已记录，但发现冲突", body: "这条记忆和已有记忆可能不一致，请稍后在记忆列表核对。" });
      } else {
        toast({ kind: "success", title: "已记住", body: "后续对话会通过 RAG 召回这条结论" });
      }
    } catch (e: unknown) {
      toast({ kind: "error", title: "记忆失败", body: isApiError(e) ? e.message : String(e) });
    } finally {
      setSaving("");
    }
  }

  async function saveAsKnowledge() {
    if (!finalText) { toast({ kind: "warning", title: "无法保存", body: "当前回答内容为空" }); return; }
    if (!currentWorkspaceId) { toast({ kind: "warning", title: "未选择工作区", body: "请先在左侧选择工作区" }); return; }
    if (saving) return;
    setSaving("knowledge");
    try {
      const title = `对话结论-${new Date().toISOString().slice(0, 10)}`;
      const body = `# ${title}\n\n${finalText}\n`;
      const file = new File([body], `${title}.md`, { type: "text/markdown" });
      await knowledgeApi.upload(currentWorkspaceId, file, {
        title,
        tags: "agent_answer,chat",
        source_type: "project_doc",
        scope: "workspace",
        language: "zh",
      });
      // Also save to unified files for File Manager visibility
      try {
        const form = new FormData();
        form.append("file", file);
        form.append("artifact_type", "knowledge");
        form.append("title", title);
        form.append("workspace_id", currentWorkspaceId);
        await apiRequest({ method: "POST", url: `/workspaces/${currentWorkspaceId}/artifacts/upload`, data: form });
      } catch {}
      toast({ kind: "success", title: "已保存到知识库", body: "这条回答已整理为可检索文档" });
    } catch (e: unknown) {
      toast({ kind: "error", title: "保存失败", body: isApiError(e) ? e.message : String(e) });
    } finally {
      setSaving("");
    }
  }

  return (
    <div className="chat-result-inline">
      <details
        className="result-inline-disclosure"
        data-testid="result-inline-disclosure"
        open={detailsOpen}
        onToggle={(event) => setDetailsOpen(event.currentTarget.open)}
      >
        <summary className="result-overview-toggle" aria-label={detailsOpen ? "收起执行详情" : "展开执行详情"}>
          <span className="result-overview-toggle-label">{detailsOpen ? "收起执行详情" : "展开执行详情"}</span>
      {result ? (
        <section className="result-overview" aria-label="执行摘要">
          <div className="result-overview-main">
            <span className={`result-overview-status ${isUnknownOutcome ? "unknown" : isFailed ? "failed" : "complete"}`}>
              {isUnknownOutcome ? "结果未知" : isFailed ? "需要关注" : "本轮完成"}
            </span>
            <span className="result-overview-title">
              {actionCount > 0 ? `已处理 ${actionCount} 个工具调用` : "已生成本轮答复"}
            </span>
          </div>
          <span className="result-overview-meta">
            {isUnknownOutcome ? "写入已冻结，等待受控核对" : failedToolCount > 0 ? `${failedToolCount} 项需要跟进` : successToolCount > 0 ? `${successToolCount} 项执行成功` : "可将结论沉淀到记忆或知识库"}
          </span>
        </section>
      ) : (
        <span className="result-overview-fallback">本轮答复</span>
      )}
        </summary>
        <div className="result-inline-details">
      {isUnknownOutcome && (
        <section className="unknown-outcome-alert" role="alert" data-testid="unknown-outcome-alert">
          <strong>执行结果未知，系统已冻结后续写操作</strong>
          <p>
            外部写操作可能仍在执行。请先通过受控 read-back、运行审计或人工核对确认事实，
            <b>不要重试原任务或换方案继续</b>。
          </p>
          <div className="unknown-outcome-facts">
            {unknownOutcome?.tool_id && <span>工具：{unknownOutcome.tool_id}</span>}
            {unknownOutcome?.call_id && <span>调用：{unknownOutcome.call_id}</span>}
            {unknownOutcome?.error_code && <span>代码：{unknownOutcome.error_code}</span>}
          </div>
          <a className="unknown-outcome-link" href="/runs?view=audit">查看任务审计</a>
        </section>
      )}
      {(contextCompacted || outputTruncated) && (
        <div
          className={`context-budget-notice ${outputTruncated ? "warning" : ""}`}
          data-testid="context-budget-notice"
        >
          {outputTruncated
            ? truncationReason === "timeout"
              ? "模型响应超时，当前展示的是已接收内容。"
              : "回复达到输出长度上限，当前内容可能不完整。"
            : "较早的运行上下文已压缩，最近对话和关键任务引用仍被保留。"}
        </div>
      )}
      {((result?.tool_calls) ?? []).length > 0 && (
        <div className="chat-tool-summary" data-testid="inline-tool-summary">
          <IconBolt size={10} className="inline-icon-accent" />
          <span>{toolCallSummary(result?.tool_calls ?? [])}</span>
          <details className="inline-technical-details">
            <summary>技术详情</summary>
            <div className="chat-tool-calls">
              {(result?.tool_calls ?? []).map((tc: ToolCallResult, idx: number) => (
                <span key={tc.call_id || `${tc.tool_id}-${idx}`} className="chat-tool-call">
                  <span className="tc-name">{toolLabel(tc.tool_id)}</span>
                  <span className={"tc-status " + (tc.ok ? "ok" : "err")}>
                    {tc.ok ? "已完成" : "需关注"}
                  </span>
                </span>
              ))}
            </div>
          </details>
        </div>
      )}

      {showActionTrace && (
        <div className="action-trace-panel" data-testid="action-trace-panel">
          <div className="action-trace-head">
            <span className="action-trace-title">动作跟踪</span>
            <span className="action-trace-pill">{actionCount} 个工具</span>
            <span className="action-trace-pill ok">{successToolCount} 成功</span>
            {failedToolCount > 0 && <span className="action-trace-pill danger">{failedToolCount} 需关注</span>}
            {retry.attempts > 0 && <span className="action-trace-pill warn">{retry.attempts} 次自动重试</span>}
            {validationCorrection.attempts > 0 && (
              <span className={`action-trace-pill ${validationCorrection.exhausted ? "danger" : "ok"}`}>
                {validationCorrection.attempts} 次参数自纠
              </span>
            )}
            {toolRecoveryEvents.length > 0 && <span className="action-trace-pill ok">{toolRecoveryEvents.length} 次改策略继续</span>}
            {retry.blocked > 0 && <span className="action-trace-pill muted">{retry.blocked} 次未重试</span>}
          </div>
          {retry.events.length > 0 ? (
            <div className="action-retry-list">
              {retry.events.slice(0, 4).map((ev, i) => (
                <div className="action-retry-row" key={`${ev.node_id || ev.tool_id || "retry"}-${i}`}>
                  <span className={`action-retry-dot ${ev.retry_allowed ? (ev.final_status === "succeeded" ? "ok" : "warn") : "muted"}`} />
                  <span className="action-retry-main">
                    <b>{toolLabel(String(ev.tool_id || ev.node_id || "工具"))}</b>
                    {ev.retry_allowed
                      ? ev.final_status === "succeeded"
                        ? " 首次失败后已恢复"
                        : " 已重试但仍失败"
                      : ` ${retryBlockedLabel(ev.reason)}`}
                  </span>
                  {ev.backoff_ms ? <span className="action-retry-meta">{ev.backoff_ms}ms</span> : null}
                </div>
              ))}
            </div>
          ) : actionCount > 0 ? (
            <div className="action-trace-note">
              本轮没有触发自动重试；危险命令和有副作用动作不会自动重试。
            </div>
          ) : (
            <div className="action-trace-note">
              本轮失败发生在工具调用前，未触发可重试动作。
            </div>
          )}
          {validationCorrection.attempts > 0 && (
            <div className="action-trace-note">
              工具参数校验未通过后已交由模型修正
              {validationCorrection.exhausted ? "，达到上限后停止，未执行无效调用。" : "，无效调用未进入执行器。"}
            </div>
          )}
          {toolRecoveryEvents.length > 0 && (
            <div className="action-trace-note">
              原调用未被盲目重复，模型已收到失败证据并继续选择安全替代方案。
            </div>
          )}
          {tracking.taskId && (
            <div className="action-trace-note">
              <b>任务跟踪 · {tracking.taskId}</b>
              <span className="tracking-status">
                状态 {tracking.status || "unknown"}
                {tracking.mode ? ` · ${tracking.mode}` : ""}
                {tracking.progress?.percent != null ? ` · ${tracking.progress.percent}%` : ""}
              </span>
              {tracking.stallRisk && <span className="action-trace-pill warn tracking-stall-pill">可能停滞</span>}
              {!tracking.done && (
                <div className="tracking-card-wrap">
                  <TaskTrackingCard tracking={tracking.summary} />
                </div>
              )}
              {tracking.done && (
                <div className="tracking-summary">
                  设备 {String(tracking.taskSummary.succeeded_devices ?? 0)} 成功 / {String(tracking.taskSummary.failed_devices ?? 0)} 失败 / {String(tracking.taskSummary.skipped_devices ?? 0)} 跳过；
                  发现 {String(tracking.taskSummary.findings_critical ?? 0)} 个严重问题 · {String(tracking.taskSummary.findings_warning ?? 0)} 个警告 · {String(tracking.taskSummary.findings_info ?? 0)} 条提示。
                  {tracking.suggestedNextAction === "analyze_artifacts" ? " 下一步：读取原始采集结果并分析。" : ""}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {Array.isArray(summaries) && summaries.length > 0 && (
        <div className="chat-source-summary" data-testid="inline-source-summary">
          <b>参考来源 · {summaries.length} 个</b>
          <div className="chat-source-list">
            {summaries.slice(0, 6).map((s: SourceSummary, i: number) => (
              <span className="chat-source-chip" key={s.citation_id || s.chunk_id || s.source_id || i}>
                {s.citation_id ? `${s.citation_id} · ` : ""}
                {s.evidence_type === "memory" ? "记忆" : "知识"} · {s.title || s.source_id}
                <span className="score">{s.score != null ? ` ${Number(s.score).toFixed(2)}` : ""}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="result-actions">
          <button type="button" className="run-detail-button" onClick={() => void rememberAnswer()} disabled={!!saving}>
            {saving === "memory" ? "记录中…" : "记住结论"}
          </button>
          <button type="button" className="run-detail-button" onClick={() => void saveAsKnowledge()} disabled={!!saving}>
            {saving === "knowledge" ? "保存中…" : "存为知识"}
          </button>
          {hasFailedTool && !isUnknownOutcome && onRetryAlternative && (
            <button type="button" className="run-detail-button" onClick={onRetryAlternative}>
              换方案继续
            </button>
          )}
          {isFailed && !isUnknownOutcome && onRetryOriginal && (
            <button type="button" className="run-detail-button" onClick={onRetryOriginal}>
              重试原任务
            </button>
          )}
          {Array.isArray(summaries) && summaries.length > 0 && (
            <span className="run-detail-info">来源 ({summaries.length})</span>
          )}
        </div>

      {isFailed && result?.errors && result.errors.length > 0 && (
        <details className="mt-2">
          <summary className="wb-run-detail">技术详情</summary>
          <div className="text-xs mono mt-1 technical-error">
            {(result?.errors ?? []).join("\n")}
          </div>
        </details>
      )}
        </div>
      </details>
    </div>
  );
});
