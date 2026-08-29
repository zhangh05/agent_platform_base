import { beforeEach, describe, expect, it, vi } from "vitest";
import { useWorkbenchStore } from "../stores/workbench";

describe("workbench backend message merge", () => {
  beforeEach(() => {
    localStorage.clear();
    useWorkbenchStore.setState({
      bySession: {},
      currentSessionId: null,
      runDetails: {},
      runDetailLoading: {},
      runDetailError: {},
      sending: false,
      lastUserInput: "",
    });
  });

  it("deduplicates repeated backend user messages and preserves assistant content", () => {
    const store = useWorkbenchStore.getState();
    store.switchSession("sess-merge");
    store.mergeFromBackend("sess-merge", [
      {
        message_id: "run-1:user",
        session_id: "sess-merge",
        role: "user",
        content: "查看明天上海天气",
        created_at: "2026-06-28T10:00:00Z",
        run_id: "run-1",
      },
      {
        message_id: "run-1:user",
        session_id: "sess-merge",
        role: "user",
        content: "查看明天上海天气",
        created_at: "2026-06-28T10:00:00Z",
        run_id: "run-1",
      },
      {
        message_id: "run-1:assistant",
        session_id: "sess-merge",
        role: "assistant",
        content: "明天上海天气：多云。",
        created_at: "2026-06-28T10:00:01Z",
        run_id: "run-1",
      },
    ]);

    const messages = useWorkbenchStore.getState().bySession["sess-merge"];
    expect(messages.map((m) => `${m.role}:${m.text}`)).toEqual([
      "user:查看明天上海天气",
      "assistant:明天上海天气：多云。",
    ]);
  });

  it("restores the immutable Skill label on a persisted user message", () => {
    const store = useWorkbenchStore.getState();
    store.switchSession("sess-skill");
    store.mergeFromBackend("sess-skill", [{
      message_id: "run-skill:user",
      session_id: "sess-skill",
      role: "user",
      content: "连接设备，并查看版本",
      created_at: "2026-08-29T10:00:00Z",
      run_id: "run-skill",
      metadata: { workbench_skill: { skill_id: "skill-1", name: "测试1" } },
    }]);

    expect(useWorkbenchStore.getState().bySession["sess-skill"][0].skill).toEqual({
      skill_id: "skill-1",
      name: "测试1",
    });
  });

  it("keeps backend turn ordering when replacing optimistic local messages", () => {
    const store = useWorkbenchStore.getState();
    store.switchSession("sess-order");
    store.appendUser("你好，查看明天杭州天气", "sess-order");
    const assistantId = store.appendAssistantStreaming("sess-order");
    useWorkbenchStore.getState().updateAssistant(assistantId, {
      status: "ready",
      text: "明天杭州天气：小雨。",
    }, "sess-order");

    useWorkbenchStore.setState((state) => ({
      bySession: {
        ...state.bySession,
        "sess-order": state.bySession["sess-order"].map((m) =>
          m.role === "user"
            ? { ...m, created_at: "2026-06-28T10:00:05Z" }
            : { ...m, created_at: "2026-06-28T10:00:06Z" },
        ),
      },
    }));

    store.mergeFromBackend("sess-order", [
      {
        message_id: "run-weather:user",
        session_id: "sess-order",
        role: "user",
        content: "你好，查看明天杭州天气",
        created_at: "2026-06-28T10:00:00Z",
        run_id: "run-weather",
      },
      {
        message_id: "run-weather:assistant",
        session_id: "sess-order",
        role: "assistant",
        content: "明天杭州天气：小雨。",
        created_at: "2026-06-28T10:00:01Z",
        run_id: "run-weather",
      },
    ]);

    const messages = useWorkbenchStore.getState().bySession["sess-order"];
    expect(messages.map((m) => `${m.role}:${m.text}`)).toEqual([
      "user:你好，查看明天杭州天气",
      "assistant:明天杭州天气：小雨。",
    ]);
    expect(messages.map((m) => m.created_at)).toEqual([
      "2026-06-28T10:00:00Z",
      "2026-06-28T10:00:01Z",
    ]);
  });

  it("collapses duplicate local users and replaces pending assistant with backend answer", () => {
    const store = useWorkbenchStore.getState();
    store.switchSession("sess-live");
    store.appendUser("派发子agent，让它搜索一下BGP邻居的建立条件", "sess-live");
    store.appendUser("派发子agent，让它搜索一下BGP邻居的建立条件", "sess-live");
    store.appendAssistantStreaming("sess-live");

    store.mergeFromBackend("sess-live", [
      {
        message_id: "run-sub:user",
        session_id: "sess-live",
        role: "user",
        content: "派发子agent，让它搜索一下BGP邻居的建立条件",
        created_at: "2026-06-28T10:00:00Z",
        run_id: "run-sub",
      },
      {
        message_id: "run-sub:assistant",
        session_id: "sess-live",
        role: "assistant",
        content: "子 agent 已完成搜索，BGP 邻居建立条件如下。",
        created_at: "2026-06-28T10:00:01Z",
        run_id: "run-sub",
      },
    ]);

    const messages = useWorkbenchStore.getState().bySession["sess-live"];
    expect(messages.map((m) => `${m.role}:${m.text}`)).toEqual([
      "user:派发子agent，让它搜索一下BGP邻居的建立条件",
      "assistant:子 agent 已完成搜索，BGP 邻居建立条件如下。",
    ]);
    expect(messages.every((m) => m.status === "ready")).toBe(true);
  });

  it("keeps legitimate repeated backend user turns with the same text", () => {
    const store = useWorkbenchStore.getState();
    store.switchSession("sess-repeat");

    store.mergeFromBackend("sess-repeat", [
      {
        message_id: "run-a:user",
        session_id: "sess-repeat",
        role: "user",
        content: "查看本机IP地址",
        created_at: "2026-06-28T10:00:00Z",
        run_id: "run-a",
      },
      {
        message_id: "run-a:assistant",
        session_id: "sess-repeat",
        role: "assistant",
        content: "本机 IP 查询完成。",
        created_at: "2026-06-28T10:00:01Z",
        run_id: "run-a",
      },
      {
        message_id: "run-b:user",
        session_id: "sess-repeat",
        role: "user",
        content: "查看本机IP地址",
        created_at: "2026-06-28T10:00:02Z",
        run_id: "run-b",
      },
      {
        message_id: "run-b:assistant",
        session_id: "sess-repeat",
        role: "assistant",
        content: "再次查询完成。",
        created_at: "2026-06-28T10:00:03Z",
        run_id: "run-b",
      },
    ]);

    const messages = useWorkbenchStore.getState().bySession["sess-repeat"];
    expect(messages.map((m) => `${m.run_id}:${m.role}:${m.text}`)).toEqual([
      "run-a:user:查看本机IP地址",
      "run-a:assistant:本机 IP 查询完成。",
      "run-b:user:查看本机IP地址",
      "run-b:assistant:再次查询完成。",
    ]);
  });

  it("moves scratch streaming messages before final websocket update", () => {
    const store = useWorkbenchStore.getState();
    store.switchSession(null);
    store.appendUser("你好", "_scratch");
    const assistantId = store.appendAssistantStreaming("_scratch");
    store.updateAssistant(assistantId, { text: "流式片段" }, "_scratch");

    store.moveSessionMessages("_scratch", "sess-new");
    store.switchSession("sess-new");
    useWorkbenchStore.getState().updateAssistant(
      assistantId,
      {
        status: "ready",
        text: "最终回答",
        run_id: "turn-new",
      },
      "sess-new",
    );

    const state = useWorkbenchStore.getState();
    expect(state.bySession["_scratch"]).toBeUndefined();
    expect(state.bySession["sess-new"].map((m) => `${m.role}:${m.text}:${m.status}`)).toEqual([
      "user:你好:ready",
      "assistant:最终回答:ready",
    ]);
    expect(state.bySession["sess-new"][1].run_id).toBe("turn-new");
  });
  it("defers full history serialization until streaming updates become idle", () => {
    vi.useFakeTimers();
    const write = vi.spyOn(Storage.prototype, "setItem");
    const store = useWorkbenchStore.getState();
    const assistantId = store.appendAssistantStreaming("sess-persist");
    for (const text of ["一", "一二", "一二三"]) {
      store.updateAssistant(assistantId, { text }, "sess-persist");
    }
    expect(write).not.toHaveBeenCalledWith(
      expect.stringContaining("lzcore_workbench"), expect.any(String),
    );
    vi.advanceTimersByTime(499);
    expect(write).not.toHaveBeenCalledWith(
      expect.stringContaining("lzcore_workbench"), expect.any(String),
    );
    vi.advanceTimersByTime(1);
    expect(write).toHaveBeenCalledWith(
      expect.stringContaining("lzcore_workbench"), expect.any(String),
    );
    vi.useRealTimers();
  });


  it("replaces a parent-run approval wait with its terminal continuation result", () => {
    const store = useWorkbenchStore.getState();
    store.switchSession("sess-approval");
    store.mergeFromBackend("sess-approval", [{
      message_id: "parent-run:user", session_id: "sess-approval", role: "user",
      content: "删除临时文件", created_at: "2026-08-17T00:00:00Z", run_id: "parent-run",
    }]);
    const assistantId = store.appendAssistantStreaming("sess-approval");
    store.updateAssistant(assistantId, {
      run_id: "parent-run", status: "ready",
      text: "该操作正在等待审批，批准后将从当前步骤继续。",
    }, "sess-approval");
    store.mergeFromBackend("sess-approval", [
      {
        message_id: "parent-run:user", session_id: "sess-approval", role: "user",
        content: "删除临时文件", created_at: "2026-08-17T00:00:00Z", run_id: "parent-run",
      },
      {
        message_id: "parent-run:assistant", session_id: "sess-approval", role: "assistant",
        content: "删除已落地。", created_at: "2026-08-17T00:00:01Z", run_id: "parent-run",
      },
    ]);
    expect(useWorkbenchStore.getState().bySession["sess-approval"].map(
      (message) => `${message.run_id}:${message.role}:${message.text}`,
    )).toEqual([
      "parent-run:user:删除临时文件",
      "parent-run:assistant:删除已落地。",
    ]);
  });

  it("removes local placeholders for a server-rejected turn", () => {
    const store = useWorkbenchStore.getState();
    store.switchSession("sess-conflict");
    const userId = store.appendUser("不应提交的第二回合", "sess-conflict");
    const assistantId = store.appendAssistantStreaming("sess-conflict");
    store.discardMessages([userId, assistantId], "sess-conflict");
    expect(useWorkbenchStore.getState().bySession["sess-conflict"]).toEqual([]);
  });
  it("keeps user before assistant when legacy backend timestamps collide across runs", () => {
    const store = useWorkbenchStore.getState();
    store.switchSession("sess-collision");
    store.mergeFromBackend("sess-collision", [
      {
        message_id: "fc148697:assistant", session_id: "sess-collision", role: "assistant",
        content: "你好！很高兴见到你。", created_at: "2026-08-18T06:16:51.661472+00:00",
        run_id: "fc148697",
      },
      {
        message_id: "request_41:user", session_id: "sess-collision", role: "user",
        content: "你好啊", created_at: "2026-08-18T06:16:51.661472+00:00",
        run_id: "request_41",
      },
    ]);
    expect(useWorkbenchStore.getState().bySession["sess-collision"].map(
      (message) => `${message.role}:${message.text}`,
    )).toEqual([
      "user:你好啊",
      "assistant:你好！很高兴见到你。",
    ]);
  });


  it("merges a delayed completion by client request id without replacing another turn", () => {
    const store = useWorkbenchStore.getState();
    store.switchSession("sess-request-correlation");
    const oldUser = store.appendUser("旧请求", "sess-request-correlation", undefined, "request-old");
    const oldAssistant = store.appendAssistantStreaming("sess-request-correlation", "request-old");
    store.updateAssistant(oldAssistant, { status: "error", text: "连接中断，等待服务器恢复。" }, "sess-request-correlation");
    const nextUser = store.appendUser("新请求", "sess-request-correlation", undefined, "request-new");
    const nextAssistant = store.appendAssistantStreaming("sess-request-correlation", "request-new");

    store.mergeFromBackend("sess-request-correlation", [
      {
        message_id: "run-old:user", session_id: "sess-request-correlation", role: "user",
        content: "旧请求", created_at: "2026-08-19T10:00:00Z", run_id: "run-old",
        metadata: { client_request_id: "request-old" },
      },
      {
        message_id: "run-old:assistant", session_id: "sess-request-correlation", role: "assistant",
        content: "旧请求的最终结果", created_at: "2026-08-19T10:00:01Z", run_id: "run-old",
        metadata: { client_request_id: "request-old" },
      },
    ]);

    const messages = useWorkbenchStore.getState().bySession["sess-request-correlation"];
    expect(messages).toHaveLength(4);
    expect(messages.find((message) => message.id === oldUser)?.run_id).toBe("run-old");
    expect(messages.find((message) => message.id === oldAssistant)).toMatchObject({
      run_id: "run-old", text: "旧请求的最终结果", status: "error",
    });
    expect(messages.find((message) => message.id === nextUser)?.client_request_id).toBe("request-new");
    expect(messages.find((message) => message.id === nextAssistant)).toMatchObject({
      client_request_id: "request-new", status: "streaming", text: "",
    });
  });

});
