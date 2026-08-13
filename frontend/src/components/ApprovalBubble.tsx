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
  expires_at: string;
  approval_kind?: string;
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
 * fallback. The backend-provided expires_at value is authoritative; the
 * browser never turns a display timer into an approval decision.
 */
export function ApprovalBubble({ onResolved }: { onResolved?: (decision: "approve" | "reject") => void }) {
  const { currentSessionId, currentWorkspaceId } = useSessionStore();
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [resolving, setResolving] = useState(false);
  const onResolvedRef = useRef(onResolved);
  onResolvedRef.current = onResolved;
  const mountedRef = useRef(true);
  const resolvingRef = useRef(false);
  const resolvedIdsRef = useRef<Map<string, number>>(new Map());

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      resolvingRef.current = false;
    };
  }, []);

  // SSE gives immediate invalidation; low-frequency polling survives disconnects.
  useEffect(() => {
    if (!currentSessionId || !currentWorkspaceId) return;

    let cancelled = false;
    let es: EventSource | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let pollInFlight = false;
    let authorized = false;

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
        authorized = true;
        if (data.ok && data.pending?.length > 0) {
          const p = (data.pending as unknown as PendingApproval[]).find((item) => !resolvedIdsRef.current.has(item.approval_id));
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
        if (status === 401) stopPoll();
      }
      finally { pollInFlight = false; }
    };

    // Initial check. Healthy SSE events trigger later checks; polling is only
    // a fallback while an approval is active or the stream is disconnected.
    void poll();
    try {
      es = openApprovalStream(currentWorkspaceId, (event) => {
        if (!resolvingRef.current && event.session_id === currentSessionId && event.workspace_id === currentWorkspaceId) {
          void poll();
        }
      }, () => { if (authorized) startPoll(); });
    } catch {
      es = null;
    }

    return () => {
      cancelled = true;
      stopPoll();
      es?.close();
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
    if (!p || resolvingRef.current) return;
    resolvingRef.current = true;
    setResolving(true);
    try {
      const res = await approvalApi.resolve(p.approval_id, { decision, workspace_id: currentWorkspaceId });
      if (!res.ok) {
        console.warn("[Approval] resolve returned not ok:", res);
        // Keep showing the bubble so user can retry
        resolvingRef.current = false;
        setResolving(false);
        return;
      }
      resolvedIdsRef.current.set(p.approval_id, Date.now());
      setPending(null);
      setSecondsLeft(0);
      onResolvedRef.current?.(decision);
    } catch (err) {
      console.error("[Approval] resolve failed:", err);
      // Keep bubble visible so user can retry
    } finally {
      resolvingRef.current = false;
      if (mountedRef.current) setResolving(false);
    }
  }, [pending, currentWorkspaceId]);

  if (!pending) return null;

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
