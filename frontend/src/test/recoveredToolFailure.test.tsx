import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ResultInline } from "../pages/AgentWorkbench/components/ResultInline";

const baseResult = {
  trace_id: "trace-recovered",
  session_id: "session-recovered",
  events: [],
  warnings: [],
  errors: [],
  ok: true,
  turn_id: "turn-recovered",
  final_response: "删除已完成",
  tool_calls: [
    { tool_id: "workspace.file", call_id: "call-invalid", ok: false, error: "MISSING_REQUIRED_ARG" },
    { tool_id: "workspace.file", call_id: "call-success", ok: true, result: { status: "success" } },
  ],
  metadata: { execution_outcome: "complete" as const },
};

describe("ResultInline recovered tool failures", () => {
  it("keeps task completion separate from failed tool attempts", () => {
    render(<ResultInline result={baseResult} fallbackText="" />);

    expect(screen.getByText("1 项成功，1 次失败未影响任务完成")).toBeInTheDocument();
    expect(screen.queryByText("1 项需要跟进")).not.toBeInTheDocument();
    expect(screen.getByText("工具调用已完成；1 次调用失败未影响任务结论")).toBeInTheDocument();
  });
});
