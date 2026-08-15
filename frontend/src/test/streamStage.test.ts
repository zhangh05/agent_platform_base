import { describe, expect, it } from "vitest";
import { progressPatchForStreamStage, stageElapsedSince } from "../utils/streamStage";

describe("stream stage progress semantics", () => {
  it("does not let a heartbeat overwrite the active stage or fabricate stage timing", () => {
    expect(progressPatchForStreamStage("heartbeat", { turn_elapsed_ms: 2100 })).toBeNull();
  });

  it("uses distinct real stage and turn elapsed fields", () => {
    expect(progressPatchForStreamStage("model_started", {
      turn_elapsed_ms: 2100,
      stage_elapsed_ms: 100,
    })).toEqual({
      progressText: "正在调用模型…",
      progressElapsedMs: 2100,
      stageElapsedMs: 100,
    });
  });

  it("ticks a real stage locally without letting heartbeats replace its identity", () => {
    expect(stageElapsedSince(1_000, 3_100)).toBe(2_100);
    expect(stageElapsedSince(null, 3_100)).toBeUndefined();
  });

  it("does not substitute turn duration for missing stage duration", () => {
    expect(progressPatchForStreamStage("planner_started", {
      turn_elapsed_ms: 2100,
    })).toEqual({
      progressText: "正在分析任务…",
      progressElapsedMs: 2100,
      stageElapsedMs: undefined,
    });
  });
});
