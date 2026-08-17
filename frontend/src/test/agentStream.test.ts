import { describe, expect, it } from "vitest";
import { beginModelStep, canFallbackToHttp, discardToolCallDraft, finalizeStreamText, needsDurableFinalReconciliation } from "../utils/agentStream";

describe("agent stream text", () => {
  it("allows HTTP fallback only before a WebSocket turn frame is submitted", () => {
    expect(canFallbackToHttp(false)).toBe(true);
    expect(canFallbackToHttp(true)).toBe(false);
  });
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

describe("durable final reconciliation", () => {
  it("re-reads the canonical persisted assistant message only for an empty successful terminal UI", () => {
    expect(needsDurableFinalReconciliation("", [])).toBe(true);
    expect(needsDurableFinalReconciliation("  ", [])).toBe(true);
    expect(needsDurableFinalReconciliation("最终答复", [])).toBe(false);
    expect(needsDurableFinalReconciliation("", ["provider failure"])).toBe(false);
  });
});
