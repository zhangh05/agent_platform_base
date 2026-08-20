import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApprovalBubble } from "../components/ApprovalBubble";
import { approvalApi } from "../api";
import { useSessionStore } from "../stores/session";

describe("approval transport lifecycle", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useSessionStore.getState().resetForUser("default");
    useSessionStore.getState().setCurrentSession("session-1");
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("continues bounded polling when the initial check is empty", async () => {
    const pending = vi.spyOn(approvalApi, "pending").mockResolvedValue({
      ok: true,
      pending: [],
      count: 0,
    });

    render(<ApprovalBubble />);
    await act(async () => { await Promise.resolve(); });
    expect(pending).toHaveBeenCalledTimes(1);

    await act(async () => { vi.advanceTimersByTime(60_000); });
    expect(pending.mock.calls.length).toBeGreaterThan(1);
  });

  it("disables approval actions while a resolution is in flight", async () => {
    vi.spyOn(approvalApi, "pending").mockResolvedValue({
      ok: true,
      pending: [{
        approval_id: "approval-1",
        session_id: "session-1",
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

  it("notifies the owner after a successful approval resolution", async () => {
    vi.spyOn(approvalApi, "pending").mockResolvedValue({
      ok: true,
      pending: [{
        approval_id: "approval-callback",
        session_id: "session-1",
        tool_id: "workspace.file",
        risk_level: "high",
        arguments_preview: { action: "delete" },
        created_at: new Date().toISOString(),
        created_at_iso: new Date().toISOString(),
        expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
        approval_kind: "interactive",
        requester: "test-user",
      }],
      count: 1,
    });
    vi.spyOn(approvalApi, "resolve").mockResolvedValue({
      ok: true,
      approval_id: "approval-callback",
      decision: "approve",
    });
    const onResolved = vi.fn();
    render(<ApprovalBubble onResolved={onResolved} />);
    await act(async () => { await Promise.resolve(); });
    fireEvent.click(screen.getByRole("button", { name: /允许/ }));
    await act(async () => { await Promise.resolve(); });
    expect(onResolved).toHaveBeenCalledWith("approve");
  });


  it("does not overlap a delayed poll and discovers approval on the next interval", async () => {
    let resolveInitial!: (value: { ok: boolean; pending: never[]; count: number }) => void;
    const pending = vi.spyOn(approvalApi, "pending")
      .mockImplementationOnce(() => new Promise((resolve) => { resolveInitial = resolve; }))
      .mockResolvedValueOnce({
        ok: true,
        pending: [{
          approval_id: "approval-after-sse-failure",
          session_id: "session-1",
          tool_id: "workspace.file",
          risk_level: "high",
          arguments_preview: { action: "delete", filepath: "files/data/probe.md" },
          created_at: new Date().toISOString(),
          created_at_iso: new Date().toISOString(),
          expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
          approval_kind: "interactive",
          requester: "test-user",
        }],
        count: 1,
      });

    render(<ApprovalBubble />);
    await act(async () => { await Promise.resolve(); });
    await act(async () => { resolveInitial({ ok: true, pending: [], count: 0 }); });
    await act(async () => { vi.advanceTimersByTime(5_000); await Promise.resolve(); });

    expect(pending).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("approval-bubble")).toBeInTheDocument();
  });

  it("clears a previous session approval before it can be resolved", async () => {
    vi.spyOn(approvalApi, "pending").mockResolvedValue({
      ok: true,
      pending: [{
        approval_id: "approval-session-one",
        session_id: "session-1",
        tool_id: "workspace.file",
        risk_level: "high",
        arguments_preview: { action: "delete", filepath: "old.txt" },
        created_at: new Date().toISOString(),
        created_at_iso: new Date().toISOString(),
        expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
        approval_kind: "interactive",
        requester: "test-user",
      }],
      count: 1,
    });
    const resolve = vi.spyOn(approvalApi, "resolve").mockResolvedValue({
      ok: true,
      approval_id: "approval-session-one",
      decision: "approve",
    });

    render(<ApprovalBubble />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByTestId("approval-bubble")).toBeInTheDocument();

    await act(async () => {
      useSessionStore.getState().setCurrentSession("session-2");
      await Promise.resolve();
    });

    expect(screen.queryByTestId("approval-bubble")).not.toBeInTheDocument();
    expect(resolve).not.toHaveBeenCalled();
  });

});
