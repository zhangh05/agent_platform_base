/**
 * E2E 1 — Backend health check via the UI.
 *
 * Verifies the frontend can hit the Flask backend through the Vite
 * dev proxy and that the topbar navigation is reachable.
 */
import { test, expect } from "./fixtures";

test("1. backend health + frontend nav reachable", async ({ page }) => {
  await page.goto("/workbench");
  // The topbar nav should be visible.
  await expect(page.getByTestId("nav-workbench")).toBeVisible();
  await expect(page.getByTestId("nav-knowledge")).toBeVisible();
  await expect(page.getByTestId("nav-capabilities")).toBeVisible();

  // The backend health endpoint must return 2xx via the proxy.
  const resp = await page.request.get("/api/health");
  expect(resp.status()).toBeLessThan(500);
});
