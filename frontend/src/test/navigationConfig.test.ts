import { describe, expect, it } from "vitest";
import { NAV_ITEMS, buildNavGroups } from "../config/nav";

describe("navigation simplification", () => {
  it("keeps daily product areas in the default grouped navigation", () => {
    const primary = NAV_ITEMS.filter((item) => !item.advanced);
    const groups = buildNavGroups(primary);
    expect(primary.map((item) => item.to)).toEqual([
      "/workbench", "/runs", "/capabilities", "/knowledge", "/data",
      "/memory", "/diagnostics", "/settings",
    ]);
    expect(groups.find((group) => group.id === "tasks")?.items.map((item) => item.to)).toEqual(["/runs"]);
    expect(groups.find((group) => group.id === "capabilities")?.items.map((item) => item.to)).toEqual(["/capabilities"]);
    expect(groups.find((group) => group.id === "system")?.items.map((item) => item.to)).toEqual(["/diagnostics", "/settings"]);
  });

  it("keeps governance and build routes reachable only through the advanced collection", () => {
    const advanced = NAV_ITEMS.filter((item) => item.advanced);
    expect(advanced.map((item) => item.to)).toEqual([
      "/reviews", "/extensions", "/workflows", "/users",
    ]);
    expect(buildNavGroups(advanced).flatMap((group) => group.items)).toHaveLength(4);
  });
});
