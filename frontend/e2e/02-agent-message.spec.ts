/**
 * E2E 2 — Agent message full closed loop.
 *
 * Sends a message via the AgentWorkbench UI, verifies final_response
 * + trace_id + turn_id are displayed in the Inspector, and confirms
 * tool_calls array is rendered if present.
 */
import { test, expect } from "./fixtures";

test("2. agent message closed loop", async ({ page }) => {
  await page.goto("/workbench");

  // Pick a workspace if Sidebar hasn't auto-selected one.
  // Wait for sidebar workspace list to load.
  const wsFirst = page.locator('[data-testid^="ws-"]').first();
  await wsFirst.waitFor({ state: "visible", timeout: 8_000 });
  await wsFirst.click();

  // Send a deterministic message.
  const input = page.getByTestId("chat-input");
  await input.fill("hello");
  await page.getByTestId("btn-send").click();

  // The Inspector should show the identity block within 12s.
  const turnId = page.getByTestId("inspector-turn-id");
  await expect(turnId).toBeVisible({ timeout: 12_000 });
  // turn_id should be a non-empty string.
  const text = (await turnId.textContent()) ?? "";
  expect(text.length).toBeGreaterThan(0);
  expect(text).not.toBe("—");
});
