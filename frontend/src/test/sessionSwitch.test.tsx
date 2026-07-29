/**
 * Test 9 — session 切换
 */

import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Sidebar } from "../layouts/Sidebar";
import { enqueue, getRequests, installMockApi, resetMocks } from "./mockServer";
import { useSessionStore } from "../stores/session";

describe("Session switch", () => {
  beforeEach(() => {
    resetMocks();
    installMockApi();
    useSessionStore.getState().reset();
  });

  it("switches active session when user clicks", async () => {
    enqueue("/workspaces", { status: 200, data: { workspaces: [{ workspace_id: "default", name: "Default", created_at: "", is_default: true, stats: { session_count: 2, artifact_count: 0, knowledge_source_count: 0 } }] } });
    enqueue("/sessions", {
      status: 200,
      data: {
        sessions: [
          { session_id: "sess-A", workspace_id: "default", title: "Session A", status: "active", created_at: "2026-06-11T09:00:00Z", updated_at: "2026-06-11T09:00:00Z", message_count: 3 },
          { session_id: "sess-B", workspace_id: "default", title: "Session B", status: "active", created_at: "2026-06-11T09:30:00Z", updated_at: "2026-06-11T09:30:00Z", message_count: 1 },
        ],
      },
    });
    enqueue("/runs/recent", { status: 200, data: { runs: [] } });
    render(<Sidebar />);
    const sessB = await screen.findByTestId("sess-btn-sess-B");
    await waitFor(() => expect(useSessionStore.getState().currentSessionId).toBe("sess-A"));
    fireEvent.click(sessB);
    await waitFor(() => expect(useSessionStore.getState().currentSessionId).toBe("sess-B"));
  });

  it("loads recent runs for the active session", async () => {
    enqueue("/workspaces", { status: 200, data: { workspaces: [{ workspace_id: "default", name: "Default", created_at: "", is_default: true, stats: { session_count: 2, artifact_count: 0, knowledge_source_count: 0 } }] } });
    enqueue("/sessions", {
      status: 200,
      data: {
        sessions: [
          { session_id: "sess-A", workspace_id: "default", title: "Session A", status: "active", created_at: "2026-06-11T09:00:00Z", updated_at: "2026-06-11T09:00:00Z", message_count: 3 },
          { session_id: "sess-B", workspace_id: "default", title: "Session B", status: "active", created_at: "2026-06-11T09:30:00Z", updated_at: "2026-06-11T09:30:00Z", message_count: 1 },
        ],
      },
    });
    enqueue("/runs/recent", { status: 200, data: { runs: [] } });
    enqueue("/runs/recent", { status: 200, data: { runs: [{ run_id: "run-B", session_id: "sess-B", status: "ok", user_input_summary: "B run" }] } });

    render(<Sidebar />);
    const sessB = await screen.findByTestId("sess-btn-sess-B");
    fireEvent.click(sessB);

    await screen.findByText("B run");
    const recentRunRequests = getRequests().filter((r) => r.url === "/runs/recent");
    expect(recentRunRequests.at(-1)?.params).toMatchObject({
      workspace_id: "default",
      session_id: "sess-B",
    });
  });
});
