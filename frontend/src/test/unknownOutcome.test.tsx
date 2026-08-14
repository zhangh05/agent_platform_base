import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ResultInline } from "../pages/AgentWorkbench/components/ResultInline";
import type { AgentResult } from "../types";

const unknownResult: AgentResult = {
  ok: false,
  final_response: "写操作等待核对。",
  events: [],
  trace_id: "trace-unknown",
  session_id: "session-unknown",
  turn_id: "turn-unknown",
  tool_calls: [{
    call_id: "call-write-1",
    tool_id: "workspace.file",
    ok: false,
    summary: "remote write timed out",
  }],
  warnings: [],
  errors: ["unknown_outcome"],
  metadata: {
    workspace_id: "default",
    execution_outcome: "unknown",
    unknown_outcome: {
      status: "unknown",
      tool_id: "workspace.file",
      call_id: "call-write-1",
      error_code: "TOOL_TIMEOUT_UNCERTAIN",
      execution_may_continue: true,
    },
  },
};

describe("unknown outcome result UI", () => {
  it("shows the durable uncertainty fact and suppresses unsafe retry actions", () => {
    const retryOriginal = vi.fn();
    const retryAlternative = vi.fn();

    render(
      <ResultInline
        result={unknownResult}
        fallbackText=""
        onRetryOriginal={retryOriginal}
        onRetryAlternative={retryAlternative}
      />,
    );

    expect(screen.getByLabelText("执行摘要")).toHaveTextContent("结果未知");
    expect(screen.getByLabelText("执行摘要")).toHaveTextContent("写入已冻结，等待受控核对");
    expect(screen.getByTestId("unknown-outcome-alert")).toHaveTextContent("执行结果未知");
    expect(screen.getByText("工具：workspace.file")).toBeInTheDocument();
    expect(screen.getByText("调用：call-write-1")).toBeInTheDocument();
    expect(screen.getByText("代码：TOOL_TIMEOUT_UNCERTAIN")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试原任务" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "换方案继续" })).not.toBeInTheDocument();
    expect(retryOriginal).not.toHaveBeenCalled();
    expect(retryAlternative).not.toHaveBeenCalled();
  });
});
