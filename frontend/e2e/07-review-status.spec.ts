/**
 * E2E 7 — Review status update.
 */
import { test, expect, selectWorkspace } from "./fixtures";

test("7. review status list + filter", async ({ page, workspaceId }) => {
  await page.goto("/reviews");
  await selectWorkspace(page, workspaceId);

  // Page should render the review table or empty state.
  await expect(page.getByTestId("page-reviews")).toBeVisible({ timeout: 6_000 });
  // Filter buttons should be present.
  await expect(page.getByTestId("filter-pending")).toBeVisible();
  await expect(page.getByTestId("filter-all")).toBeVisible();

  // Switch filter to "all" — page should still be visible.
  await page.getByTestId("filter-all").click();
  await expect(page.getByTestId("page-reviews")).toBeVisible();
});
