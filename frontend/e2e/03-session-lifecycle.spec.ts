/**
 * E2E 3 — Session create + switch.
 */
import { test, expect } from "./fixtures";

test("3. session create + switch", async ({ page }) => {
  await page.goto("/workbench");

  const wsFirst = page.locator('[data-testid^="ws-"]').first();
  await wsFirst.waitFor({ state: "visible", timeout: 8_000 });
  await wsFirst.click();

  // Create a session via the sidebar.
  const newBtn = page.getByTestId("btn-new-session");
  await expect(newBtn).toBeVisible();
  await newBtn.click();

  // A new session button should appear in the list within 8s.
  const sessList = page.getByTestId("sess-list");
  await expect(sessList).toBeVisible({ timeout: 8_000 });
  const sessionButtons = page.locator('[data-testid^="sess-btn-"]');
  await expect(sessionButtons.first()).toBeVisible();

  // Click the first session — currentSessionId should be set in localStorage.
  await sessionButtons.first().click();
  // Refresh and confirm the workspace is restored.
  await page.reload();
  // The workspace should still be selected after refresh.
  await expect(page.locator('[data-testid^="ws-"].active').first()).toBeVisible({ timeout: 6_000 });
});
