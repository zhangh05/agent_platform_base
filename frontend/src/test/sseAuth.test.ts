import { afterEach, describe, expect, it, vi } from "vitest";
import { openSSE } from "../api/sse";

describe("authenticated SSE transport", () => {
  afterEach(() => {
    window.localStorage.removeItem("NA_API_TOKEN");
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("uses an Authorization header and never places the API token in the URL", async () => {
    window.localStorage.setItem("NA_API_TOKEN", "platform-secret-token");
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(controller) { streamController = controller; },
    });
    const fetchMock = vi.fn().mockResolvedValue(new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const connection = openSSE("/agent/sse/stream/session-1?workspace_id=default");
    await Promise.resolve();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).not.toContain("access_token");
    expect(url).not.toContain("platform-secret-token");
    expect((options.headers as Record<string, string>).Authorization).toBe("Bearer platform-secret-token");
    expect(options.credentials).toBe("include");

    connection.close();
    streamController.close();
  });

  it("parses named multiline events over fetch streaming", async () => {
    window.localStorage.setItem("NA_API_TOKEN", "token");
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode("id: 42\nevent: turn_completed\ndata: first\ndata: second\n\n"));
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    })));
    const connection = openSSE("/agent/sse/stream/session-1?workspace_id=default");
    const received = new Promise<MessageEvent<string>>((resolve) => {
      connection.addEventListener("turn_completed", (event) => resolve(event as MessageEvent<string>));
    });

    const event = await received;
    expect(event.data).toBe("first\nsecond");
    expect(event.lastEventId).toBe("42");
    connection.close();
  });

  it("stops reconnecting when a static API token is rejected", async () => {
    vi.useFakeTimers();
    window.localStorage.setItem("NA_API_TOKEN", "expired-token");
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    const connection = openSSE("/agent/approvals/sse?workspace_id=default");
    const error = new Promise<Event>((resolve) => { connection.onerror = resolve; });
    await error;
    await vi.advanceTimersByTimeAsync(60_000);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    connection.close();
    vi.useRealTimers();
  });

  it("rejects a non-SSE response instead of buffering an HTML error page", async () => {
    vi.useFakeTimers();
    window.localStorage.setItem("NA_API_TOKEN", "token");
    const fetchMock = vi.fn().mockResolvedValue(new Response("<html>proxy error</html>", {
      status: 200,
      headers: { "Content-Type": "text/html" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const connection = openSSE("/agent/sse/stream/session-1?workspace_id=default");
    const error = new Promise<Event>((resolve) => { connection.onerror = resolve; });
    await error;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    connection.close();
    vi.useRealTimers();
  });

  it("reconnects with the last event id and the server retry interval", async () => {
    vi.useFakeTimers();
    window.localStorage.setItem("NA_API_TOKEN", "token");
    const encoder = new TextEncoder();
    const first = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode("id: 99\nretry: 250\ndata: first\n\n"));
        controller.close();
      },
    });
    let secondController!: ReadableStreamDefaultController<Uint8Array>;
    const second = new ReadableStream<Uint8Array>({
      start(controller) { secondController = controller; },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(first, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }))
      .mockResolvedValueOnce(new Response(second, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    const connection = openSSE("/agent/sse/stream/session-1?workspace_id=default");
    const received = new Promise<MessageEvent<string>>((resolve) => {
      connection.onmessage = resolve;
    });
    expect((await received).data).toBe("first");
    await vi.advanceTimersByTimeAsync(250);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const options = fetchMock.mock.calls[1][1] as RequestInit;
    expect((options.headers as Record<string, string>)["Last-Event-ID"]).toBe("99");
    connection.close();
    secondController.close();
    vi.useRealTimers();
  });
});
