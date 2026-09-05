import "../pages/AgentWorkbench/WorkbenchHighlight";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkbenchHeader } from "../pages/AgentWorkbench/components/WorkbenchHeader";
import { MessageRow } from "../pages/AgentWorkbench/components/MessageRow";

describe("workbench refinement", () => {
  it("keeps a named timeline action and its selected state while the header is collapsed", () => {
    const onViewModeChange = vi.fn();
    render(<WorkbenchHeader sessionTitle="任务" viewMode="chat" onViewModeChange={onViewModeChange} headerCollapsed onToggleHeaderCollapsed={vi.fn()} llmHealth={{ connected: true }} currentSessionId="session" visibleHistory={[]} />);
    expect(screen.getByRole("button", { name: "对话" })).toHaveAttribute("aria-pressed", "true");
    const timeline = screen.getByRole("button", { name: "时间线" });
    expect(timeline).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(timeline);
    expect(onViewModeChange).toHaveBeenCalledWith("timeline");
  });
  it("reports failed code copying without claiming success", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    render(<MessageRow m={{ id: "copy-test", role: "assistant", text: "```text\nevidence\n```", status: "ready", created_at: "2026-09-05T00:00:00Z" }} idx={0} total={1} lastUserInput="" onRetryOriginal={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "复制" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "复制失败" })).toBeInTheDocument());
  });
});
