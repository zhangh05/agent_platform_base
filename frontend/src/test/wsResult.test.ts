import { describe, expect, it } from "vitest";
import { agentResultFromWsDone } from "../utils/wsResult";

describe("agentResultFromWsDone", () => {
  it("preserves full inspector fields from WebSocket done payload", () => {
    const result = agentResultFromWsDone(
      {
        final_response: "回答完成",
        session_id: "s-ws",
        turn_id: "t-ws",
        trace_id: "trace-ws",
        events: [
          { event_id: "ev-1", event_type: "tool_call", type: "tool_call", timestamp: 1 },
          { event_id: "ev-2", event_type: "final", type: "final", timestamp: 2 },
        ],
        tool_calls: [
          {
            call_id: "call-1",
            tool_id: "knowledge.manage",
            ok: true,
            source_count: 1,
            result: { source_summary: [{ title: "本机 ifconfig 资料" }] },
          },
        ],
        metadata: {
          visible_tools: ["knowledge.manage"],
          source_count: 1,
        },
        tool_decision: { needed: true, selected_tools: ["knowledge.manage"] },
        no_tool_reason: "",
      },
      "",
      "fallback-session",
    );

    expect(result.trace_id).toBe("trace-ws");
    expect(result.events).toHaveLength(2);
    expect(result.tool_calls).toHaveLength(1);
    expect(result.metadata.source_count).toBe(1);
    expect(result.tool_decision?.selected_tools).toEqual(["knowledge.manage"]);
  });

  it("never creates an empty assistant message", () => {
    const result = agentResultFromWsDone({}, "", "session-1");
    expect(result.final_response).toBe("未收到可显示的模型回复，请重试。");
  });
});
