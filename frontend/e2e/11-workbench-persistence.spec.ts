/**
 * E2E 10 — Workbench chat history persists across refresh (plan-C).
 *
 * 验证:
 *  1. 发一条消息 → 立刻看到用户气泡
 *  2. 模拟后端返回 (会经过 sessionsApi.messages)
 *  3. F5 刷新 → 同一个会话的历史仍在
 *
 * 当前 backend 有 bug: agent.run 完成后 run_id 不会被 append 到
 * session.run_ids, 所以 /api/sessions/<id>/messages 永远返回 [].
 * 但 plan-C 的 localStorage 持久化不依赖 backend — 这是
 * 核心要验证的不变量.
 */
import { test, expect } from "./fixtures";

test("10. workbench history persists across browser refresh", async ({ page, api }) => {
  // 确保 /messages 不影响测试 (返回空, localStorage 兜底)
  await page.route("**/api/sessions/**/messages**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, messages: [], count: 0 }),
    });
  });

  // 拦截 /agent/message — 直接返回 mock, 避免真实 LLM 调用耗时间
  await page.route("**/api/agent/message**", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    const turnId = `turn-${Date.now()}`;
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
  // 选第一个 workspace
  const wsFirst = page.locator('[data-testid^="ws-"]').first();
  await wsFirst.waitFor({ state: "visible", timeout: 8_000 });
  await wsFirst.click();

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
  const userMsg = page.locator('.chat-msg.user').filter({ hasText: "这条消息刷新后应该还在" });
  await expect(userMsg).toBeVisible({ timeout: 5_000 });
  // 等助手回应
  const assistantMsg = page.locator(".chat-msg.assistant").last();
  await expect(assistantMsg).toBeVisible({ timeout: 5_000 });
  await expect(assistantMsg).toContainText("echo:");

  // 检查持久化指示
  await expect(page.getByTestId("wb-persisted-indicator")).toBeVisible();

  // F5 刷新
  await page.reload();

  // 刷新后: 用户消息气泡仍可见
  await expect(userMsg).toBeVisible({ timeout: 8_000 });
  // 助手回应也仍在
  await expect(page.locator(".chat-msg.assistant").filter({ hasText: "echo:" })).toBeVisible();
  // 持久化指示仍显示
  await expect(page.getByTestId("wb-persisted-indicator")).toBeVisible();
});
