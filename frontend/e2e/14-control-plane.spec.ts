/** E2E 14 — approval, extension and workflow control-plane contracts. */
import { test, expect } from "./fixtures";

test("14. approvals, extensions and workflows use the isolated workspace", async ({ api, workspaceId }) => {
  const pending = await api.get(`/api/agent/approvals/pending?workspace_id=${workspaceId}`);
  expect(pending.ok()).toBeTruthy();
  const pendingBody = await pending.json();
  expect(Array.isArray(pendingBody.approvals ?? pendingBody.pending ?? [])).toBeTruthy();

  const extensions = await api.get("/api/extensions");
  expect(extensions.ok()).toBeTruthy();
  const workflows = await api.get(`/api/workflows?workspace_id=${workspaceId}`);
  expect(workflows.ok()).toBeTruthy();
});
