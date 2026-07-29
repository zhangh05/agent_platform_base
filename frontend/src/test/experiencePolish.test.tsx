import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "../app/App";
import { Sidebar } from "../layouts/Sidebar";
import { TaskWorkbench } from "../pages/AgentWorkbench/AgentWorkbench";
import { RuntimeAudit } from "../pages/RuntimeAudit/RuntimeAudit";
import { enqueue, installMockApi, resetMocks } from "./mockServer";
import { useSessionStore, useUIStore } from "../stores/session";
import { useWorkbenchStore } from "../stores/workbench";

describe("Experience polish", () => {
  beforeEach(() => {
    resetMocks();
    installMockApi();
    useSessionStore.getState().reset();
    useWorkbenchStore.setState({ bySession: {}, currentSessionId: null });
    useUIStore.setState({ sidebarOpen: true, theme: "light" });
  });

  it("prefers the default workspace instead of the first test workspace", async () => {
    enqueue("/workspaces", {
      status: 200,
      data: {
        workspaces: [
          { workspace_id: "api_contract_test", name: "api_contract_test", is_default: false, created_at: "", stats: { session_count: 42, artifact_count: 38, knowledge_source_count: 0 } },
          { workspace_id: "default", name: "default", is_default: true, created_at: "", stats: { session_count: 0, artifact_count: 0, knowledge_source_count: 0 } },
        ],
      },
    });
    enqueue("/version", { status: 200, data: { version: "1.0.2" } });
    enqueue("/runtime/summary", {
      status: 200,
      data: {
        capabilities: { total: 7, enabled: 4, planned: 3 },
        tools: { registered: 73, model_visible: 70 },
      },
    });
    enqueue("/sessions", { status: 200, data: { sessions: [] } });
    enqueue("/runs/recent", { status: 200, data: { runs: [] } });

    render(<App />);

    await waitFor(() => {
      expect(useSessionStore.getState().currentWorkspaceId).toBe("default");
    });
    expect(await screen.findByText("Operations Console · v1.0.2")).toBeInTheDocument();
  });

  it("always uses default workspace on startup", async () => {
    enqueue("/workspaces", {
      status: 200,
      data: {
        workspaces: [
          { workspace_id: "default", name: "default", is_default: true, created_at: "", stats: { session_count: 0, artifact_count: 0, knowledge_source_count: 0 } },
        ],
      },
    });
    enqueue("/version", { status: 200, data: { version: "v0.4" } });
    enqueue("/runtime/summary", {
      status: 200,
      data: {
        capabilities: { total: 7, enabled: 4, planned: 3 },
        tools: { registered: 73, model_visible: 70 },
      },
    });
    enqueue("/sessions", { status: 200, data: { sessions: [] } });
    enqueue("/runs/recent", { status: 200, data: { runs: [] } });

    render(<App />);

    await waitFor(() => {
      expect(useSessionStore.getState().currentWorkspaceId).toBe("default");
    });
  });

  it("renders runtime summary in the workbench hint", async () => {
    enqueue("/runtime/summary", {
      status: 200,
      data: {
        capabilities: { total: 7, enabled: 4, planned: 3 },
        tools: { registered: 73, model_visible: 70 },
      },
    });

    render(<TaskWorkbench />);

    // v2 workbench: empty state is shown; runtime summary moved to header status line
    expect(await screen.findByTestId("workbench-empty")).toBeInTheDocument();
  });

  it("renders the active session directly from restored bySession messages", async () => {
    useSessionStore.setState({ currentWorkspaceId: "default", currentSessionId: "sess-restored" });
    enqueue("/sessions/sess-restored/messages", {
      status: 200,
      data: { ok: true, messages: [], count: 0 },
    });
    render(<TaskWorkbench />);

    act(() => {
      useWorkbenchStore.setState({
        bySession: {
          "sess-restored": [
            { id: "u1", role: "user", text: "你好", status: "ready", created_at: "2026-06-18T06:38:23Z" },
            { id: "a1", role: "assistant", text: "你好，我在。", status: "ready", created_at: "2026-06-18T06:38:24Z" },
          ],
        },
        currentSessionId: "sess-restored",
      });
    });

    expect(await screen.findByText("你好")).toBeInTheDocument();
    expect(await screen.findByText("你好，我在。")).toBeInTheDocument();
    expect(screen.queryByTestId("workbench-empty")).not.toBeInTheDocument();
  });

  it("fills a clear prompt from a quick chip", async () => {
    // The chat input only mounts once a session is active (AgentWorkbench gates the input
    // bar behind `currentSessionId`), so provide one to exercise the chip → input flow.
    useSessionStore.setState({ currentWorkspaceId: "default", currentSessionId: "sess-chip" });
    render(<TaskWorkbench />);

    fireEvent.click((await screen.findAllByText("出口策略放通检查"))[0]);

    await waitFor(() => {
      expect(screen.getByTestId("chat-input")).toHaveValue(
        "帮我分析出口访问策略是否放通。请告诉我需要提供源地址、目的地址、端口、协议，以及相关 ACL/NAT/路由配置。",
      );
    });
  });

  it("does not duplicate a leading version prefix from the backend", async () => {
    enqueue("/workspaces", { status: 200, data: { workspaces: [] } });
    enqueue("/version", { status: 200, data: { version: "v0.4" } });

    render(<App />);

    expect(await screen.findByText("Operations Console · v0.4")).toBeInTheDocument();
    expect(screen.queryByText("Operations Console · vv0.4")).not.toBeInTheDocument();
  });

  it("uses run ids to select audit runs with blank turn ids", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    enqueue("/runs/recent", {
      status: 200,
      data: {
        runs: [
          { run_id: "run-a", turn_id: "", trace_id: "trace-a", session_id: "s1", status: "ok", started_at: "", finished_at: "", visible_tools: [], tool_call_count: 0, error_count: 0, warning_count: 0, events: [] },
          { run_id: "run-b", turn_id: "", trace_id: "trace-b", session_id: "s2", status: "ok", started_at: "", finished_at: "", visible_tools: [], tool_call_count: 0, error_count: 0, warning_count: 0, events: [] },
        ],
      },
    });
    enqueue("/workspaces/default/runs/run-a/trace", {
      status: 200,
      data: { events: [] },
    });

    render(<RuntimeAudit />);

    const list = await screen.findByTestId("audit-turn-list");
    const firstRun = await screen.findByTestId("turn-run-a");
    expect(firstRun).toHaveTextContent("run-a");
    expect(screen.getByTestId("turn-run-b")).toHaveTextContent("run-b");

    fireEvent.click(firstRun);

    await waitFor(() => {
      expect(list.querySelectorAll(".list-item.active")).toHaveLength(1);
    });
    expect(firstRun).toHaveClass("active");
    expect(await screen.findByText("该 turn 无 event")).toBeInTheDocument();
    expect(consoleError).not.toHaveBeenCalledWith(
      expect.stringContaining("Each child in a list should have a unique"),
      expect.anything(),
      expect.anything(),
      expect.anything(),
    );
  });

  it("keeps a noisy session list bounded in the sidebar", async () => {
    const sessions = Array.from({ length: 15 }, (_, i) => ({
      session_id: `sess-${i}`,
      workspace_id: "default",
      title: `Session ${i}`,
      status: "active",
      created_at: "",
      updated_at: "",
      message_count: 0,
    }));
    enqueue("/workspaces", {
      status: 200,
      data: {
        workspaces: [
          { workspace_id: "default", name: "default", is_default: true, created_at: "", stats: { session_count: 15, artifact_count: 0, knowledge_source_count: 0 } },
        ],
      },
    });
    enqueue("/sessions", { status: 200, data: { sessions } });
    enqueue("/runs/recent", { status: 200, data: { runs: [] } });

    render(<Sidebar />);

    expect(await screen.findByText("Session 0")).toBeInTheDocument();
    expect(screen.queryByText("Session 12")).not.toBeInTheDocument();
    expect(screen.getByText("另有 3 个活跃会话")).toBeInTheDocument();
  });

  it("keeps the selected session visible when it is outside the sidebar preview", async () => {
    const sessions = Array.from({ length: 15 }, (_, i) => ({
      session_id: `sess-${i}`,
      workspace_id: "default",
      title: `Session ${i}`,
      status: "active",
      created_at: "",
      updated_at: "",
      message_count: 0,
    }));
    useSessionStore.getState().setCurrentSession("sess-14");
    enqueue("/workspaces", {
      status: 200,
      data: {
        workspaces: [
          { workspace_id: "default", name: "default", is_default: true, created_at: "", stats: { session_count: 15, artifact_count: 0, knowledge_source_count: 0 } },
        ],
      },
    });
    enqueue("/sessions", { status: 200, data: { sessions } });
    enqueue("/runs/recent", { status: 200, data: { runs: [] } });

    render(<Sidebar />);

    expect(await screen.findByText("Session 14")).toBeInTheDocument();
    expect(screen.getByTestId("sess-sess-14")).toHaveClass("active");
    expect(screen.getByText("另有 2 个活跃会话")).toBeInTheDocument();
  });
});
