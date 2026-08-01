import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { extensionsApi } from "../api";
import { ExtensionRegistryProvider, useExtensionRegistry } from "../extensions/registry";

function Probe() {
  const registry = useExtensionRegistry();
  return <div>{registry.ready ? registry.navItems.map((item) => item.label).join(",") : "loading"}</div>;
}

test("builds navigation from an installed bundled extension", async () => {
  vi.spyOn(extensionsApi, "list").mockResolvedValue({
    ok: true,
    count: 1,
    extensions: [{
      extension_id: "reference.insights",
      name: "文本洞察示例扩展",
      version: "1.0.0",
      description: "",
      capabilities: ["text_insights"],
      tools: ["reference.insights.summarize"],
      frontend_routes: [{
        path: "/extensions/reference.insights/overview",
        module: "frontend/ReferenceInsights.tsx",
        label: "扩展示例",
      }],
    }],
  });
  render(<ExtensionRegistryProvider><Probe /></ExtensionRegistryProvider>);
  await waitFor(() => expect(screen.getByText("扩展示例")).toBeInTheDocument());
});
