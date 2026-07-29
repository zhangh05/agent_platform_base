/**
 * E2E 7 — Review status update.
 */
import { test, expect } from "./fixtures";

test("7. review status list + filter", async ({ page }) => {
  await page.goto("/reviews");
  const wsFirst = page.locator('[data-testid^="ws-"]').first();
  await wsFirst.waitFor({ state: "visible", timeout: 8_000 });
  await wsFirst.click();

  // Page should render the review table or empty state.
  await expect(page.getByTestId("page-reviews")).toBeVisible({ timeout: 6_000 });
  // Filter buttons should be present.
  await expect(page.getByTestId("filter-pending")).toBeVisible();
  await expect(page.getByTestId("filter-all")).toBeVisible();

  // Switch filter to "all" — page should still be visible.
  await page.getByTestId("filter-all").click();
  await expect(page.getByTestId("page-reviews")).toBeVisible();
});
