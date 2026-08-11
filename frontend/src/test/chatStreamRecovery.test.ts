import { beforeEach, describe, expect, it } from "vitest";
import {
  clearInflightChatStream,
  newChatStreamId,
  readInflightChatStream,
  updateInflightChatStream,
  writeInflightChatStream,
  type InflightChatStream,
} from "../utils/chatStreamRecovery";

const record: InflightChatStream = {
  streamId: "019fefdd-dc22-7dc0-8baf-a00900000000",
  workspaceId: "default",
  sessionId: "session-1",
  scratchSessionId: "session-1",
  messageId: "message-1",
  startedAt: "2026-08-11T08:00:00.000Z",
  lastSeq: 0,
};

describe("chat stream recovery storage", () => {
  beforeEach(() => localStorage.clear());

  it("persists and advances the replay cursor", () => {
    writeInflightChatStream(record);
    updateInflightChatStream(record.streamId, { lastSeq: 17 });
    expect(readInflightChatStream()).toEqual({ ...record, lastSeq: 17 });
  });

  it("does not clear a newer stream from a stale callback", () => {
    writeInflightChatStream(record);
    clearInflightChatStream("019fefdd-dc22-7dc0-8baf-a00900000001");
    expect(readInflightChatStream()).toEqual(record);
    clearInflightChatStream(record.streamId);
    expect(readInflightChatStream()).toBeNull();
  });

  it("creates a canonical UUID stream identifier", () => {
    expect(newChatStreamId()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });
});
