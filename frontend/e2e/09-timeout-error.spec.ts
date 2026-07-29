/**
 * E2E 9 — Provider timeout surfaces error state.
 *
 * v1.0.1 fix: 前端 axios 默认 timeout 从 12s → 30s (TIMEOUTS.default).
 *
 * 测试策略: 用 Playwright route 立即返回 HTTP 408 (Request Timeout),
 * 后端语义和「请求挂起到 client timeout」等价 — 都走 toApiError 里
 * code === "timeout" 的分支, 最终错误信息也是 "请求超时".
 *
 * 这样既能验证前端对 timeout 的渲染, 又不需要 e2e 跑 30s+.
 */
import { test, expect } from "./fixtures";

test("9. provider timeout surfaces error state", async ({ page, api }) => {
  // 直接返回 408: toApiError 看到 status 408 → code 置为 "timeout",
  // 并把 body.message 透传出去 (此处塞 "请求超时" 满足断言).
  await page.route("**/api/knowledge/search**", async (route) => {
    await route.fulfill({
      status: 408,
      contentType: "application/json",
      body: JSON.stringify({ message: "请求超时: provider too slow" }),
    });
  });

  await page.goto("/knowledge");
  const wsFirst = page.locator('[data-testid^="ws-"]').first();
  await wsFirst.waitFor({ state: "visible", timeout: 8_000 });
  await wsFirst.click();

  // Trigger the search.
  await page.getByTestId("knowledge-search-input").fill("OSPF");
  await page.getByTestId("btn-knowledge-search").click();

  // Error state should render immediately.
  const err = page.getByTestId("knowledge-search-error");
  await expect(err).toBeVisible({ timeout: 5_000 });
  await expect(err).toContainText("请求超时");
});
