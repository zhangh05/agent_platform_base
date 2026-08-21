import { describe, expect, it } from "vitest";
import { NAV_ITEMS, buildNavGroups } from "../config/nav";

describe("navigation simplification", () => {
  it("keeps daily product areas in the default grouped navigation", () => {
    const primary = NAV_ITEMS.filter((item) => !item.advanced && !item.utility);
    const groups = buildNavGroups(primary);
    expect(primary.map((item) => item.to)).toEqual([
      "/workbench", "/runs", "/capabilities", "/knowledge", "/data",
      "/memory", "/diagnostics",
    ]);
    expect(groups.find((group) => group.id === "tasks")?.items.map((item) => item.to)).toEqual(["/runs"]);
    expect(groups.find((group) => group.id === "capabilities")?.items.map((item) => item.to)).toEqual(["/capabilities"]);
    expect(groups.find((group) => group.id === "system")?.items.map((item) => item.to)).toEqual(["/diagnostics"]);
  });

  it("keeps governance and build routes reachable through the advanced page", () => {
    const advanced = NAV_ITEMS.filter((item) => item.advanced);
    expect(advanced.map((item) => item.to)).toEqual([
      "/reviews", "/extensions", "/workflows",
    ]);
    expect(buildNavGroups(advanced).flatMap((group) => group.items)).toHaveLength(3);
  });
  it("keeps settings and user access together in the compact settings menu", () => {
    expect(NAV_ITEMS.filter((item) => item.utility === "settings").map((item) => item.to)).toEqual([
      "/settings", "/users",
    ]);
  });
});
