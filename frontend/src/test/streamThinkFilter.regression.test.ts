import { describe, expect, it } from "vitest";
import { filterStreamingThink } from "../utils/displayText";

function visibleChunks(chunks: string[]): string {
  const state = { mode: "idle" as const };
  return chunks.map((chunk) => filterStreamingThink(chunk, state)).join("");
}

describe("streaming think filter regression", () => {
  it("hides reasoning when think tags cross token boundaries", () => {
    expect(visibleChunks(["<thi", "nk>secret", "</thi", "nk>final"])).toBe("final");
  });

  it("keeps visible text before a split opening tag", () => {
    expect(visibleChunks(["回答：<th", "ink>reason", "</think>结论"])).toBe("回答：结论");
  });

  it("keeps normal content when a bracket is not a think tag", () => {
    expect(visibleChunks(["比较 <three> 与 <thinker>"])).toBe("比较 <three> 与 <thinker>");
  });

  it("hides a complete think block in one provider chunk", () => {
    expect(visibleChunks(["<think>reason</think>answer"])).toBe("answer");
  });
});

describe("streaming reasoning tag compatibility", () => {
  it("hides reasoning tags split across provider chunks", () => {
    expect(visibleChunks(["<reas", "oning>private", "</reason", "ing>answer"])).toBe("answer");
  });
});
