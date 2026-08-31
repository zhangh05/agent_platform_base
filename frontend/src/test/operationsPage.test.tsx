import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "../router";
import { OperationsPage } from "../pages/Operations/OperationsPage";
import { enqueue, getRequests, installMockApi, resetMocks } from "./mockServer";
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
});
