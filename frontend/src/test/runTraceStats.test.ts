import { describe, expect, it } from "vitest";
import { deriveRunTraceStats, isTraceCapabilityEvent, isTraceToolEvent } from "../utils/runTraceStats";

describe("deriveRunTraceStats", () => {
  it("derives real counters from trace events when run summary is stale", () => {
    const stats = deriveRunTraceStats(
      {
        run_id: "run-1",
        created_at: "2026-06-19T01:00:00",
        started_at: "",
        finished_at: "",
        tool_call_count: 0,
        warning_count: 0,
        error_count: 0,
        visible_tools: [],
      },
      [
        { event_id: "1", event_type: "run_started", timestamp: 1 },
        {
          event_id: "2",
          event_type: "tool_call_started",
          timestamp: 2,
          metadata: { canonical_tool_id: "host.shell.exec" },
        },
        {
          event_id: "3",
          event_type: "tool_call_failed",
          timestamp: 3,
          metadata: { canonical_tool_id: "host.shell.exec" },
        },
        { event_id: "4", event_type: "warning", timestamp: 4 },
      ],
    );

    expect(stats.toolCallCount).toBe(1);
    expect(stats.warningCount).toBe(1);
    expect(stats.errorCount).toBe(1);
    expect(stats.startedAt).toBe("1");
    expect(stats.finishedAt).toBe("4");
    expect(stats.visibleTools).toEqual(["host.shell.exec"]);
  });

  it("recognizes tool events carried in the type field", () => {
    expect(isTraceToolEvent({ event_id: "tool-1", event_type: "", type: "tool_call", tool_id: "web.manage" })).toBe(true);
    expect(isTraceToolEvent({ event_id: "tool-2", event_type: "", type: "tool_result", tool_id: "web.manage" })).toBe(true);
  });

  it("recognizes skill events for the trace filter", () => {
    expect(isTraceCapabilityEvent({ event_id: "cap-1", event_type: "", name: "capability_call" })).toBe(true);
    expect(isTraceCapabilityEvent({ event_id: "cap-2", event_type: "capability_call_start" })).toBe(true);
  });
});
