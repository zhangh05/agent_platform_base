/** Operational pages fill the workspace; a centered reading cap is inappropriate here. */
import { test, expect } from "./fixtures";

const routes = [
  ["/runs", ".operations-page"],
  ["/data", ".data-center"],
  ["/knowledge", ".knowledge-library"],
  ["/memory", ".memory-page"],
  ["/capabilities", ".capability-center"],
  ["/diagnostics", ".diagnostics-page"],
  ["/settings", ".settings-page"],
  ["/users", ".user-management"],
  ["/extensions/network.operations/manage", ".network-admin"],
] as const;

for (const width of [1920, 1600, 1440, 1200, 1024, 900, 760, 390]) {
  test(`21. management pages fill the workspace at ${width}px in both themes`, async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width, height: 1000 });
    const measurements = [];
    for (const theme of ["light", "dark"]) {
      for (const [route, selector] of routes) {
        await page.goto(route);
        await expect(page.locator(selector)).toBeVisible();
        if (await page.locator("html").getAttribute("data-theme") !== theme) {
          await page.getByRole("button", { name: "切换主题", exact: true }).click();
        }
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        const bounds = await page.locator(selector).evaluate((element) => {
          const main = document.querySelector("#main")!.getBoundingClientRect();
          const rect = element.getBoundingClientRect();
          const grid = element.querySelector(".network-grid")?.getBoundingClientRect();
          return {
            viewport: innerWidth,
            leftGap: rect.left - main.left,
            rightGap: main.right - rect.right,
            overflow: document.documentElement.scrollWidth - innerWidth,
            gridGap: grid ? rect.width - grid.width : 0,
          };
        });
        expect(bounds.viewport).toBe(width);
        expect(Math.abs(bounds.leftGap), `${route}: unused left workspace`).toBeLessThanOrEqual(1);
        expect(Math.abs(bounds.rightGap), `${route}: unused right workspace`).toBeLessThanOrEqual(1);
        expect(bounds.gridGap, `${route}: registry capped inside page`).toBeLessThanOrEqual(1);
        expect(bounds.overflow, `${route}: document overflow`).toBeLessThanOrEqual(1);
        measurements.push({ route, theme, ...bounds });
        if (width === 1920 || width === 390) {
          await page.screenshot({ path: testInfo.outputPath(`${route.slice(1).replaceAll("/", "-")}-${theme}.png`) });
        }
      }
    }
    await testInfo.attach("workspace-bounds", { body: JSON.stringify(measurements, null, 2), contentType: "application/json" });
  });
}
