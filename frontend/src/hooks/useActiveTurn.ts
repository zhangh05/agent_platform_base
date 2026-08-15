import { useCallback, useEffect, useRef, useState } from "react";
import { jobsApi } from "../api";
import type { ActiveTurnSnapshot, JobItem } from "../types";

type JobBroadcast = {
  type?: string;
  name?: string;
  data?: {
    job_id?: string;
    workspace_id?: string;
    session_id?: string;
    status?: string;
    title?: string;
    progress?: JobItem["progress"];
    active_turn?: ActiveTurnSnapshot;
  };
};

function belongsToSession(job: JobItem, sessionId: string): boolean {
  return job.job_type === "agent_run"
    && String(job.payload?.session_id || job.metadata?.active_turn?.session_id || "") === sessionId;
}

/**
 * Keeps the current session's durable runtime snapshot in sync.
 *
 * WebSocket broadcasts provide immediate updates. A low-frequency fetch is
 * retained only while a job is running so refreshes and missed broadcasts
 * recover without replaying the user request.
 */
export function useActiveTurn(workspaceId: string | null, sessionId: string | null) {
  const [job, setJob] = useState<JobItem | null>(null);
  const mountedRef = useRef(true);
  // A boolean mounted flag cannot distinguish a prior session request from
  // the current session after React has mounted the next effect.
  const refreshEpochRef = useRef(0);

  const refresh = useCallback(async () => {
    const refreshEpoch = ++refreshEpochRef.current;
    if (!workspaceId || !sessionId) {
      setJob(null);
      return null;
    }
    try {
      const response = await jobsApi.list(workspaceId);
      const match = (response.jobs || []).find((item) => belongsToSession(item, sessionId)) || null;
      if (mountedRef.current && refreshEpoch === refreshEpochRef.current) {
        setJob(match);
      }
      return match;
    } catch {
      return null;
    }
  }, [sessionId, workspaceId]);

  useEffect(() => {
    mountedRef.current = true;
    void refresh();
    return () => {
      mountedRef.current = false;
      refreshEpochRef.current += 1;
    };
  }, [refresh]);

  useEffect(() => {
    if (!workspaceId || !sessionId) return;
    const onWsEvent = (event: Event) => {
      const detail = (event as CustomEvent<JobBroadcast>).detail;
      if (detail?.name !== "job_updated") return;
      const data = detail.data || {};
      if (data.workspace_id !== workspaceId || data.session_id !== sessionId) return;
      setJob((current) => ({
        ...(current || {
          job_id: String(data.job_id || ""),
          job_type: "agent_run",
          title: String(data.title || ""),
          status: String(data.status || "running"),
          workspace_id: workspaceId,
          created_at: new Date().toISOString(),
          payload: { session_id: sessionId },
        }),
        job_id: String(data.job_id || current?.job_id || ""),
        status: String(data.status || current?.status || "running"),
        progress: data.progress ?? current?.progress,
        metadata: {
          ...(current?.metadata || {}),
          active_turn: data.active_turn || current?.metadata?.active_turn,
        },
      }));
    };
    window.addEventListener("ws-event", onWsEvent);
    return () => window.removeEventListener("ws-event", onWsEvent);
  }, [sessionId, workspaceId]);

  useEffect(() => {
    if (job?.status !== "running") return;
    const timer = window.setInterval(() => { void refresh(); }, 2500);
    return () => window.clearInterval(timer);
  }, [job?.status, refresh]);

  return { job, refresh };
}
