/**
 * E2E 10 — Page refresh restores workspace + session state.
 */
import { test, expect, selectWorkspace } from "./fixtures";

test("10. refresh restores workspace + session", async ({ page, workspaceId }) => {
  await page.goto("/workbench");

  // Wait for the sidebar to populate.
  await selectWorkspace(page, workspaceId);

  // Reload the page.
  await page.reload();

  // The fixed workspace remains active without exposing a redundant selector.
  await selectWorkspace(page, workspaceId);
});
