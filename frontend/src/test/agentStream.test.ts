import { describe, expect, it } from "vitest";
import { beginModelStep, discardToolCallDraft, finalizeStreamText } from "../utils/agentStream";

describe("agent stream text", () => {
  it("discards text emitted by a model step that becomes a tool call", () => {
    const state = beginModelStep("旧内容");
    state.draft += "3";

    discardToolCallDraft(state);

    expect(state.draft).toBe("");
  });

  it("uses the authoritative final response instead of partial streamed text", () => {
    expect(finalizeStreamText("3\n3\n", "分析完成，共生成 120 行摘要。"))
      .toBe("分析完成，共生成 120 行摘要。");
  });
});
