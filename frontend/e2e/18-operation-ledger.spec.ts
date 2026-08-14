import { test, expect, selectWorkspace } from "./fixtures";

test("18. diagnostics exposes a read-only operation ledger through the real admin API", async ({ page, workspaceId }) => {
  await page.goto("/diagnostics");
  await selectWorkspace(page, workspaceId);
  await page.getByRole("button", { name: /开始检测|重新检测/ }).click();

  const panel = page.getByTestId("operation-ledger-panel");
  await expect(panel).toBeVisible({ timeout: 12_000 });
  await expect(panel).toContainText("结果未知");
  await expect(page.getByRole("button", { name: /重试|重放/ })).toHaveCount(0);
});
