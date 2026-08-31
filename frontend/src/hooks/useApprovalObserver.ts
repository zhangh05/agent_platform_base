import { useCallback, useEffect, useRef, useState } from "react";
import { sessionsApi } from "../api";
import type { ApprovalSessionSnapshot } from "../components/ApprovalBubble";
import { useWorkbenchStore } from "../stores/workbench";

const activeStates = new Set(["pending", "ready", "claimed", "dispatching"]);
const labels: Record<string, string> = {
  pending: "等待审批，当前会话正在同步审批状态。",
  ready: "审批已通过，等待服务器续跑。",
  claimed: "审批已通过，服务器正在恢复任务。",
  dispatching: "审批已通过，任务正在继续执行，结果将自动更新。",
  completed: "审批后的任务已完成，会话结果已同步。",
  rejected: "审批已拒绝，本次获批操作未执行。",
  expired: "审批已过期，本次操作未执行。",
  stalled: "审批后的执行状态待核对，请勿重复提交配置。",
  failed: "审批后的任务未能完成，请查看执行详情。",
};

/** Observe durable approval state, including another tab and refresh recovery.
 * The shared approval poll supplies snapshots; no extra persistent transport or
 * fixed-duration timer is created. Message updates may reuse the same ID.
 */
export function useApprovalObserver(workspaceId: string | null, sessionId: string | null) {
  const scope = `${workspaceId}:${sessionId}`;
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const requestRef = useRef<AbortController | null>(null);
  const snapshotRef = useRef({ signature: "", settle: 0 });
  const [status, setStatus] = useState("");

  useEffect(() => {
    snapshotRef.current = { signature: "", settle: 0 };
    setStatus("");
    return () => { requestRef.current?.abort(); requestRef.current = null; };
  }, [scope]);

  const onSessionUpdate = useCallback((snapshot: ApprovalSessionSnapshot) => {
    if (!workspaceId || !sessionId || scopeRef.current !== scope
      || snapshot.workspaceId !== workspaceId || snapshot.sessionId !== sessionId) return;
    const records = snapshot.continuations;
    const current = records.find((record) => activeStates.has(record.status)) || records[0];
    const state = snapshot.pendingCount ? "pending" : current?.status || "";
    if (!state && !snapshotRef.current.signature) return;
    const signature = JSON.stringify([snapshot.pendingCount, records.map((record) => [record.continuation_id, record.status, record.updated_at])]);
    if (signature !== snapshotRef.current.signature) {
      snapshotRef.current = { signature, settle: 3 };
    }
    const active = activeStates.has(state);
    // Terminal publication and parent-message projection are distinct writes.
    // Re-read a few snapshots after terminal state instead of stopping before
    // the in-place parent response has been persisted.
    if (requestRef.current || (!active && snapshotRef.current.settle <= 0)) return;
    setStatus(state === "completed" ? "任务已执行完成，正在同步会话结果。" : labels[state] || "");
    const controller = new AbortController();
    requestRef.current = controller;
    void sessionsApi.messages(sessionId, workspaceId, controller.signal).then(async (response) => {
      if (controller.signal.aborted || scopeRef.current !== scope) return;
      useWorkbenchStore.getState().mergeFromBackend(sessionId, response.messages || []);
      if (current?.parent_run_id && (!active || snapshotRef.current.settle === 3)) {
        await useWorkbenchStore.getState().loadRunDetail(workspaceId, current.parent_run_id, sessionId, true);
        if (controller.signal.aborted || scopeRef.current !== scope) return;
      }
      snapshotRef.current.settle = Math.max(0, snapshotRef.current.settle - 1);
      setStatus(labels[state] || "");
    }).catch(() => {
      if (!controller.signal.aborted && scopeRef.current === scope) setStatus("会话状态同步失败，正在重连；请勿重复提交审批。");
    }).finally(() => {
      if (requestRef.current === controller) requestRef.current = null;
    });
  }, [scope, sessionId, workspaceId]);

  return { approvalStatus: status, onSessionUpdate };
}
