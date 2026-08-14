import { apiBaseURL, getApiAccessToken } from "./client";

const MAX_SSE_BUFFER_CHARS = 1024 * 1024;

class SSEHTTPError extends Error {
  constructor(readonly status: number) {
    super(`sse_http_${status}`);
  }
}

export interface SSEConnection {
  onmessage: ((event: MessageEvent<string>) => void) | null;
  onerror: ((event: Event) => void) | null;
  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void;
  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void;
  close(): void;
}

/**
 * Open an SSE stream without ever placing a credential in the URL.
 *
 * Cookie sessions use the browser-native EventSource. API-token deployments
 * use fetch streaming so Authorization remains a request header. The fetch
 * implementation preserves named events, multiline data, Last-Event-ID and
 * bounded reconnect behaviour used by the native transport.
 */
export function openSSE(path: string): SSEConnection {
  const url = `${apiBaseURL}${path.startsWith("/") ? path : `/${path}`}`;
  const token = getApiAccessToken();
  if (!token && typeof EventSource !== "undefined") {
    return new EventSource(url, { withCredentials: true });
  }
  return new FetchSSEConnection(url, token);
}

class FetchSSEConnection extends EventTarget implements SSEConnection {
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  private controller: AbortController | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;
  private retryMs = 1000;
  private lastEventId = "";

  constructor(private readonly url: string, private readonly token: string) {
    super();
    void this.connect();
  }

  close(): void {
    this.closed = true;
    this.controller?.abort();
    this.controller = null;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private async connect(): Promise<void> {
    if (this.closed) return;
    this.controller = new AbortController();
    const headers: Record<string, string> = { Accept: "text/event-stream" };
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    if (this.lastEventId) headers["Last-Event-ID"] = this.lastEventId;

    try {
      const response = await fetch(this.url, {
        method: "GET",
        credentials: "include",
        cache: "no-store",
        headers,
        signal: this.controller.signal,
      });
      if (!response.ok || !response.body) {
        throw new SSEHTTPError(response.status);
      }
      const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
      if (!contentType.includes("text/event-stream")) {
        throw new Error("sse_invalid_content_type");
      }
      this.retryMs = 1000;
      await this.consume(response.body);
      if (!this.closed) this.scheduleReconnect();
    } catch (error) {
      if (this.closed || this.controller.signal.aborted) return;
      const event = new Event("error");
      Object.defineProperty(event, "error", { value: error, enumerable: false });
      this.onerror?.(event);
      this.dispatchEvent(event);
      // A static API token cannot recover from an authentication rejection.
      // Stop the loop and let the auth/session layer create a new connection.
      if (error instanceof SSEHTTPError && (error.status === 401 || error.status === 403)) {
        this.close();
        return;
      }
      this.scheduleReconnect();
    }
  }

  private async consume(body: ReadableStream<Uint8Array>): Promise<void> {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (!this.closed) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
        if (buffer.length > MAX_SSE_BUFFER_CHARS && !buffer.includes("\n\n")) {
          throw new Error("sse_frame_too_large");
        }
        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          this.dispatchFrame(frame);
          boundary = buffer.indexOf("\n\n");
        }
        if (done) break;
      }
    } finally {
      reader.releaseLock();
    }
  }

  private dispatchFrame(frame: string): void {
    let type = "message";
    const data: string[] = [];
    for (const line of frame.split("\n")) {
      if (!line || line.startsWith(":")) continue;
      const separator = line.indexOf(":");
      const field = separator >= 0 ? line.slice(0, separator) : line;
      let value = separator >= 0 ? line.slice(separator + 1) : "";
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event" && value) type = value;
      else if (field === "data") data.push(value);
      else if (field === "id" && !value.includes("\0")) this.lastEventId = value;
      else if (field === "retry" && /^\d+$/.test(value)) {
        this.retryMs = Math.max(250, Math.min(Number(value), 30_000));
      }
    }
    if (!data.length) return;
    const event = new MessageEvent<string>(type, {
      data: data.join("\n"),
      lastEventId: this.lastEventId,
    });
    if (type === "message") this.onmessage?.(event);
    this.dispatchEvent(event);
  }

  private scheduleReconnect(): void {
    if (this.closed || this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.connect();
    }, this.retryMs);
    this.retryMs = Math.min(this.retryMs * 2, 30_000);
  }
}
