/** E2E 14 — approval API plus extension and workflow control-plane pages. */
import { test, expect, selectWorkspace } from "./fixtures";

test("14. approvals, extensions and workflows use the isolated workspace", async ({ page, api, workspaceId }) => {
  const pending = await api.get(`/api/agent/approvals/pending?workspace_id=${workspaceId}`);
  expect(pending.ok()).toBeTruthy();
  const pendingBody = await pending.json();
  expect(Array.isArray(pendingBody.approvals ?? pendingBody.pending ?? [])).toBeTruthy();

  const extensions = await api.get("/api/extensions");
  expect(extensions.ok()).toBeTruthy();
  const workflows = await api.get(`/api/workflows?workspace_id=${workspaceId}`);
  expect(workflows.ok()).toBeTruthy();

  await page.goto("/workbench");
  await selectWorkspace(page, workspaceId);
  await page.goto("/extensions");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.goto("/workflows");
  await expect(page.getByRole("heading", { name: /应用编排/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "新建流程" })).toBeVisible();
});
