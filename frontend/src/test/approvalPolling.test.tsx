import { act, render } from "@testing-library/react";
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
});
