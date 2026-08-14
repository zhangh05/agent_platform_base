import { describe, expect, it } from "vitest";
import type { ChatMsg } from "../stores/workbench";
import { buildTaskProgress } from "../utils/taskProgress";

function assistant(overrides: Partial<ChatMsg> = {}): ChatMsg {
  return {
    id: "a-1",
    role: "assistant",
    text: "",
    status: "streaming",
    created_at: "2026-08-14T10:00:00Z",
    ...overrides,
  };
}

describe("task progress projection", () => {
  it("maps granular runtime stages into four user-facing phases", () => {
    const model = buildTaskProgress(assistant({
      runtimeEvents: [
        { event_id: "1", event_type: "planner_completed" },
        { event_id: "2", event_type: "tool_call", tool_id: "web.search" },
      ],
    }));

    expect(model.activeIndex).toBe(1);
    expect(model.phases.map((phase) => phase.state)).toEqual(["done", "active", "idle", "idle"]);
  });

  it("shows only real tool calls as evidence entries", () => {
    const model = buildTaskProgress(assistant({
      toolCalls: [
        { tool_id: "web.search", tool_name: "搜索", ok: true, status: "done", summary: "3 个来源" },
        { tool_id: "device.inspect", tool_name: "检查", ok: false, status: "running" },
      ],
    }));

    expect(model.evidence).toHaveLength(2);
    expect(model.evidence[0]).toMatchObject({ source: "网络检索", status: "done" });
    expect(model.evidence[1]).toMatchObject({ source: "网络设备", status: "running" });
  });

  it("restores a completed durable snapshot without a streaming placeholder", () => {
    const model = buildTaskProgress(undefined, {
      session_id: "s-1",
      status: "succeeded",
      stage: "turn_completed",
      tool_calls: [{ tool_id: "knowledge.manage", status: "done", ok: true }],
    });

    expect(model.status).toBe("succeeded");
    expect(model.phases.every((phase) => phase.state === "done")).toBe(true);
    expect(model.evidence[0].source).toBe("知识库");
  });
});
