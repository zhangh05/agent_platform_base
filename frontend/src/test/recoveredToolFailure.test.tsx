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

  it("shows runtime goal-loop lifecycle instead of a generic failure only", () => {
    render(<ResultInline result={{
      ...baseResult,
      metadata: {
        execution_outcome: "complete" as const,
        recovery_goals: [{
          goal_id: "tool-goal-1",
          status: "passed" as const,
          source_tool_id: "web.manage",
          description: "Resolve failed web.manage observation",
          attempts: 1,
          max_attempts: 3,
        }],
      },
    }} fallbackText="" />);

    expect(screen.getByText("1 个恢复目标已满足")).toBeInTheDocument();
    expect(screen.getByTestId("goal-loop-summary")).toHaveTextContent("目标已满足");
    expect(screen.getByTestId("goal-loop-summary")).toHaveTextContent("外部资料失败后的替代取证");
  });

  it("does not describe a queued tracked task as completed", () => {
    render(<ResultInline result={{
      ...baseResult,
      metadata: {
        execution_outcome: "partial" as const,
        tracking_summary: {
          task_id: "inspection-queued",
          status: "queued",
          done: false,
          progress: { completed: 0, total: 6 },
        },
      },
    }} fallbackText="" />);

    expect(screen.getByText("巡检任务仍在执行：已获取 0 / 6 台设备结果")).toBeInTheDocument();
    expect(screen.getAllByText("已提交，等待结果")).toHaveLength(2);
    expect(screen.getByLabelText("执行摘要")).toHaveTextContent("等待设备结果");
  });
});
