/** E2E 14 — extension and workflow control-plane contracts. */
import { test, expect } from "./fixtures";

test("14. extensions and workflows use the isolated workspace", async ({ api, workspaceId }) => {
  const extensions = await api.get("/api/extensions");
  expect(extensions.ok()).toBeTruthy();
  const workflows = await api.get(`/api/workflows?workspace_id=${workspaceId}`);
  expect(workflows.ok()).toBeTruthy();
});
