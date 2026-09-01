import { useCallback, useEffect, useRef, useState } from "react";
import { sessionsApi } from "../api";
import { openSSE, type SSEConnection } from "../api/sse";
import type { ApprovalSessionSnapshot } from "../components/ApprovalBubble";
import { useWorkbenchStore, type ChatMsg } from "../stores/workbench";
import type { InlineToolCall, RuntimeEvent } from "../types";
import { filterStreamingThink, toolLabel, type ThinkFilterState } from "../utils/displayText";
import { progressPatchForStreamStage } from "../utils/streamStage";

const activeStates = new Set(["pending", "ready", "claimed", "dispatching"]);
type ParentMessagePatch = Partial<Pick<ChatMsg,
  "status" | "text" | "toolCalls" | "progressText" | "runtimeEvents"
>>;
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
  const streamRef = useRef<SSEConnection | null>(null);
  const draftRef = useRef("");
  const thinkFilterRef = useRef<{ mode: ThinkFilterState }>({ mode: "idle" });
  const snapshotRef = useRef({ signature: "", settle: 0 });
  const [status, setStatus] = useState("");
  const [activeContinuation, setActiveContinuation] = useState<{
    continuationId: string;
    parentRunId: string;
  } | null>(null);

  useEffect(() => {
    snapshotRef.current = { signature: "", settle: 0 };
    streamRef.current?.close();
    streamRef.current = null;
    draftRef.current = "";
    thinkFilterRef.current = { mode: "idle" };
    setActiveContinuation(null);
    setStatus("");
    return () => {
      requestRef.current?.abort(); requestRef.current = null;
      streamRef.current?.close(); streamRef.current = null;
    };
  }, [scope]);

  useEffect(() => {
    if (!workspaceId || !sessionId || !activeContinuation) return;
    const connection = openSSE(
      `/agent/sse/stream/${encodeURIComponent(sessionId)}?workspace_id=${encodeURIComponent(workspaceId)}`,
    );
    streamRef.current?.close();
    streamRef.current = connection;

    const findParentMessage = () => {
      const messages = useWorkbenchStore.getState().bySession[sessionId] || [];
      return [...messages].reverse().find((item) => (
        item.role === "assistant" && item.run_id === activeContinuation.parentRunId
      ));
    };
    const patchParent = (patch: ParentMessagePatch) => {
      const message = findParentMessage();
      if (!message) return;
      useWorkbenchStore.getState().updateAssistant(message.id, patch, sessionId);
    };
    const parse = (event: Event) => {
      try { return JSON.parse((event as MessageEvent<string>).data || "{}"); }
      catch { return {}; }
    };
    const matches = (payload: Record<string, unknown>) => (
      String(payload.continuation_id || "") === activeContinuation.continuationId
      && String(payload.parent_run_id || "") === activeContinuation.parentRunId
    );
    const onStarted = (event: Event) => {
      const payload = parse(event);
      if (!matches(payload)) return;
      draftRef.current = "";
      thinkFilterRef.current = { mode: "idle" };
      patchParent({ status: "streaming", text: "", progressText: "审批已通过，正在恢复任务…" });
    };
    const onToken = (event: Event) => {
      const payload = parse(event);
      if (!matches(payload)) return;
      const visible = filterStreamingThink(String(payload.content || ""), thinkFilterRef.current);
      if (!visible) return;
      draftRef.current += visible;
      patchParent({ status: "streaming", text: draftRef.current, progressText: "正在生成回复…" });
    };
    const onRuntimeEvent = (event: Event) => {
      const payload = parse(event);
      if (!matches(payload)) return;
      const name = String(payload.name || "event");
      const data = payload.data && typeof payload.data === "object"
        ? payload.data as Record<string, unknown>
        : {};
      const message = findParentMessage();
      if (!message) return;
      const runtimeEvent: RuntimeEvent = {
        ...data,
        event_id: String(data.event_id || `continuation-${activeContinuation.continuationId}-${Date.now()}`),
        event_type: String(data.event_type || data.type || name),
        type: String(data.type || name),
      } as RuntimeEvent;
      const patch: ParentMessagePatch = {
        status: "streaming",
        runtimeEvents: [...(message.runtimeEvents || []), runtimeEvent],
      };
      const progress = progressPatchForStreamStage(name, data);
      if (progress) patch.progressText = progress.progressText;
      if (name === "model_started") {
        draftRef.current = "";
        thinkFilterRef.current = { mode: "idle" };
        patch.text = "";
      }
      if (name === "tool_call" || name === "tool_result") {
        const toolId = String(data.tool_id || data.name || "");
        const callId = String(data.call_id || data.node_id || toolId || "");
        const previous = (message.toolCalls || []) as InlineToolCall[];
        if (toolId && callId && name === "tool_call" && !previous.some((item) => item.call_id === callId)) {
          patch.toolCalls = [...previous, {
            call_id: callId,
            tool_id: toolId,
            tool_name: toolLabel(toolId),
            ok: false,
            status: "running",
          }];
        } else if (callId && name === "tool_result") {
          const ok = data.ok ?? data.status === "ok";
          patch.toolCalls = previous.map((item) => item.call_id === callId
            ? { ...item, status: ok ? "done" : "fail", ok: Boolean(ok), summary: String(data.summary || "") }
            : item);
        }
      }
      useWorkbenchStore.getState().updateAssistant(message.id, patch, sessionId);
    };
    const syncTerminal = (event: Event) => {
      const payload = parse(event);
      if (!matches(payload)) return;
      patchParent({ progressText: "任务已结束，正在同步最终结果…" });
      void sessionsApi.messages(sessionId, workspaceId).then(async (response) => {
        useWorkbenchStore.getState().mergeFromBackend(sessionId, response.messages || []);
        await useWorkbenchStore.getState().loadRunDetail(
          workspaceId, activeContinuation.parentRunId, sessionId, true,
        );
      }).catch(() => { /* durable approval polling retries synchronization */ });
    };
    connection.addEventListener("continuation_started", onStarted);
    connection.addEventListener("continuation_token", onToken);
    connection.addEventListener("continuation_runtime_event", onRuntimeEvent);
    connection.addEventListener("continuation_completed", syncTerminal);
    connection.addEventListener("continuation_failed", syncTerminal);
    return () => {
      connection.removeEventListener("continuation_started", onStarted);
      connection.removeEventListener("continuation_token", onToken);
      connection.removeEventListener("continuation_runtime_event", onRuntimeEvent);
      connection.removeEventListener("continuation_completed", syncTerminal);
      connection.removeEventListener("continuation_failed", syncTerminal);
      connection.close();
      if (streamRef.current === connection) streamRef.current = null;
    };
  }, [activeContinuation, sessionId, workspaceId]);

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
    if (current && active && current.continuation_id && current.parent_run_id) {
      setActiveContinuation((existing) => (
        existing?.continuationId === current.continuation_id
          && existing.parentRunId === current.parent_run_id
          ? existing
          : { continuationId: current.continuation_id, parentRunId: current.parent_run_id }
      ));
    } else if (!active) {
      setActiveContinuation(null);
    }
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
