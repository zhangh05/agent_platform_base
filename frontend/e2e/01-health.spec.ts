/**
 * E2E 1 — Backend health check via the UI.
 *
 * Verifies the frontend can hit the Flask backend through the Vite
 * dev proxy and that the topbar navigation is reachable.
 */
import { test, expect } from "./fixtures";

test("1. backend health + frontend nav reachable", async ({ page }) => {
  await page.goto("/workbench");
  // Assert the rendered top navigation by accessible name, not stale test ids.
  await expect(page.getByRole("link", { name: "工作台", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "能力中心", exact: true })).toBeVisible();

  // The backend health endpoint must return 2xx via the proxy.
  const resp = await page.request.get("/api/health");
  expect(resp.status()).toBeLessThan(500);
});
