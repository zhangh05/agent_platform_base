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
  const [createdResponse] = await Promise.all([
    page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/api/sessions"
      && response.request().method() === "POST"),
    newBtn.click(),
  ]);
  expect(createdResponse.ok()).toBeTruthy();
  const created = await createdResponse.json();
  const sessionId = created.session?.session_id;
  expect(sessionId).toBeTruthy();

  // A new session button should appear in the list within 8s.
  const sessList = page.getByTestId("sess-list");
  await expect(sessList).toBeVisible({ timeout: 8_000 });
  // Resolve the server-created identity, never a live positional locator that
  // may refer to a different row after the asynchronous list reload.
  const selectedSession = `sess-btn-${sessionId}`;
  const button = page.getByTestId(selectedSession);
  await expect(button).toBeVisible();
  await expect(button.locator("..")).toHaveClass(/active/);

  // Create a second session so clicking the first really exercises switching,
  // rather than simply clicking the already active newly created row.
  const [secondResponse] = await Promise.all([
    page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/api/sessions"
      && response.request().method() === "POST"),
    newBtn.click(),
  ]);
  expect(secondResponse.ok()).toBeTruthy();
  const second = await secondResponse.json();
  expect(second.session?.session_id).toBeTruthy();
  expect(second.session.session_id).not.toBe(sessionId);
  await expect(page.getByTestId(`sess-${second.session.session_id}`)).toHaveClass(/active/);
  await button.click();
  await expect(button.locator("..")).toHaveClass(/active/);
  await page.reload();
  // The product has one fixed workspace; the selected session is the state
  // that must survive a full client reload.
  await expect(page.getByTestId(String(selectedSession))).toBeVisible({ timeout: 6_000 });
  await expect(page.getByTestId(String(selectedSession)).locator("..")).toHaveClass(/active/);
});
