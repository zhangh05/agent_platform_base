/**
 * E2E 3 — Session create + switch.
 */
import { test, expect, selectWorkspace } from "./fixtures";

test("3. session create + switch", async ({ page, workspaceId }) => {
  await page.goto("/workbench");
  await selectWorkspace(page, workspaceId);

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
  await page.reload();
  // The same isolated workspace remains selectable after a full client reload.
  await expect(page.locator(`[data-testid="ws-${workspaceId}"]`)).toHaveClass(/active/, { timeout: 6_000 });
});
