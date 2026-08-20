import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { list } = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock("../api", () => ({ jobsApi: { list } }));

import { useActiveTurn } from "../hooks/useActiveTurn";

type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void };
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const job = (id: string, sessionId: string) => ({
  job_id: id,
  job_type: "agent_run",
  status: "running",
  workspace_id: "ws-1",
  title: id,
  created_at: "2026-01-01T00:00:00Z",
  payload: { session_id: sessionId },
});

describe("useActiveTurn", () => {
  afterEach(() => { list.mockReset(); });

  it("ignores an older session refresh that settles after switching sessions", async () => {
    const first = deferred<{ jobs: ReturnType<typeof job>[] }>();
    const second = deferred<{ jobs: ReturnType<typeof job>[] }>();
    list.mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise);

    const { result, rerender } = renderHook(
      ({ sessionId }) => useActiveTurn("ws-1", sessionId),
      { initialProps: { sessionId: "session-a" } },
    );
    rerender({ sessionId: "session-b" });

    await act(async () => { second.resolve({ jobs: [job("job-b", "session-b")] }); });
    await waitFor(() => expect(result.current.job?.job_id).toBe("job-b"));

    await act(async () => { first.resolve({ jobs: [job("job-a", "session-a")] }); });
    await waitFor(() => expect(result.current.job?.job_id).toBe("job-b"));
  });
  it("does not let an older polling response overwrite a newer durable snapshot", async () => {
    const stale = deferred<{ jobs: ReturnType<typeof job>[] }>();
    const terminal = { ...job("job-a", "session-a"), status: "succeeded" };
    list.mockImplementationOnce(() => stale.promise).mockResolvedValueOnce({ jobs: [terminal] });

    const { result } = renderHook(() => useActiveTurn("ws-1", "session-a"));

    await act(async () => { await result.current.refresh(); });
    await waitFor(() => expect(result.current.job?.status).toBe("succeeded"));

    await act(async () => { stale.resolve({ jobs: [job("job-a", "session-a")] }); });
    await waitFor(() => expect(result.current.job?.status).toBe("succeeded"));
  });
});
