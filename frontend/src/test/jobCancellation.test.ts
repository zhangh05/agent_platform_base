import { describe, expect, it } from "vitest";
import {
  claimJobCancellation,
  jobCancellationKey,
  releaseJobCancellation,
} from "../utils/jobCancellation";

describe("job cancellation intent correlation", () => {
  it("accepts one cancellation request for the same durable turn", () => {
    const claimed = new Set<string>();
    const first = claimJobCancellation(claimed, "ws", "job", "request-1");
    const duplicate = claimJobCancellation(claimed, "ws", "job", "request-1");

    expect(first).toBe(jobCancellationKey("ws", "job", "request-1"));
    expect(duplicate).toBeNull();
    expect(claimed.size).toBe(1);
  });

  it("allows a retry after transport failure and a later client turn", () => {
    const claimed = new Set<string>();
    const first = claimJobCancellation(claimed, "ws", "job", "request-1");
    releaseJobCancellation(claimed, first!);

    expect(claimJobCancellation(claimed, "ws", "job", "request-1")).toBe(first);
    expect(claimJobCancellation(claimed, "ws", "job", "request-2")).toBe(
      jobCancellationKey("ws", "job", "request-2"),
    );
  });
});
