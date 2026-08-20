/**
 * E2E 2 — Agent message full closed loop.
 *
 * Verifies the explicit-session boundary and one UI message round trip.
 */
import { test, expect, selectWorkspace } from "./fixtures";

test("2. agent message closed loop", async ({ page, workspaceId }) => {
  await page.addInitScript(() => {
    class UnavailableWebSocket { constructor() { throw new Error("e2e websocket unavailable"); } }
    Object.defineProperty(window, "WebSocket", { value: UnavailableWebSocket, configurable: true });
  });
  await page.route("**/api/agent/message**", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true, final_response: "hello received", events: [],
        trace_id: "trace-e2e", session_id: body.session_id,
        turn_id: "turn-e2e", tool_calls: [], warnings: [], errors: [],
        metadata: { execution_outcome: "complete", tool_execution_outcome: "complete" },
      }),
    });
  });
  await page.goto("/workbench");

  // Pick a workspace if Sidebar hasn't auto-selected one.
  // Wait for sidebar workspace list to load.
  await selectWorkspace(page, workspaceId);

  const input = page.getByTestId("chat-input");
  await expect(input).toBeDisabled();
  await page.getByTestId("btn-new-session").click();
  await expect(input).toBeEnabled();
  await input.fill("hello");
  await page.getByTestId("btn-send").click();

  await expect(page.getByTestId("chat-user").filter({ hasText: "hello" })).toBeVisible();
  await expect(page.getByTestId("chat-assistant").filter({ hasText: "hello received" })).toBeVisible();
});
