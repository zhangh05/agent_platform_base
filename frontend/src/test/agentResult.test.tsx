/**
 * Test — RuntimeEventTimeline v3.9 collapsible cards.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RuntimeEventTimeline } from "../components/RuntimeEventTimeline";
import { installMockApi, resetMocks } from "./mockServer";
import type { AgentResult } from "../types";
import type { ChatMsg } from "../stores/workbench";

const sampleResult: AgentResult = {
  ok: true,
  final_response: "OSPF 是一种链路状态路由协议。",
  events: [
    { event_id: "evt-1", event_type: "turn_started", summary: "轮次启动" },
    { event_id: "call-1", event_type: "tool_call", tool_id: "web.manage", summary: "搜索 OSPF 协议" },
    { event_id: "evt-3", event_type: "tool_result", tool_id: "web.manage", summary: "Found 3 results" },
  ],
  trace_id: "trace_abc123",
  session_id: "sess_xyz",
  turn_id: "turn_001",
  tool_calls: [
    { call_id: "call-1", tool_id: "web.manage", ok: true, duration_ms: 450, summary: "Found 3 results about OSPF" },
  ],
  warnings: [],
  errors: [],
  metadata: {
    selected_capabilities: ["knowledge"],
    workspace_id: "default",
    planner_mode: "deterministic",
    source_count: 3,
    retrieval_backend: "bm25",
  },
};

const failedResult: AgentResult = {
  ok: false,
  final_response: "",
  events: [{ event_id: "evt-err", event_type: "error", summary: "Connection refused" }],
  trace_id: "trace_fail", session_id: "sess_xyz", turn_id: "turn_002",
  tool_calls: [], warnings: ["Retry limit exceeded"], errors: ["SSH connection refused: port 22"],
  metadata: { workspace_id: "default" },
};

function messagesFor(result: AgentResult): ChatMsg[] {
  const runId = result.turn_id || "run-test";
  return [
    {
      id: `${runId}-user`,
      role: "user",
      text: "测试请求",
      created_at: "2026-06-28T10:00:00Z",
      status: "ready",
      run_id: runId,
    },
    {
      id: `${runId}-assistant`,
      role: "assistant",
      text: result.final_response || result.errors?.[0] || "",
      created_at: "2026-06-28T10:00:01Z",
      status: result.ok ? "ready" : "error",
      run_id: runId,
      result,
    },
  ];
}

describe("RuntimeEventTimeline", () => {
  beforeEach(() => { resetMocks(); installMockApi(); });

  it("shows turn_id in card header", () => {
    render(<RuntimeEventTimeline messages={messagesFor(sampleResult)} />);
    expect(screen.getByTestId("runtime-timeline")).toBeInTheDocument();
    expect(screen.getByText(/turn_001/)).toBeInTheDocument();
    expect(screen.getByText(/OSPF/)).toBeInTheDocument();
  });

  it("shows steps after clicking expand", () => {
    render(<RuntimeEventTimeline messages={messagesFor(sampleResult)} />);
    // Click the card bar to expand
    fireEvent.click(screen.getByText(/turn_001/).closest(".rt-card-bar")!);
    expect(screen.getByText("turn_started")).toBeInTheDocument();
    expect(screen.getByText("轮次启动")).toBeInTheDocument();
  });

  it("shows error diagnostics for failed run", () => {
    render(<RuntimeEventTimeline messages={messagesFor(failedResult)} />);
    fireEvent.click(screen.getByText(/turn_002/).closest(".rt-card-bar")!);
    expect(screen.getAllByText(/SSH connection refused/).length).toBeGreaterThan(0);
  });

  it("shows empty state when no results", () => {
    render(<RuntimeEventTimeline messages={[]} />);
    expect(screen.getByTestId("timeline-empty")).toBeInTheDocument();
  });

  it("shows workspace metadata", () => {
    render(<RuntimeEventTimeline messages={messagesFor(sampleResult)} />);
    expect(screen.getByText("default")).toBeInTheDocument();
  });
});
