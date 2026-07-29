import { useEffect, useState, useRef, useCallback } from "react";
import { useSessionStore } from "../stores/session";
import { approvalApi, openApprovalStream } from "../api";
import { IconAlert, IconCheck, IconClose, IconClock } from "./Icon";
import "./ApprovalBubble.css";

interface PendingApproval {
  approval_id: string;
  tool_id: string;
  description?: string;
  risk_level: string;
  arguments_preview?: Record<string, unknown>;
  arguments_summary?: string;
  created_at: string;
  created_at_iso?: string;
  /** v2.3.1-p1: risk source information */
  argument_source?: string;
  argument_risk?: string;
  reason?: string;
  recommendation?: string;
}

/**
 * ApprovalBubble — small popup above the input bar for high-risk tool approval.
 *
 * SSE triggers immediate refreshes; a 5s poll remains as a disconnect-safe
 * fallback. refs hold mutable approval state across re-renders.
 * Auto-denies after 60s.
 */
export function ApprovalBubble({ onResolved }: { onResolved?: (decision: "approve" | "reject") => void }) {
  const { currentSessionId, currentWorkspaceId } = useSessionStore();
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(60);
  const onResolvedRef = useRef(onResolved);
  onResolvedRef.current = onResolved;
  const mountedRef = useRef(true);
  const resolvingRef = useRef(false);
  const resolvedIdsRef = useRef<Map<string, number>>(new Map());

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      setPending(null);
      resolvingRef.current = false;
      setSecondsLeft(60);
    };
  }, []);

  // SSE gives immediate invalidation; low-frequency polling survives disconnects.
  useEffect(() => {
    if (!currentSessionId || !currentWorkspaceId) return;

    let cancelled = false;
    let es: EventSource | null = null;
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
        if (data.ok && data.pending?.length > 0) {
          const p = (data.pending as unknown as PendingApproval[]).find((item) => !resolvedIdsRef.current.has(item.approval_id));
          if (!p) {
            if (!resolvingRef.current) {
              setPending(null);
              setSecondsLeft(60);
            }
            return;
          }
          // created_at is ISO-8601 string (v3.9.8+). Date.parse handles both.
          const created = p.created_at ? Date.parse(p.created_at) : Date.now();
          const elapsed = (Date.now() - created) / 1000;
          const secs = Math.max(0, Math.ceil(60 - elapsed));
          if (secs <= 0 || elapsed > 120) {
            try { await approvalApi.resolve(p.approval_id, { decision: "reject", workspace_id: currentWorkspaceId }); } catch { /* ignore */ }
            if (!resolvingRef.current) { setPending(null); setSecondsLeft(60); }
            return;
          }
          setPending(p);
          setSecondsLeft(secs);
          startPoll();
        } else if (!resolvingRef.current) {
          setPending(null);
          setSecondsLeft(60);
        }
      } catch { /* ignore */ }
      finally { pollInFlight = false; }
    };

    // Initial poll: only continue polling if a pending approval is found
    poll();
    startPoll();
    try {
      es = openApprovalStream(currentWorkspaceId, (event) => {
        if (!resolvingRef.current && event.session_id === currentSessionId && event.workspace_id === currentWorkspaceId) {
          void poll();
        }
      });
    } catch {
      es = null;
    }

    return () => {
      cancelled = true;
      stopPoll();
      es?.close();
    };
  }, [currentSessionId, currentWorkspaceId]);

  // Countdown timer — uses state for re-renders
  useEffect(() => {
    if (!pending) return;

    const tick = () => {
      setSecondsLeft((prev) => {
        const next = prev - 1;
        if (next <= 0) {
          if (!resolvingRef.current) resolveApprovalRef.current("reject");
          return 0;
        }
        return next;
      });
    };

    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [pending?.approval_id]);

  const resolveApproval = useCallback(async (decision: "approve" | "reject") => {
    const p = pending;
    if (!p || resolvingRef.current) return;
    resolvingRef.current = true;
    try {
      const res = await approvalApi.resolve(p.approval_id, { decision, workspace_id: currentWorkspaceId });
      if (!res.ok) {
        console.warn("[Approval] resolve returned not ok:", res);
        // Keep showing the bubble so user can retry
        resolvingRef.current = false;
        return;
      }
      resolvedIdsRef.current.set(p.approval_id, Date.now());
      setPending(null);
      setSecondsLeft(60);
      onResolvedRef.current?.(decision);
    } catch (err) {
      console.error("[Approval] resolve failed:", err);
      // Keep bubble visible so user can retry
    } finally {
      resolvingRef.current = false;
    }
  }, [pending, currentWorkspaceId]);

  const resolveApprovalRef = useRef(resolveApproval);
  resolveApprovalRef.current = resolveApproval;

  const resolving = resolvingRef.current;

  if (!pending) return null;

  const isUrgent = secondsLeft <= 10;

  return (
    <div className="approval-bubble-popup" data-testid="approval-bubble">
      <div className="abp-inner">
        <div className="abp-header">
          <IconAlert size={14} />
          <span>高危操作</span>
          <span className={`abp-countdown ${isUrgent ? "urgent" : ""}`}>
            <IconClock size={11} />
            {secondsLeft}s
          </span>
        </div>

        <div className="abp-body">
          <code>{pending.tool_id}</code>
          {(pending.arguments_preview || pending.arguments_summary) && (
            <span className="abp-args">
              {pending.arguments_preview
                ? JSON.stringify(pending.arguments_preview).substring(0, 80)
                : pending.arguments_summary?.substring(0, 80)}
            </span>
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
            autoFocus
          >
            <IconCheck size={11} /> 允许
          </button>
        </div>
      </div>
    </div>
  );
}
