import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useChatStream } from "../hooks/useChatStream";
import { useWorkbenchStore } from "../stores/workbench";
import { writeInflightChatStream, readInflightChatStream } from "../utils/chatStreamRecovery";
import { enqueue, installMockApi, resetMocks } from "./mockServer";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: Array<Record<string, unknown>> = [];
  autoComplete = true;

  constructor(_url: string) {
    FakeWebSocket.instances.push(this);
  }

  open() { this.onopen?.(); }

  send(raw: string) {
    const frame = JSON.parse(raw) as Record<string, unknown>;
    this.sent.push(frame);
    if (frame.type !== "resume") return;
    this.onmessage?.({ data: JSON.stringify({ type: "accepted", stream_id: frame.stream_id, resumed: true }) } as MessageEvent);
    if (!this.autoComplete) return;
    this.onmessage?.({ data: JSON.stringify({
      type: "done",
      stream_id: frame.stream_id,
      stream_seq: 4,
      session_id: "session-1",
      turn_id: "turn-1",
      trace_id: "trace-1",
      final_response: "恢复完成",
      events: [],
      tool_calls: [],
      warnings: [],
      errors: [],
      metadata: {},
    }) } as MessageEvent);
  }

  close() { this.onclose?.(); }
}

describe("chat stream refresh recovery", () => {
  beforeEach(() => {
    localStorage.clear();
    resetMocks();
    installMockApi();
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    useWorkbenchStore.getState().resetForUser();
    useWorkbenchStore.getState().switchSession("session-1");
  });

  it("resumes the persisted stream and applies its terminal result", async () => {
    const messageId = useWorkbenchStore.getState().appendAssistantStreaming("session-1");
    writeInflightChatStream({
      streamId: "11111111-1111-4111-8111-111111111111",
      workspaceId: "default",
      sessionId: "session-1",
      scratchSessionId: "session-1",
      messageId,
      startedAt: new Date().toISOString(),
      lastSeq: 3,
    });

    renderHook(() => useChatStream(
      { workspaceId: "default", sessionId: "session-1", llmHealth: {} },
      { onSessionResolved: vi.fn() },
    ));

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    await act(async () => FakeWebSocket.instances[0].open());
    await waitFor(() => {
      const message = useWorkbenchStore.getState().bySession["session-1"].find((item) => item.id === messageId);
      expect(message?.status).toBe("ready");
      expect(message?.text).toBe("恢复完成");
    });
    expect(FakeWebSocket.instances[0].sent[0]).toMatchObject({
      type: "resume",
      stream_id: "11111111-1111-4111-8111-111111111111",
      after_seq: 3,
    });
    expect(readInflightChatStream()).toBeNull();
  });

  it("keeps recovery metadata when the page detaches", async () => {
    const messageId = useWorkbenchStore.getState().appendAssistantStreaming("session-1");
    const record = {
      streamId: "22222222-2222-4222-8222-222222222222",
      workspaceId: "default",
      sessionId: "session-1",
      scratchSessionId: "session-1",
      messageId,
      startedAt: new Date().toISOString(),
      lastSeq: 0,
    };
    writeInflightChatStream(record);
    const rendered = renderHook(() => useChatStream(
      { workspaceId: "default", sessionId: "session-1", llmHealth: {} },
      { onSessionResolved: vi.fn() },
    ));
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    FakeWebSocket.instances[0].autoComplete = false;
    await act(async () => FakeWebSocket.instances[0].open());
    rendered.unmount();
    expect(readInflightChatStream()).toEqual(record);
  });

  it("uses an already-persisted final answer before opening a resume socket", async () => {
    const startedAt = new Date().toISOString();
    writeInflightChatStream({
      streamId: "33333333-3333-4333-8333-333333333333",
      workspaceId: "default",
      sessionId: "session-1",
      scratchSessionId: "session-1",
      messageId: "stale-placeholder",
      startedAt,
      lastSeq: 0,
    });
    enqueue("/sessions/session-1/messages", {
      status: 200,
      data: {
        ok: true,
        count: 2,
        messages: [
          {
            message_id: "turn-done:user",
            session_id: "session-1",
            role: "user",
            content: "分析该配置",
            created_at: startedAt,
            run_id: "turn-done",
          },
          {
            message_id: "turn-done:assistant",
            session_id: "session-1",
            role: "assistant",
            content: "配置分析结果",
            created_at: startedAt,
            run_id: "turn-done",
          },
        ],
      },
    });

    renderHook(() => useChatStream(
      { workspaceId: "default", sessionId: "session-1", llmHealth: {} },
      { onSessionResolved: vi.fn() },
    ));

    await waitFor(() => expect(readInflightChatStream()).toBeNull());
    expect(FakeWebSocket.instances).toHaveLength(0);
    expect(useWorkbenchStore.getState().bySession["session-1"].at(-1)).toMatchObject({
      status: "ready",
      text: "配置分析结果",
      run_id: "turn-done",
    });
  });
});
