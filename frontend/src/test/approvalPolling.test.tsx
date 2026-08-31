import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApprovalBubble } from "../components/ApprovalBubble";
import { approvalApi } from "../api";
import { useSessionStore } from "../stores/session";

function pendingApproval() {
  return { ok: true, count: 1, pending: [{ approval_id: "a1", session_id: "session-1", tool_id: "network.operations.device.manage", risk_level: "high", arguments_preview: { commands: ["system-view", "return"] }, created_at: new Date().toISOString(), created_at_iso: new Date().toISOString(), expires_at: new Date(Date.now() + 1800000).toISOString(), approval_kind: "interactive", requester: "test" }] };
}

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

  it("shows permission errors instead of silently ignoring a click", async () => {
    vi.spyOn(approvalApi, "pending").mockResolvedValue(pendingApproval());
    const resolve = vi.spyOn(approvalApi, "resolve").mockRejectedValue({ status: 403, message: "approval_resolver_forbidden" });
    render(<ApprovalBubble />);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: /允许/ }));
    await act(async () => {});
    expect(screen.getByRole("alert")).toHaveTextContent("当前身份没有审批权限");
    expect(screen.getByRole("button", { name: /允许/ })).toBeEnabled();
    expect(resolve).toHaveBeenCalledTimes(1);
    expect(screen.getByText("查看完整操作参数")).toBeInTheDocument();
  });

  it("observes remote decisions and continuation snapshots after a refresh", async () => {
    const pending = vi.spyOn(approvalApi, "pending").mockResolvedValue(pendingApproval());
    const onSessionUpdate = vi.fn();
    render(<ApprovalBubble onSessionUpdate={onSessionUpdate} />);
    await act(async () => {});
    pending.mockResolvedValue({ ok: true, count: 0, pending: [], continuations: [{ continuation_id: "c1", session_id: "session-1", workspace_id: "default", parent_run_id: "r1", status: "dispatching", created_at: "", updated_at: "", approval_count: 1, decision_count: 1 }] });
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(screen.queryByTestId("approval-bubble")).not.toBeInTheDocument();
    expect(onSessionUpdate).toHaveBeenLastCalledWith(expect.objectContaining({ pendingCount: 0, continuations: [expect.objectContaining({ status: "dispatching" })] }));
  });

  it("reports a recorded decision whose continuation failed to start", async () => {
    vi.spyOn(approvalApi, "pending").mockResolvedValue(pendingApproval());
    vi.spyOn(approvalApi, "resolve").mockResolvedValue({ ok: true, approval_id: "a1", decision: "approve", runtime_result: { ok: false, error: "continuation_dispatch_unavailable" } });
    render(<ApprovalBubble />);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: /允许/ }));
    await act(async () => {});
    expect(screen.getByRole("alert")).toHaveTextContent("审批已记录，但续跑未启动");
    expect(screen.queryByRole("button", { name: /允许/ })).not.toBeInTheDocument();
  });

  it("ignores a resolve response after switching sessions", async () => {
    vi.spyOn(approvalApi, "pending").mockResolvedValue(pendingApproval());
    let finish!: (value: { ok: boolean; approval_id: string; decision: string }) => void;
    vi.spyOn(approvalApi, "resolve").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const onResolved = vi.fn();
    render(<ApprovalBubble onResolved={onResolved} />);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: /允许/ }));
    await act(async () => { useSessionStore.getState().setCurrentSession("session-2"); });
    await act(async () => { finish({ ok: true, approval_id: "a1", decision: "approve" }); });
    expect(onResolved).not.toHaveBeenCalled();
    expect(screen.queryByTestId("approval-bubble")).not.toBeInTheDocument();
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
