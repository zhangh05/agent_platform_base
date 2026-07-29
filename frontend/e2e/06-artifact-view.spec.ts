/** E2E 6 — Data Center artifact view. */
import { test, expect } from "./fixtures";

test("6. data center artifact view", async ({ page, api }) => {
  // Discover the first workspace the UI will see.
  const wsList = await api.get("/api/workspaces");
  const wsBody = await wsList.json().catch(() => ({}));
  const wsList0 = (wsBody.workspaces ?? [])[0]?.workspace_id ?? "default";

  // Pre-seed an artifact in the same workspace.
  const r = await api.post(`/api/workspaces/${wsList0}/artifacts`, {
    data: {
      title: "e2e-artifact-view",
      artifact_type: "analysis_output",
      content: "summary: uploaded file reviewed\nstatus: draft\n",
      sensitivity: "sensitive",
    },
  });
  expect(r.status()).toBeLessThan(500);
  const seeded = await r.json().catch(() => ({}));
  expect(seeded.artifact?.artifact_id).toBeTruthy();

  await page.goto("/data");
  const wsFirst = page.locator('[data-testid^="ws-"]').first();
  await wsFirst.waitFor({ state: "visible", timeout: 8_000 });
  await wsFirst.click();

  await expect(page.getByTestId("page-data-center")).toBeVisible({ timeout: 6_000 });
  await page.getByRole("button", { name: "证据与制品", exact: true }).click();
  await expect(page.getByText("e2e-artifact-view", { exact: true })).toBeVisible({ timeout: 6_000 });
});
