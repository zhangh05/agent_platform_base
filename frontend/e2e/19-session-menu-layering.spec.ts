/** E2E 19 — Session action menu must remain above following sidebar panels. */
import { test, expect } from "./fixtures";

test("19. session delete action stays topmost and clickable above recent runs", async ({ page, api }) => {
  const created = await api.post("/api/sessions", {
    data: { workspace_id: "default", title: "menu layering regression" },
  });
  expect(created.ok()).toBeTruthy();
  const sessionId = String((await created.json()).session?.session_id ?? "");
  expect(sessionId).toBeTruthy();

  await page.goto("/workbench");

  const trigger = page.getByTestId(`session-menu-trigger-${sessionId}`);
  await expect(trigger).toBeVisible();
  await trigger.click();

  const deleteAction = page.getByRole("menuitem", { name: "永久删除" });
  await expect(deleteAction).toBeVisible();
  await expect(deleteAction).toBeEnabled();

  const isTopmostHitTarget = await deleteAction.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const hit = document.elementFromPoint(
      rect.left + rect.width / 2,
      rect.top + rect.height / 2,
    );
    return hit === element || element.contains(hit);
  });
  expect(isTopmostHitTarget).toBe(true);

  const dialog = new Promise<void>((resolve) => {
    page.once("dialog", async (confirm) => {
      expect(confirm.type()).toBe("confirm");
      expect(confirm.message()).toContain("永久删除会话");
      await confirm.dismiss();
      resolve();
    });
  });
  await deleteAction.click();
  await dialog;
});
