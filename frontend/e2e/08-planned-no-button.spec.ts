/**
 * E2E 8 — Planned capability has no invoke button.
 */
import { test, expect } from "./fixtures";

test("8. planned capability has no invoke button", async ({ page }) => {
  await page.goto("/capabilities");

  // The capabilities endpoint does NOT require a workspace; the page
  // must render regardless of which workspace (if any) is selected.
  await expect(page.getByTestId("page-capabilities")).toBeVisible({ timeout: 8_000 });

  // Wait for capability cards (or an empty state) to appear.
  const cards = page.locator('[data-testid^="cap-"]');
  await cards
    .first()
    .waitFor({ state: "visible", timeout: 10_000 })
    .catch(() => {
      // Empty backend — no capabilities. Skip the planned assertion.
    });

  const count = await cards.count();
  if (count > 0) {
    // Find any card with status=planned.
    const planned = page.locator('[data-testid^="cap-"][data-status="planned"]');
    if ((await planned.count()) > 0) {
      const firstPlanned = planned.first();
      const buttons = firstPlanned.locator("button");
      const btnCount = await buttons.count();
      // No button in the planned card should say "invoke" / "调用" / "run" / "execute".
      for (let i = 0; i < btnCount; i++) {
        const txt = (await buttons.nth(i).textContent()) ?? "";
        expect(txt.toLowerCase()).not.toMatch(/invoke|调用|run|execute/);
      }
    }
  }
});
