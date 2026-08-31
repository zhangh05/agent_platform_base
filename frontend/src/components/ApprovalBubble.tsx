import { memo, useEffect, useState, useRef, useCallback } from "react";
import { useSessionStore } from "../stores/session";
import { approvalApi, type ApprovalContinuationSummary } from "../api";
import { IconAlert, IconCheck, IconClose, IconClock } from "./Icon";
import "./ApprovalBubble.css";

interface PendingApproval {
  approval_id: string;
  session_id: string;
  tool_id: string;
  description?: string;
  risk_level: string;
  arguments_preview?: Record<string, unknown>;
  arguments_summary?: string;
  created_at: string;
  created_at_iso?: string;
  expires_at: string;
  approval_kind?: string;
  /** v2.3.1-p1: risk source information */
  argument_source?: string;
  argument_risk?: string;
  reason?: string;
  recommendation?: string;
}

export interface ApprovalSessionSnapshot {
  workspaceId: string;
  sessionId: string;
  pendingCount: number;
  continuations: ApprovalContinuationSummary[];
}

function approvalError(error: unknown): string {
  const value = error as { message?: string; error?: string; status?: number };
  const message = value?.message || value?.error || "审批请求失败，请稍后重试。";
  if (message === "approval_resolver_forbidden") return "当前身份没有审批权限，请由申请人或管理员处理。";
  if (value?.status === 401) return "登录已失效，请重新登录后处理审批。";
  if (message === "csrf_origin_denied") return "审批请求来源校验失败，请从当前服务地址重新打开页面。";
  if (message === "approval_expired") return "审批已过期，未执行配置。请重新发起任务。";
  if (value?.status === 404) return "该审批已被处理或不存在，正在同步会话状态。";
  return message;
}

/**
 * ApprovalBubble — small popup above the input bar for high-risk tool approval.
 *
 * A continuous, non-overlapping poll discovers pending approvals without reserving a page-lifetime
 * SSE worker. The backend-provided expires_at value is authoritative; the
 * browser never turns a display timer into an approval decision.
 */
export const ApprovalBubble = memo(function ApprovalBubble({ onResolved, onSessionUpdate }: {
  onResolved?: (decision: "approve" | "reject") => void;
  onSessionUpdate?: (snapshot: ApprovalSessionSnapshot) => void;
}) {
  const { currentSessionId, currentWorkspaceId } = useSessionStore();
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [resolving, setResolving] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const scope = `${currentWorkspaceId}:${currentSessionId}`;
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const pollRef = useRef<(() => Promise<void>) | null>(null);
  const onSessionUpdateRef = useRef(onSessionUpdate);
  onSessionUpdateRef.current = onSessionUpdate;
  const onResolvedRef = useRef(onResolved);
  onResolvedRef.current = onResolved;
  const mountedRef = useRef(true);
  const resolvingRef = useRef(false);
  const resolvedIdsRef = useRef<Map<string, number>>(new Map());
  const pollFailureCountRef = useRef(0);
  const pollErrorVisibleRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      resolvingRef.current = false;
    };
  }, []);

  // Session changes synchronously remove the prior session's approval.
  // The server independently enforces the same binding before it can resolve.
  useEffect(() => {
    resolvingRef.current = false;
    setResolving(false);
    setPending(null);
    setSecondsLeft(0);
    setErrorMessage("");
    pollFailureCountRef.current = 0;
    pollErrorVisibleRef.current = false;
  }, [currentSessionId, currentWorkspaceId]);

  // Approval discovery is intentionally HTTP polling. A persistent stream per
  // tab consumed scarce synchronous web workers even when no approval existed.
  useEffect(() => {
    if (!currentSessionId || !currentWorkspaceId) return;

    let cancelled = false;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let pollInFlight = false;

    const stopPoll = () => {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    };

    const startPoll = () => {
      if (!pollTimer) pollTimer = setInterval(() => { void poll(); }, 5000);
    };

    const poll = async () => {
      if (pollInFlight) return;
      pollInFlight = true;
      try {
        const now = Date.now();
        for (const [id, ts] of resolvedIdsRef.current) {
          if (now - ts > 120000) resolvedIdsRef.current.delete(id);
        }
        const data = await approvalApi.pending(currentSessionId, currentWorkspaceId);
        if (cancelled) return;
        pollFailureCountRef.current = 0;
        if (pollErrorVisibleRef.current) {
          pollErrorVisibleRef.current = false;
          setErrorMessage("");
        }
        if (data.ok) onSessionUpdateRef.current?.({
          workspaceId: currentWorkspaceId, sessionId: currentSessionId,
          pendingCount: data.pending?.length || 0, continuations: data.continuations || [],
        });
        if (data.ok && data.pending?.length > 0) {
          const p = (data.pending as unknown as PendingApproval[]).find((item) => (
            item.session_id === currentSessionId && !resolvedIdsRef.current.has(item.approval_id)
          ));
          if (!p) {
            if (!resolvingRef.current) {
              setPending(null);
              setSecondsLeft(0);
            }
            return;
          }
          const expiresAt = p.expires_at ? Date.parse(p.expires_at) : Date.now();
          const secs = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
          setPending(p);
          setSecondsLeft(secs);
          startPoll();
        } else if (!resolvingRef.current) {
          setPending(null);
          setSecondsLeft(0);
        }
      } catch (error) {
        const status = typeof error === "object" && error !== null && "status" in error
          ? Number((error as { status?: number }).status || 0)
          : 0;
        pollFailureCountRef.current += 1;
        if (status === 401) {
          stopPoll();
          pollErrorVisibleRef.current = true;
          setErrorMessage("登录已失效，审批状态监听已停止。请重新登录。");
        } else if (pollFailureCountRef.current >= 2) {
          pollErrorVisibleRef.current = true;
          setErrorMessage("审批状态同步失败，正在自动重连；请勿重复提交或批准操作。");
        }
      }
      finally { pollInFlight = false; }
    };

    // Poll continuously while the workbench session is open. Five seconds is
    // fast enough for a human approval gate and does not pin a server thread.
    void poll();
    startPoll();
    pollRef.current = poll;

    return () => {
      cancelled = true;
      stopPoll();
      pollRef.current = null;
    };
  }, [currentSessionId, currentWorkspaceId]);

  // Display-only countdown. Expiry and audit are server-owned.
  useEffect(() => {
    if (!pending) return;

    const tick = () => {
      const expiresAt = Date.parse(pending.expires_at || "");
      setSecondsLeft(Number.isFinite(expiresAt)
        ? Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000))
        : 0);
    };

    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [pending?.approval_id, pending?.expires_at]);

  const resolveApproval = useCallback(async (decision: "approve" | "reject") => {
    const p = pending;
    if (!p || p.session_id !== currentSessionId || resolvingRef.current) return;
    resolvingRef.current = true;
    setResolving(true);
    pollErrorVisibleRef.current = false;
    setErrorMessage("");
    try {
      const res = await approvalApi.resolve(p.approval_id, {
        decision,
        workspace_id: currentWorkspaceId,
        session_id: currentSessionId,
      });
      if (!mountedRef.current || scopeRef.current !== scope) return;
      if (!res.ok) {
        setErrorMessage(approvalError(res));
        return;
      }
      resolvedIdsRef.current.set(p.approval_id, Date.now());
      setPending(null);
      setSecondsLeft(0);
      if (res.runtime_result?.ok === false) {
        setErrorMessage(`审批已记录，但续跑未启动：${res.runtime_result.message || res.runtime_result.error || "请检查运行记录"}`);
      }
      onResolvedRef.current?.(decision);
      await pollRef.current?.();
    } catch (err) {
      if (!mountedRef.current || scopeRef.current !== scope) return;
      setErrorMessage(approvalError(err));
      // A lost response may follow a recorded decision. Reconcile via GET;
      // never replay an approval POST automatically.
      await pollRef.current?.();
    } finally {
      if (mountedRef.current && scopeRef.current === scope) {
        resolvingRef.current = false;
        setResolving(false);
      }
    }
  }, [pending, currentWorkspaceId, currentSessionId, scope]);

  if (!pending && !errorMessage) return null;
  if (!pending) return <div className="approval-bubble-popup"><div className="abp-inner"><p role="alert">{errorMessage}</p><button type="button" onClick={() => setErrorMessage("")}>关闭</button></div></div>;

  const isUrgent = secondsLeft <= 60;
  const countdown = secondsLeft >= 60
    ? `${Math.floor(secondsLeft / 60)}:${String(secondsLeft % 60).padStart(2, "0")}`
    : `${secondsLeft}s`;

  return (
    <div className="approval-bubble-popup" data-testid="approval-bubble">
      <div className="abp-inner">
        <div className="abp-header">
          <IconAlert size={14} />
          <span>高危操作</span>
          <span className={`abp-countdown ${isUrgent ? "urgent" : ""}`}>
            <IconClock size={11} />
            {countdown}
          </span>
        </div>

        <div className="abp-body">
          <code>{pending.tool_id}</code>
          {(pending.arguments_preview || pending.arguments_summary) && (
            <details className="abp-args"><summary>查看完整操作参数</summary><pre>
              {pending.arguments_preview
                ? JSON.stringify(pending.arguments_preview, null, 2)
                : pending.arguments_summary}
            </pre></details>
          )}
          {/* v2.3.1-p1: risk source info */}
          {(pending.argument_source || pending.recommendation) && (
            <div className="abp-risk-info">
              {pending.argument_source && (
                <span className="abp-risk-tag" data-source={pending.argument_source}>
                  来源: {pending.argument_source === "unknown" ? "❓ 未知" : pending.argument_source}
                </span>
              )}
              {pending.recommendation && (
                <span className="abp-risk-note">{pending.recommendation}</span>
              )}
            </div>
          )}
        </div>

        {errorMessage && <p role="alert">{errorMessage}</p>}

        <div className="abp-actions">
          <button
            className="btn sm ghost"
            onClick={() => resolveApproval("reject")}
            disabled={resolving}
            type="button"
          >
            <IconClose size={11} /> 拒绝
          </button>
          <button
            className="btn sm primary"
            onClick={() => resolveApproval("approve")}
            disabled={resolving}
            type="button"
          >
            <IconCheck size={11} /> {resolving ? "提交中…" : "允许"}
          </button>
        </div>
      </div>
    </div>
  );
});
