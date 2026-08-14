import { afterEach, describe, expect, it } from "vitest";
import { getApiAccessToken, realtimeEndpoint } from "../api/client";

describe("realtime endpoint and browser credential boundary", () => {
  afterEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  it("derives a secure websocket endpoint from the current API origin", () => {
    expect(realtimeEndpoint("/ws/agent", "https://api.example.test/edge")).toBe("wss://api.example.test/ws/agent");
  });

  it("uses only session-scoped browser API tokens", () => {
    window.localStorage.setItem("LZCORE_API_TOKEN", "legacy-token");
    expect(getApiAccessToken()).not.toBe("legacy-token");
    window.sessionStorage.setItem("LZCORE_API_TOKEN", "session-token");
    expect(getApiAccessToken()).toBe("session-token");
  });
});
