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
    const fetchMock = vi.fn().mockResolvedValue(new Response(body, { status: 200 }));
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
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 200 })));
    const connection = openSSE("/agent/sse/stream/session-1?workspace_id=default");
    const received = new Promise<MessageEvent<string>>((resolve) => {
      connection.addEventListener("turn_completed", (event) => resolve(event as MessageEvent<string>));
    });

    const event = await received;
    expect(event.data).toBe("first\nsecond");
    expect(event.lastEventId).toBe("42");
    connection.close();
  });
});
