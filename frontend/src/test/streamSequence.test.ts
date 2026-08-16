import { describe, expect, it } from "vitest";
import { decideStreamFrame } from "../utils/streamSequence";

describe("stream sequence guard", () => {
  it("rejects duplicate and stale live frames", () => {
    expect(decideStreamFrame({ type: "event", seq: 4 }, 4, false)).toEqual({
      accept: false,
      nextSequence: 4,
    });
    expect(decideStreamFrame({ type: "token", seq: 3 }, 4, false)).toEqual({
      accept: false,
      nextSequence: 4,
    });
    expect(decideStreamFrame({ type: "event", seq: 5 }, 4, false)).toEqual({
      accept: true,
      nextSequence: 5,
    });
  });

  it("accepts a done frame that mirrors the last live sequence", () => {
    expect(decideStreamFrame({ type: "done", stream_seq: 5 }, 5, false)).toEqual({
      accept: true,
      nextSequence: 5,
    });
    expect(decideStreamFrame({ type: "done", stream_seq: 4 }, 5, false)).toEqual({
      accept: false,
      nextSequence: 5,
    });
  });

  it("keeps unsequenced backward-compatible frames and rejects post-terminal frames", () => {
    expect(decideStreamFrame({ type: "event" }, 5, false)).toEqual({
      accept: true,
      nextSequence: 5,
    });
    expect(decideStreamFrame({ type: "token", seq: 6 }, 5, true)).toEqual({
      accept: false,
      nextSequence: 5,
    });
  });
});
