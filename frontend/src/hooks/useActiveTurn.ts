import { useCallback, useEffect, useRef, useState } from "react";
import { jobsApi } from "../api";
import type { JobItem } from "../types";

function belongsToSession(job: JobItem, sessionId: string): boolean {
  return job.job_type === "agent_run"
    && String(job.payload?.session_id || job.metadata?.active_turn?.session_id || "") === sessionId;
}

/**
 * Keeps the current session's durable runtime snapshot in sync.
 *
 * Durable job snapshots are authoritative. Polling is active only while a turn
 * is running, which keeps refresh recovery reliable without holding a second
 * page-lifetime WebSocket or SSE connection per browser tab.
 */
export function useActiveTurn(workspaceId: string | null, sessionId: string | null) {
  const [job, setJob] = useState<JobItem | null>(null);
  const [loaded, setLoaded] = useState(false);
  const mountedRef = useRef(true);
  // A boolean mounted flag cannot distinguish a prior session request from
  // the current session after React has mounted the next effect.
  const refreshEpochRef = useRef(0);

  const refresh = useCallback(async () => {
    const refreshEpoch = ++refreshEpochRef.current;
    if (!workspaceId || !sessionId) {
      setJob(null);
      setLoaded(false);
      return null;
    }
    try {
      const response = await jobsApi.list(workspaceId);
      const match = (response.jobs || []).find((item) => belongsToSession(item, sessionId)) || null;
      if (mountedRef.current && refreshEpoch === refreshEpochRef.current) {
        setJob(match);
        setLoaded(true);
      }
      return match;
    } catch {
      // A failed observation must not convert an optimistic streaming message
      // into an error. Only a successful durable-job snapshot is authoritative.
      if (mountedRef.current && refreshEpoch === refreshEpochRef.current) {
        setLoaded(false);
      }
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
    if (job?.status !== "running") return;
    const timer = window.setInterval(() => { void refresh(); }, 2500);
    return () => window.clearInterval(timer);
  }, [job?.status, refresh]);

  return { job, loaded, refresh };
}
