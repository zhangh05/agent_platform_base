import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApprovalBubble } from "../components/ApprovalBubble";
import { approvalApi } from "../api";
import { useSessionStore } from "../stores/session";

class HealthyEventSource {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn();
}

describe("approval transport lifecycle", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", HealthyEventSource);
    useSessionStore.getState().resetForUser("default");
    useSessionStore.getState().setCurrentSession("session-1");
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("does not keep polling when the initial check is empty and SSE is healthy", async () => {
    const pending = vi.spyOn(approvalApi, "pending").mockResolvedValue({
      ok: true,
      pending: [],
      count: 0,
    });

    render(<ApprovalBubble />);
    await act(async () => { await Promise.resolve(); });
    expect(pending).toHaveBeenCalledTimes(1);

    await act(async () => { vi.advanceTimersByTime(60_000); });
    expect(pending).toHaveBeenCalledTimes(1);
  });

  it("disables approval actions while a resolution is in flight", async () => {
    vi.spyOn(approvalApi, "pending").mockResolvedValue({
      ok: true,
      pending: [{
        approval_id: "approval-1",
        tool_id: "exec.run",
        risk_level: "high",
        arguments_preview: {},
        created_at: new Date().toISOString(),
        created_at_iso: new Date().toISOString(),
        expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
        approval_kind: "interactive",
        requester: "test-user",
      }],
      count: 1,
    });
    let finish!: (value: { ok: boolean; approval_id: string; decision: string }) => void;
    vi.spyOn(approvalApi, "resolve").mockImplementation(
      () => new Promise((resolve) => { finish = resolve; }),
    );

    render(<ApprovalBubble />);
    await act(async () => { await Promise.resolve(); });
    const allow = screen.getByRole("button", { name: /允许/ });
    const reject = screen.getByRole("button", { name: /拒绝/ });
    fireEvent.click(allow);

    expect(allow).toBeDisabled();
    expect(reject).toBeDisabled();

    await act(async () => { finish({ ok: true, approval_id: "approval-1", decision: "approve" }); });
  });
});
