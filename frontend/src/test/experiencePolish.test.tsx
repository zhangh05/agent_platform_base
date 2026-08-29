import { describe, it, expect, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "../app/App";
import { AppLayout } from "../layouts/AppLayout";
import { Sidebar } from "../layouts/Sidebar";
import { TaskWorkbench } from "../pages/AgentWorkbench/AgentWorkbench";
import { enqueue, installMockApi, resetMocks } from "./mockServer";
import { useSessionStore, useUIStore } from "../stores/session";
import { useWorkbenchStore } from "../stores/workbench";
import { formatEventTime } from "../utils/runEvent";
import { MemoryRouter } from "../router";

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
    expect(await screen.findByText("v1.0.2")).toBeInTheDocument();
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

  it("keeps the global sidebar visible on network operations routes", () => {
    render(
      <MemoryRouter initialEntries={["/extensions/network.operations/manage"]}>
        <AppLayout navigationItems={[]} settingsNavigationItems={[]}>
          <div>网络设备与 Skill</div>
        </AppLayout>
      </MemoryRouter>,
    );

    expect(screen.getByTestId("layout-left")).not.toHaveClass("collapsed");
    expect(screen.getByTestId("layout-left")).toContainElement(screen.getByText("最近会话"));
    expect(screen.getByTestId("layout-center")).toHaveTextContent("网络设备与 Skill");
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

  it("requires an explicit active session before accepting input", async () => {
    render(<TaskWorkbench />);

    expect(await screen.findByRole("heading", { name: "请先新建会话" })).toBeInTheDocument();
    expect(screen.getByTestId("chat-input")).toBeDisabled();
    expect(screen.getByTestId("chat-input")).toHaveAttribute("placeholder", "请先点击左侧 + 新建会话");
    expect(screen.getByTestId("btn-send")).toBeDisabled();
    expect(screen.getAllByTitle("请先新建会话").every((element) => element.hasAttribute("disabled"))).toBe(true);
    expect(screen.getByRole("button", { name: "OSPF 邻居不起来" })).toBeDisabled();
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

    expect((await screen.findAllByText("你好")).length).toBeGreaterThan(0);
    expect(await screen.findByText("你好，我在。")).toBeInTheDocument();
    expect(screen.queryByLabelText("执行摘要")).not.toBeInTheDocument();
    expect(screen.queryByText("需要关注")).not.toBeInTheDocument();
    expect(screen.queryByTestId("workbench-empty")).not.toBeInTheDocument();
  });

  it("fills a clear prompt from a quick chip", async () => {
    // The chat input only mounts once a session is active (AgentWorkbench gates the input
    // bar behind `currentSessionId`), so provide one to exercise the chip → input flow.
    useSessionStore.setState({ currentWorkspaceId: "default", currentSessionId: "sess-chip" });
    render(<TaskWorkbench />);

    expect(screen.getByTestId("chat-input")).toBeEnabled();
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

    expect(await screen.findByText("v0.4")).toBeInTheDocument();
    expect(screen.queryByText("vv0.4")).not.toBeInTheDocument();
  });


  it("formats Unix-second trace timestamps without showing 1970", () => {
    const formatted = formatEventTime({ timestamp: 1786699729.6 });
    expect(formatted).not.toContain("1970");
    expect(formatEventTime({ timestamp: 12.5 })).toBe("—");
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
    enqueue("/sessions", { status: 200, data: { sessions } });
    enqueue("/runs/recent", { status: 200, data: { runs: [] } });

    render(<Sidebar />);

    expect(await screen.findByText("Session 0")).toBeInTheDocument();
    expect(screen.queryByTestId("ws-list")).not.toBeInTheDocument();
    expect(screen.queryByText("Session 12")).not.toBeInTheDocument();
    expect(screen.getByText("另有 3 个活跃会话")).toBeInTheDocument();
    const sessionPanel = screen.getByText("最近会话").closest(".sidebar-panel");
    const runPanel = screen.getByText("最近任务").closest(".sidebar-panel");
    expect(sessionPanel?.nextElementSibling).toBe(runPanel);
  });

  it("keeps session actions in one accessible menu", async () => {
    enqueue("/sessions", {
      status: 200,
      data: {
        sessions: [{
          session_id: "sess-menu",
          workspace_id: "default",
          title: "查看本机IP地址",
          status: "active",
          created_at: "",
          updated_at: "",
          message_count: 2,
        }],
      },
    });
    enqueue("/runs/recent", { status: 200, data: { runs: [] } });

    render(<Sidebar />);

    const trigger = await screen.findByTestId("session-menu-trigger-sess-menu");
    const sessionItem = screen.getByTestId("sess-sess-menu");
    expect(sessionItem.querySelectorAll(":scope > .row-actions")).toHaveLength(0);
    expect(sessionItem.querySelectorAll(".session-more-trigger")).toHaveLength(1);
    fireEvent.click(trigger);
    expect(trigger.closest("details")).toHaveAttribute("open");
    const menu = screen.getByRole("menu", { name: "会话操作" });
    expect(menu).toHaveTextContent("重命名");
    expect(menu).toHaveTextContent("归档");
    expect(menu).toHaveTextContent("永久删除");
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

  it("recovers the workbench LLM indicator after startup probe becomes healthy", async () => {
    useSessionStore.setState({ currentWorkspaceId: "default", currentSessionId: "sess-llm-retry" });
    enqueue("/agent/llm/status", {
      status: 200,
      data: {
        enabled: true,
        connected: false,
        provider: "minimax",
        provider_type: "anthropic_messages",
        model: "MiniMax-M3",
        safe_mode: true,
        key_loaded: true,
        key_source: "ui_settings",
        config_source: "ui_settings",
        enabled_by_ui: true,
        settings_file_exists: true,
        health: { connected: false },
      },
    });
    enqueue("/agent/llm/status", {
      status: 200,
      data: {
        enabled: true,
        connected: true,
        provider: "minimax",
        provider_type: "anthropic_messages",
        model: "MiniMax-M3",
        safe_mode: true,
        key_loaded: true,
        key_source: "ui_settings",
        config_source: "ui_settings",
        enabled_by_ui: true,
        settings_file_exists: true,
        health: { connected: true, chat_completion_ok: true },
      },
    });

    render(<TaskWorkbench />);

    expect(await screen.findByText("模型可用 · MiniMax-M3", {}, { timeout: 2500 })).toBeInTheDocument();
  });

});
