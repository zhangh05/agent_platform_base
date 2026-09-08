import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "../router";
import { OperationsPage } from "../pages/Operations/OperationsPage";
import { enqueue, enqueueAsync, getRequests, installMockApi, resetMocks } from "./mockServer";
import { useSessionStore } from "../stores/session";

describe("OperationsPage", () => {
  beforeEach(() => {
    resetMocks();
    installMockApi();
    useSessionStore.getState().reset();
    useSessionStore.setState({ currentWorkspaceId: "default" });
  });

  it("shows only runs attached to the selected job", async () => {
    enqueue("/jobs", {
      status: 200,
      data: {
        jobs: [{
          job_id: "job-1",
          job_type: "agent_run",
          status: "succeeded",
          title: "Job One",
          payload: { session_id: "sess-1" },
          run_ids: ["run-a"],
          created_at: "2026-07-22T08:00:00Z",
        }],
      },
    });
    enqueue("/runs/recent", {
      status: 200,
      data: {
        runs: [
          { run_id: "run-b", turn_id: "run-b", session_id: "sess-1", status: "ok", ok: true, user_input_summary: "Other session run" },
          { run_id: "run-a", turn_id: "run-a", session_id: "sess-1", status: "ok", ok: true, user_input_summary: "Job owned run" },
        ],
      },
    });

    render(<MemoryRouter initialEntries={["/runs"]}><OperationsPage /></MemoryRouter>);

    fireEvent.click(await screen.findByText("Job One"));

    expect(await screen.findByText("Job owned run")).toBeInTheDocument();
    expect(screen.queryByText("Other session run")).not.toBeInTheDocument();
    const recentRunRequest = getRequests().find((req) => req.url === "/runs/recent");
    expect(recentRunRequest?.params).toMatchObject({ workspace_id: "default", session_id: "sess-1" });
  });

  it("loads missing job run records by run_id", async () => {
    enqueue("/jobs", {
      status: 200,
      data: {
        jobs: [{
          job_id: "job-2",
          job_type: "agent_run",
          status: "succeeded",
          title: "Job Two",
          payload: { session_id: "sess-2" },
          run_ids: ["run-missing"],
          created_at: "2026-07-22T08:00:00Z",
        }],
      },
    });
    enqueue("/runs/recent", { status: 200, data: { runs: [] } });
    enqueue("/runs/run-missing", {
      status: 200,
      data: {
        run_id: "run-missing",
        turn_id: "run-missing",
        session_id: "sess-2",
        status: "ok",
        ok: true,
        user_input_summary: "Recovered by id",
      },
    });

    render(<MemoryRouter initialEntries={["/runs"]}><OperationsPage /></MemoryRouter>);

    fireEvent.click(await screen.findByText("Job Two"));

    expect(await screen.findByText("Recovered by id")).toBeInTheDocument();
    await waitFor(() => {
      expect(getRequests().some((req) => req.url === "/runs/run-missing")).toBe(true);
    });
  });
  it("does not revive the removed audit presentation through a legacy query", async () => {
    enqueue("/jobs", { status: 200, data: { jobs: [] } });
    render(<MemoryRouter initialEntries={["/runs?view=audit"]}><OperationsPage /></MemoryRouter>);
    expect(await screen.findByText("任务中心")).toBeInTheDocument();
    expect(screen.queryByText("审计视图")).not.toBeInTheDocument();
    expect(screen.queryByText("任务中心 · 执行审计")).not.toBeInTheDocument();
  });

  it("keeps the batch-management entry visible when there are no terminal tasks", async () => {
    enqueue("/jobs", { status: 200, data: { jobs: [] } });
    render(<MemoryRouter initialEntries={["/runs"]}><OperationsPage /></MemoryRouter>);

    expect(await screen.findByLabelText("批量任务管理")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除已选 (0)" })).toBeDisabled();
    expect(screen.getByText("当前没有可选择的终态任务。发起任务后，可在此勾选并批量永久删除。")).toBeInTheDocument();
  });

  it("ignores a late run response from the previously selected job", async () => {
    let resolveFirst!: (value: { status: number; data: unknown }) => void;
    enqueue("/jobs", { status: 200, data: { jobs: [
      { job_id: "job-a", job_type: "agent_run", status: "succeeded", title: "Job A", payload: { session_id: "sess-a" } },
      { job_id: "job-b", job_type: "agent_run", status: "succeeded", title: "Job B", payload: { session_id: "sess-b" } },
    ] } });
    enqueueAsync("/runs/recent", new Promise((resolve) => { resolveFirst = resolve; }));
    enqueue("/runs/recent", { status: 200, data: { runs: [
      { run_id: "run-b", session_id: "sess-b", status: "ok", ok: true, user_input_summary: "Current B run" },
    ] } });

    render(<MemoryRouter initialEntries={["/runs"]}><OperationsPage /></MemoryRouter>);
    fireEvent.click(await screen.findByText("Job A"));
    fireEvent.click(screen.getByText("Job B"));
    expect(await screen.findByText("Current B run")).toBeInTheDocument();

    resolveFirst({ status: 200, data: { runs: [
      { run_id: "run-a", session_id: "sess-a", status: "ok", ok: true, user_input_summary: "Stale A run" },
    ] } });
    await waitFor(() => expect(screen.queryByText("Stale A run")).not.toBeInTheDocument());
    expect(screen.getByText("Current B run")).toBeInTheDocument();
  });

  it("keeps a deep-linked job open when the jobs list refreshes", async () => {
    const job = { job_id: "job-deep", job_type: "agent_run", status: "succeeded", title: "Deep Job", payload: { session_id: "sess-deep" } };
    enqueue("/jobs", { status: 200, data: { jobs: [job] } });
    enqueue("/runs/recent", { status: 200, data: { runs: [
      { run_id: "run-deep", session_id: "sess-deep", status: "ok", ok: true, user_input_summary: "Deep linked run" },
    ] } });
    render(<MemoryRouter initialEntries={["/runs?job=job-deep"]}><OperationsPage /></MemoryRouter>);
    expect(await screen.findByText("Deep linked run")).toBeInTheDocument();

    enqueue("/jobs", { status: 200, data: { jobs: [{ ...job, updated_at: "2026-09-05T00:00:00Z" }] } });
    enqueue("/runs/recent", { status: 200, data: { runs: [
      { run_id: "run-deep", session_id: "sess-deep", status: "ok", ok: true, user_input_summary: "Deep linked run" },
    ] } });
    window.dispatchEvent(new CustomEvent("lzcore:run-completed"));

    await waitFor(() => expect(screen.getByText("Deep linked run")).toBeInTheDocument());
  });

  it("bulk-deletes the selected terminal tasks in one request", async () => {
    enqueue("/jobs", { status: 200, data: { jobs: [
      { job_id: "job-terminal-a", job_type: "agent_run", status: "succeeded", title: "Terminal A" },
      { job_id: "job-running", job_type: "agent_run", status: "running", title: "Running" },
      { job_id: "job-terminal-b", job_type: "agent_run", status: "failed", title: "Terminal B" },
    ] } });
    enqueue("/jobs/batch-delete", { status: 200, data: { ok: true, deleted: true, job_ids: ["job-terminal-a", "job-terminal-b"] } });
    enqueue("/jobs", { status: 200, data: { jobs: [{ job_id: "job-running", job_type: "agent_run", status: "running", title: "Running" }] } });
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));

    render(<MemoryRouter initialEntries={["/runs"]}><OperationsPage /></MemoryRouter>);
    fireEvent.click(await screen.findByLabelText("选择任务 Terminal A"));
    fireEvent.click(screen.getByLabelText("选择任务 Terminal B"));
    fireEvent.click(screen.getByRole("button", { name: "删除已选 (2)" }));

    await waitFor(() => {
      const request = getRequests().find((item) => item.url === "/jobs/batch-delete");
      expect(request?.method).toBe("DELETE");
      expect(request?.data).toMatchObject({
        workspace_id: "default",
        job_ids: ["job-terminal-a", "job-terminal-b"],
        confirmation: "DELETE JOBS job-terminal-a,job-terminal-b",
      });
    });
    expect(screen.queryByLabelText("选择任务 Running")).not.toBeInTheDocument();
  });
});
