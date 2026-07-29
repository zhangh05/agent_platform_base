import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Diagnostics } from "../pages/Diagnostics/Diagnostics";
import { useSessionStore } from "../stores/session";

describe("Diagnostics page", () => {
  it("renders cached diagnostics without loops and does not hide selfcheck warnings", () => {
    useSessionStore.setState({ currentWorkspaceId: "default" });
    localStorage.setItem("diagnostics_v1", JSON.stringify({
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
    }));

    render(
      <React.StrictMode>
        <Diagnostics />
      </React.StrictMode>,
    );

    expect(screen.getByTestId("page-diagnostics")).toBeInTheDocument();
    expect(screen.getByText("需要注意")).toBeInTheDocument();
    expect(screen.getByText("运行记录（run-1）含本机绝对路径")).toBeInTheDocument();
    expect(screen.getByText("● 全部正常")).toBeInTheDocument();
    expect(screen.getByText("智能体核心")).toBeInTheDocument();
  });
});
