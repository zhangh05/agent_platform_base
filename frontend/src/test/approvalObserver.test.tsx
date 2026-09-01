import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { sessionsApi } from "../api";
import { useApprovalObserver } from "../hooks/useApprovalObserver";
import { useWorkbenchStore } from "../stores/workbench";
import type { ApprovalSessionSnapshot } from "../components/ApprovalBubble";

const sseListeners = new Map<string, EventListenerOrEventListenerObject>();
vi.mock("../api/sse", () => ({
  openSSE: vi.fn(() => ({
    onmessage: null,
    onerror: null,
    addEventListener: (type: string, listener: EventListenerOrEventListenerObject) => { sseListeners.set(type, listener); },
    removeEventListener: (type: string) => { sseListeners.delete(type); },
    close: vi.fn(),
  })),
}));

function snapshot(status: string): ApprovalSessionSnapshot {
  return { workspaceId: "default", sessionId: "s1", pendingCount: status === "pending" ? 1 : 0, continuations: [{ continuation_id: "c1", workspace_id: "default", session_id: "s1", parent_run_id: "r1", status, created_at: "", updated_at: status, approval_count: 1, decision_count: status === "pending" ? 0 : 1 }] };
}

function emitSSE(type: string, payload: Record<string, unknown>) {
  const listener = sseListeners.get(type);
  if (!listener) throw new Error(`missing SSE listener: ${type}`);
  const event = new MessageEvent(type, { data: JSON.stringify(payload) });
  if (typeof listener === "function") listener(event);
  else listener.handleEvent(event);
}

beforeEach(() => {
  sseListeners.clear();
  useWorkbenchStore.getState().resetForUser();
  vi.spyOn(useWorkbenchStore.getState(), "loadRunDetail").mockResolvedValue(null);
});

it("keeps observing beyond 30 snapshots and updates an existing message ID", async () => {
  const messages = vi.spyOn(sessionsApi, "messages").mockResolvedValue({ ok: true, count: 1, messages: [{ message_id: "r1:assistant", run_id: "r1", role: "assistant", content: "等待审批", created_at: "2026-09-01T00:00:00Z" }] });
  const { result } = renderHook(() => useApprovalObserver("default", "s1"));
  await act(async () => { result.current.onSessionUpdate(snapshot("pending")); });
  for (let i = 0; i < 40; i++) {
    await act(async () => { result.current.onSessionUpdate(snapshot("dispatching")); });
  }
  expect(messages).toHaveBeenCalledTimes(41);
  expect(result.current.approvalStatus).toContain("正在继续执行");
  messages.mockResolvedValue({ ok: true, count: 1, messages: [{ message_id: "r1:assistant", run_id: "r1", role: "assistant", content: "配置完成，已核对", created_at: "2026-09-01T00:00:00Z" }] });
  await act(async () => { result.current.onSessionUpdate(snapshot("completed")); });
  expect(useWorkbenchStore.getState().bySession.s1).toHaveLength(1);
  expect(useWorkbenchStore.getState().bySession.s1[0].text).toBe("配置完成，已核对");
  expect(useWorkbenchStore.getState().loadRunDetail).toHaveBeenLastCalledWith("default", "r1", "s1", true);
  expect(result.current.approvalStatus).toContain("已完成");
});

it("restores ongoing continuation monitoring without a local click", async () => {
  vi.spyOn(sessionsApi, "messages").mockResolvedValue({ ok: true, count: 0, messages: [] });
  const { result } = renderHook(() => useApprovalObserver("default", "s1"));
  await act(async () => { result.current.onSessionUpdate(snapshot("dispatching")); });
  expect(result.current.approvalStatus).toContain("正在继续执行");
  await act(async () => { result.current.onSessionUpdate(snapshot("rejected")); });
  expect(result.current.approvalStatus).toContain("已拒绝");
});

it("projects approved continuation model and tool events into the parent message", async () => {
  vi.spyOn(sessionsApi, "messages").mockResolvedValue({
    ok: true,
    count: 1,
    messages: [{
      message_id: "r1:assistant",
      run_id: "r1",
      role: "assistant",
      content: "该操作正在等待审批。",
      created_at: "2026-09-01T00:00:00Z",
    }],
  });
  const { result } = renderHook(() => useApprovalObserver("default", "s1"));
  await act(async () => { result.current.onSessionUpdate(snapshot("pending")); });

  act(() => {
    emitSSE("continuation_started", { continuation_id: "c1", parent_run_id: "r1" });
    emitSSE("continuation_runtime_event", {
      continuation_id: "c1",
      parent_run_id: "r1",
      name: "tool_call",
      data: { type: "tool_call", tool_id: "network.operations.device.manage", call_id: "call-1" },
    });
    emitSSE("continuation_token", {
      continuation_id: "c1",
      parent_run_id: "r1",
      content: "配置已执行，",
    });
    emitSSE("continuation_token", {
      continuation_id: "c1",
      parent_run_id: "r1",
      content: "正在核验。",
    });
  });

  const message = useWorkbenchStore.getState().bySession.s1.find((item) => item.run_id === "r1");
  expect(message?.status).toBe("streaming");
  expect(message?.text).toBe("配置已执行，正在核验。");
  expect(message?.toolCalls?.[0]).toMatchObject({
    call_id: "call-1",
    tool_id: "network.operations.device.manage",
    status: "running",
  });
});

it("does not overlap hydration and discards stale results after switching session", async () => {
  let finish!: (data: Awaited<ReturnType<typeof sessionsApi.messages>>) => void;
  const messages = vi.spyOn(sessionsApi, "messages").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const { result, rerender } = renderHook(({ sid }) => useApprovalObserver("default", sid), { initialProps: { sid: "s1" } });
  act(() => { result.current.onSessionUpdate(snapshot("dispatching")); result.current.onSessionUpdate(snapshot("dispatching")); });
  expect(messages).toHaveBeenCalledTimes(1);
  rerender({ sid: "s2" });
  await act(async () => { finish({ ok: true, count: 1, messages: [{ message_id: "r1:assistant", role: "assistant", content: "旧会话结果", created_at: "" }] }); });
  expect(useWorkbenchStore.getState().bySession.s2).toBeUndefined();
  expect(result.current.approvalStatus).toBe("");
});
