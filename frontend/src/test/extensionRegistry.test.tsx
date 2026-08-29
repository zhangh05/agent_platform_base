import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { extensionsApi } from "../api";
import { ExtensionRegistryProvider, useExtensionRegistry } from "../extensions/registry";

function Probe() {
  const registry = useExtensionRegistry();
  return <div>{registry.ready ? <><span>{registry.navItems.map((item) => item.label).join(",")}</span><span data-testid="extension-routes">{registry.routes.map((route) => route.path).join(",")}</span></> : "loading"}</div>;
}

test("builds navigation from an installed bundled extension", async () => {
  vi.spyOn(extensionsApi, "list").mockResolvedValue({
    ok: true,
    count: 1,
    extensions: [{
      extension_id: "network.operations",
      name: "网络巡检",
      version: "1.0.0",
      description: "",
      capabilities: ["network_inspection"],
      tools: ["network.operations.summarize"],
      frontend_routes: [{
        path: "/extensions/network.operations/manage",
        module: "frontend/NetworkOperations.tsx",
        label: "网络巡检",
      }],
    }],
  });
  render(<ExtensionRegistryProvider><Probe /></ExtensionRegistryProvider>);
  await waitFor(() => expect(screen.getByText("网络巡检")).toBeInTheDocument());
});

test("registers the bundled network operations route", async () => {
  vi.spyOn(extensionsApi, "list").mockResolvedValue({
    ok: true,
    count: 1,
    extensions: [{
      extension_id: "network.operations",
      name: "网络巡检",
      version: "1.2.0",
      description: "",
      capabilities: ["network_inspection"],
      tools: ["network.operations.inspection"],
      frontend_routes: [{
        path: "/extensions/network.operations/manage",
        module: "frontend/NetworkOperations.tsx",
        label: "网络巡检",
      }],
    }],
  });
  render(<ExtensionRegistryProvider><Probe /></ExtensionRegistryProvider>);
  await waitFor(() => expect(screen.getByTestId("extension-routes")).toHaveTextContent("/extensions/network.operations/manage"));
});

test("does not expose routes from a disabled extension", async () => {
  vi.spyOn(extensionsApi, "list").mockResolvedValue({
    ok: true,
    count: 1,
    extensions: [{
      extension_id: "network.operations",
      name: "网络巡检",
      version: "1.0.0",
      description: "",
      capabilities: ["network_inspection"],
      tools: ["network.operations.summarize"],
      lifecycle: { enabled: false, status: "disabled", failure_count: 0, last_error: "", updated_at: "" },
      frontend_routes: [{
        path: "/extensions/network.operations/manage",
        module: "frontend/NetworkOperations.tsx",
        label: "网络巡检",
      }],
    }],
  });
  render(<ExtensionRegistryProvider><Probe /></ExtensionRegistryProvider>);
  await waitFor(() => expect(screen.queryByText("loading")).not.toBeInTheDocument());
  expect(screen.queryByText("网络巡检")).not.toBeInTheDocument();
});
