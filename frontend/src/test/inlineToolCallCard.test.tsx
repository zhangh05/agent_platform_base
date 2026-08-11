import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { InlineToolCallCard } from "../pages/AgentWorkbench/components/InlineToolCallCard";
import type { InlineToolCall } from "../types";


describe("InlineToolCallCard orchestration metadata", () => {
  it("ignores malformed persisted types instead of crashing or showing false parallel state", () => {
    const toolCall = {
      call_id: "call-1",
      tool_name: "data.manage",
      ok: true,
      orchestration: {
        step_id: "parse",
        layer: "1",
        parallel: "False",
        depends_on: "[]",
      },
    } as unknown as InlineToolCall;

    render(<InlineToolCallCard toolCall={toolCall} seq={1} />);
    fireEvent.click(screen.getByText("data.manage"));

    expect(screen.getByText("步骤：parse")).toBeInTheDocument();
    expect(screen.queryByText("并行执行")).not.toBeInTheDocument();
    expect(screen.queryByText(/依赖：/)).not.toBeInTheDocument();
    expect(screen.queryByText("第 1 组")).not.toBeInTheDocument();
  });

  it("shows valid parallel dependency metadata", () => {
    const toolCall = {
      call_id: "call-2",
      tool_id: "text.analyze",
      tool_name: "text.analyze",
      ok: true,
      orchestration: {
        step_id: "analyse",
        layer: 2,
        parallel: true,
        depends_on: ["parse"],
      },
    } as InlineToolCall;

    render(<InlineToolCallCard toolCall={toolCall} seq={2} />);
    fireEvent.click(screen.getByText("text.analyze"));

    expect(screen.getByText("第 2 组")).toBeInTheDocument();
    expect(screen.getByText("并行执行")).toBeInTheDocument();
    expect(screen.getByText("依赖：parse")).toBeInTheDocument();
  });
});
