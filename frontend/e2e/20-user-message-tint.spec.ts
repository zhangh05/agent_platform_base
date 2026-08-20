/** E2E 20 — User input remains visually distinct from assistant output. */
import { test, expect } from "./fixtures";

test("20. user message uses the low-saturation green tint", async ({ page, api }) => {
  const created = await api.post("/api/sessions", {
    data: { workspace_id: "default", title: "user tint regression" },
  });
  expect(created.ok()).toBeTruthy();
  const createdBody = await created.json();
  const sessionId = String(createdBody.session?.session_id ?? "");
  expect(sessionId).toBeTruthy();

  await page.route("**/api/agent/message**", async (route) => {
    const request = JSON.parse(route.request().postData() || "{}");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        final_response: "已收到。",
        events: [],
        trace_id: "trace-user-tint",
        session_id: request.session_id || "",
        turn_id: "turn-user-tint",
        tool_calls: [],
        warnings: [],
        errors: [],
        metadata: { source_count: 0, source_summary: [] },
      }),
    });
  });

  await page.goto("/workbench");
  const session = page.getByTestId(`sess-${sessionId}`);
  await expect(session).toBeVisible();
  await session.click();
  const input = page.getByTestId("chat-input");
  await expect(input).toBeVisible();
  await input.fill("浅绿色用户消息");
  await page.getByTestId("btn-send").click();

  const userBubble = page.getByTestId("chat-user").last().locator(".chat-bubble.user");
  await expect(userBubble).toBeVisible();
  await expect(userBubble).toHaveCSS("background-color", "rgb(234, 243, 240)");

  const assistantBubble = page.getByTestId("chat-assistant").last().locator(".chat-bubble.assistant");
  await expect(assistantBubble).toBeVisible();
  await expect(assistantBubble).toHaveCSS("background-color", "rgb(252, 253, 252)");
});
