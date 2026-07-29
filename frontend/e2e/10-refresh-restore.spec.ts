/**
 * E2E 10 — Page refresh restores workspace + session state.
 */
import { test, expect } from "./fixtures";

test("10. refresh restores workspace + session", async ({ page }) => {
  await page.goto("/workbench");

  // Wait for the sidebar to populate.
  const wsFirst = page.locator('[data-testid^="ws-"]').first();
  await wsFirst.waitFor({ state: "visible", timeout: 8_000 });
  await wsFirst.click();

  // Verify workspace is selected (active class).
  const active = page.locator('[data-testid^="ws-"].active').first();
  await expect(active).toBeVisible({ timeout: 4_000 });

  // Capture the active workspace id.
  const activeWs = (await active.getAttribute("data-testid")) ?? "";
  expect(activeWs.startsWith("ws-")).toBe(true);

  // Reload the page.
  await page.reload();

  // After reload, the same workspace should still be active.
  const restored = page.locator(`[data-testid="${activeWs}"]`).first();
  await expect(restored).toBeVisible({ timeout: 6_000 });
  // Check it has the "active" class.
  const className = (await restored.getAttribute("class")) ?? "";
  expect(className).toContain("active");
});
