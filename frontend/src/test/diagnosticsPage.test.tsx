import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "../router";
import { Diagnostics } from "../pages/Diagnostics/Diagnostics";
import { scopedLocalStorageKey, setActiveUserScope } from "../utils/userScope";
import { useSessionStore } from "../stores/session";

describe("Diagnostics page", () => {
  it("renders cached diagnostics without loops and does not hide selfcheck warnings", () => {
    useSessionStore.setState({ currentWorkspaceId: "default" });
    setActiveUserScope("AdminWarnings", "default");
    localStorage.setItem(scopedLocalStorageKey("diagnostics_v1"), JSON.stringify({
      ts: "2026-07-22T00:00:00.000Z",
      health: {
        summary: { ok: 1, warning: 0, error: 0 },
        components: [{ name: "agent", status: "ok", message: "ready" }],
      },
      selfcheck: {
        status: "warning",
        issues: [{
          severity: "warning",
          code: "ABSOLUTE_PATH",
          ref_id: "run-1",
          message: "Run record run-1 contains absolute path",
          suggested_action: "Redact absolute paths",
        }],
      },
      usage: {
        call_count: 1,
        total_tokens: 10,
        input_tokens: 6,
        output_tokens: 4,
        estimated_cost: 0,
        last_updated: "2026-07-22T00:00:00.000Z",
      },
      contextOk: true,
      prompts: [{ prompt_id: "p1", description: "测试提示词", version: "1" }],
      retention: { policy: { runs_max_age_days: 7 } },
      archive: { policy: { traces_max_age_days: 7 } },
      continuations: {
        counts: { stalled: 1, pending: 0, running: 0, failed: 0 },
        continuations: [{
          continuation_id: "cont_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          workspace_id: "default",
          session_id: "session-1",
          parent_run_id: "run-1",
          status: "stalled",
          created_at: "2026-07-22T00:00:00.000Z",
          updated_at: "2026-07-22T00:01:00.000Z",
          approval_count: 1,
          decision_count: 1,
        }],
      },
    }));

    render(
      <React.StrictMode>
        <MemoryRouter>
          <Diagnostics />
        </MemoryRouter>
      </React.StrictMode>,
    );

    expect(screen.getByTestId("page-diagnostics")).toBeInTheDocument();
    expect(screen.getByText("需要注意")).toBeInTheDocument();
    expect(screen.getByText("运行记录（run-1）含本机绝对路径")).toBeInTheDocument();
    expect(screen.getByText("● 全部正常")).toBeInTheDocument();
    expect(screen.getByText("智能体核心")).toBeInTheDocument();
    expect(screen.getByText("1 项待人工核对")).toBeInTheDocument();
    expect(screen.getByText(/心跳失联，执行结果未知/)).toBeInTheDocument();
  });

  it("treats historical failures as history rather than a current outage", () => {
    useSessionStore.setState({ currentWorkspaceId: "default" });
    setActiveUserScope("AdminHistory", "default");
    localStorage.setItem(scopedLocalStorageKey("diagnostics_v1"), JSON.stringify({
      ts: "2026-08-23T00:00:00.000Z",
      health: { summary: { ok: 1, warning: 0, error: 0 }, components: [{ name: "agent", status: "ok" }] },
      selfcheck: { status: "healthy", issues: [] },
      usage: { call_count: 1, total_tokens: 10, input_tokens: 6, output_tokens: 4, estimated_cost: 0, last_updated: "2026-08-23T00:00:00.000Z" },
      contextOk: true,
      prompts: [],
      retention: {},
      archive: {},
      continuations: { counts: { stalled: 0, failed: 3 }, continuations: [] },
      operations: { counts: { unknown: 0, running: 0, failed: 12 }, operations: [] },
    }));

    render(<MemoryRouter><Diagnostics /></MemoryRouter>);

    expect(screen.getByText("系统运行正常")).toBeInTheDocument();
    expect(screen.getAllByText("历史失败")).toHaveLength(2);
    expect(screen.getByText("无异常")).toBeInTheDocument();
    expect(screen.getByText("无待核对项")).toBeInTheDocument();
  });

  it("offers controlled resolution only for genuinely unknown operations", () => {
    useSessionStore.setState({ currentWorkspaceId: "default" });
    setActiveUserScope("AdminUnknown", "default");
    localStorage.setItem(scopedLocalStorageKey("diagnostics_v1"), JSON.stringify({
      ts: "2026-08-23T00:00:00.000Z",
      health: { summary: { ok: 1, warning: 0, error: 0 }, components: [{ name: "agent", status: "ok" }] },
      selfcheck: { status: "healthy", issues: [] },
      usage: { call_count: 1, total_tokens: 10, input_tokens: 6, output_tokens: 4, estimated_cost: 0, last_updated: "2026-08-23T00:00:00.000Z" },
      contextOk: true,
      prompts: [], retention: {}, archive: {},
      continuations: { counts: { stalled: 0 }, continuations: [] },
      operations: {
        counts: { unknown: 1, running: 0, failed: 12 },
        operations: [{ operation_id: "op_1234567890abcdef12345678", canonical_tool: "agent.manage", status: "unknown", error_code: "TOOL_TIMEOUT_UNCERTAIN", planned_at: "2026-08-15T18:02:19Z" }],
      },
    }));

    render(<MemoryRouter><Diagnostics /></MemoryRouter>);

    expect(screen.getByText("1 项未决操作")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "核对为成功" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "核对为失败" })).toBeInTheDocument();
  });
});
