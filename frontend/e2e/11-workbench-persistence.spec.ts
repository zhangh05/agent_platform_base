/**
 * E2E 10 — Workbench chat history persists across refresh (plan-C).
 *
 * 验证:
 *  1. 发一条消息 → 立刻看到用户气泡
 *  2. 模拟后端持久消息投影 (会经过 sessionsApi.messages)
 *  3. F5 刷新 → 同一个会话从后端恢复历史
 */
import { test, expect, selectWorkspace } from "./fixtures";

test("10. workbench history persists across browser refresh", async ({ page, api, workspaceId }) => {
  // Exercise the HTTP fallback deterministically; the request below is mocked
  // and the persistence assertion is transport-independent.
  await page.addInitScript(() => {
    class UnavailableWebSocket {
      constructor() { throw new Error("e2e websocket unavailable"); }
    }
    Object.defineProperty(window, "WebSocket", { value: UnavailableWebSocket, configurable: true });
  });
  const durableMessages: Array<Record<string, unknown>> = [];
  // Emulate the backend-owned durable message projection. Refresh recovery
  // must not depend on a browser-local copy.
  await page.route("**/api/sessions/**/messages**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, messages: durableMessages, count: durableMessages.length }),
    });
  });

  // 拦截 /agent/message — 直接返回 mock, 避免真实 LLM 调用耗时间
  await page.route("**/api/agent/message**", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    const turnId = `turn-${Date.now()}`;
    const createdAt = new Date().toISOString();
    durableMessages.splice(0, durableMessages.length,
      {
        message_id: `${turnId}:user`, role: "user", content: body.message || "",
        created_at: createdAt, run_id: turnId,
      },
      {
        message_id: `${turnId}:assistant`, role: "assistant", content: `echo: ${body.message || ""}`,
        created_at: createdAt, run_id: turnId, status: "ok",
      },
    );
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        final_response: `echo: ${body.message || ""}`,
        events: [
          {
            event_id: "e1",
            event_type: "turn_started",
            occurred_at: new Date().toISOString(),
            payload: {},
          },
        ],
        trace_id: `trace-${Date.now()}`,
        session_id: body.session_id || "",
        turn_id: turnId,
        tool_calls: [],
        warnings: [],
        errors: [],
        metadata: { source_count: 0, source_summary: [] },
      }),
    });
  });

  await page.goto("/workbench");
  await selectWorkspace(page, workspaceId);

  // 选第一个 session (或新建一个)
  const sessFirst = page.locator('[data-testid^="sess-"]:not([data-testid="sess-list"])').first();
  let sessionBtn: ReturnType<typeof page.locator> | null = null;
  try {
    await sessFirst.waitFor({ state: "visible", timeout: 3_000 });
    sessionBtn = sessFirst;
  } catch {
    // 没会话就新建
    await page.getByTestId("btn-new-session").click();
    await page.waitForTimeout(500);
    sessionBtn = page.locator('[data-testid^="sess-"]:not([data-testid="sess-list"])').first();
  }
  await sessionBtn.click();

  // 发送一条消息
  const input = page.getByTestId("chat-input");
  await input.waitFor({ state: "visible" });
  await input.fill("这条消息刷新后应该还在");
  await page.getByTestId("btn-send").click();

  // 等用户气泡出现
  const userMsg = page.getByTestId("chat-user").filter({ hasText: "这条消息刷新后应该还在" });
  await expect(userMsg).toBeVisible({ timeout: 5_000 });
  // 等助手回应
  const assistantMsg = page.getByTestId("chat-assistant").last();
  await expect(assistantMsg).toBeVisible({ timeout: 5_000 });
  await expect(assistantMsg).toContainText("echo:");

  // F5 刷新
  await page.reload();

  // 刷新后: 用户消息气泡仍可见
  await expect(userMsg).toBeVisible({ timeout: 8_000 });
  // 助手回应也仍在
  await expect(page.getByTestId("chat-assistant").filter({ hasText: "echo:" })).toBeVisible();
});
