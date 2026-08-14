/** E2E 13 — isolated SSE and WebSocket connect, close, and reconnect. */
import { test, expect } from "./fixtures";

test("13. realtime streams authenticate and reconnect within the isolated workspace", async ({ page, api, workspaceId, browser }) => {
  const created = await api.post("/api/sessions", {
    data: { workspace_id: workspaceId, title: "e2e realtime reconnect" },
  });
  expect(created.ok()).toBeTruthy();
  const body = await created.json();
  const sessionId = body.session.session_id as string;

  await page.goto("/workbench");
  const result = await page.evaluate(async ({ sessionId, workspaceId }) => {
    const sseConnect = () => new Promise<string>((resolve, reject) => {
      const source = new EventSource(`/api/agent/sse/stream/${sessionId}?workspace_id=${workspaceId}`);
      const timer = window.setTimeout(() => { source.close(); reject(new Error("SSE connect timeout")); }, 5_000);
      source.addEventListener("connected", () => { window.clearTimeout(timer); source.close(); resolve("connected"); }, { once: true });
      source.onerror = () => { window.clearTimeout(timer); source.close(); reject(new Error("SSE connection error")); };
    });
    const wsConnect = () => new Promise<string>((resolve, reject) => {
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${protocol}://${location.host}/ws/agent`);
      const timer = window.setTimeout(() => { socket.close(); reject(new Error("WebSocket connect timeout")); }, 5_000);
      socket.onopen = () => socket.send(JSON.stringify({ type: "ping", workspace_id: workspaceId }));
      socket.onmessage = (event) => {
        const frame = JSON.parse(String(event.data));
        if (frame.type === "pong") { window.clearTimeout(timer); socket.close(); resolve("pong"); }
      };
      socket.onerror = () => { window.clearTimeout(timer); socket.close(); reject(new Error("WebSocket connection error")); };
    });
    return { sse: [await sseConnect(), await sseConnect()], ws: [await wsConnect(), await wsConnect()] };
  }, { sessionId, workspaceId });

  expect(result.sse).toEqual(["connected", "connected"]);
  expect(result.ws).toEqual(["pong", "pong"]);

  const anonymous = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const anonymousPage = await anonymous.newPage();
  await anonymousPage.goto("/workbench");
  const denied = await anonymousPage.evaluate(async (workspaceId) => new Promise<string>((resolve, reject) => {
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${location.host}/ws/agent`);
    const timer = window.setTimeout(() => { socket.close(); reject(new Error("anonymous WebSocket timeout")); }, 5_000);
    socket.onopen = () => socket.send(JSON.stringify({ type: "ping", workspace_id: workspaceId }));
    socket.onmessage = (event) => {
      const frame = JSON.parse(String(event.data));
      if (frame.type === "error") {
        window.clearTimeout(timer);
        socket.close();
        resolve(frame.message);
      }
    };
  }), workspaceId);
  expect(denied).toBe("unauthorized");
  await anonymous.close();
});

test("13b. API-token SSE uses an Authorization header and a credential-free URL", async ({ browser }) => {
  const apiToken = process.env.E2E_API_TOKEN ?? "";
  expect(apiToken).not.toBe("");
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  await context.addInitScript((token) => {
    window.localStorage.setItem("LZCORE_API_TOKEN", token);
  }, apiToken);
  const tokenPage = await context.newPage();
  const streamRequest = tokenPage.waitForRequest(
    (request) => request.url().includes("/api/agent/approvals/sse"),
  );
  await tokenPage.goto("/workbench");
  await tokenPage.evaluate(async () => {
    const modulePath = "/src/api/sse.ts";
    const transport = await import(/* @vite-ignore */ modulePath) as {
      openSSE: (path: string) => { close(): void };
    };
    const connection = transport.openSSE("/agent/approvals/sse?workspace_id=default");
    window.setTimeout(() => connection.close(), 1_000);
  });
  const request = await streamRequest;

  expect(request.url()).not.toContain("access_token");
  expect(request.url()).not.toContain(apiToken);
  expect(request.headers()["authorization"]).toBe(`Bearer ${apiToken}`);
  await context.close();
});
