import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { extensionsApi } from "../api";
import { ExtensionCenter } from "../pages/ExtensionCenter/ExtensionCenter";
import { ExtensionRegistryProvider } from "../extensions/registry";
import { MemoryRouter } from "../router";

test("shows installed extensions and verified repository packages", async () => {
  vi.spyOn(extensionsApi, "list").mockResolvedValue({
    ok: true,
    count: 1,
    extensions: [{
      extension_id: "reference.insights",
      name: "文本洞察",
      version: "1.0.0",
      description: "示例",
      capabilities: ["insights"],
      tools: ["reference.insights.summarize"],
      frontend_routes: [],
      source: "bundled",
    }],
  });
  vi.spyOn(extensionsApi, "repository").mockResolvedValue({
    ok: true,
    packages: [{
      extension_id: "vendor.sample",
      version: "1.1.0",
      created_at: "2026-08-02T00:00:00+00:00",
      published_at: "2026-08-02T00:00:00+00:00",
      algorithm: "ed25519",
      key_id: "1234567890abcdef",
    }],
  });

  render(<ExtensionCenter />);
  await waitFor(() => expect(screen.getByText("文本洞察")).toBeInTheDocument());
  expect(screen.getByText("vendor.sample")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "安装" })).toBeInTheDocument();
});


test("links an enabled extension to its user-facing business page", async () => {
  vi.spyOn(extensionsApi, "list").mockResolvedValue({
    ok: true,
    count: 1,
    extensions: [{
      extension_id: "network.operations",
      name: "网络巡检",
      version: "1.2.0",
      description: "管理设备、巡检和状态基线",
      capabilities: ["network_inspection"],
      tools: ["network.operations.inspection"],
      frontend_routes: [{
        path: "/extensions/network.operations/overview",
        module: "frontend/NetworkOperations.tsx",
        label: "网络巡检",
      }],
      source: "bundled",
      lifecycle: { enabled: true, status: "ready", failure_count: 0, last_error: "", updated_at: "2026-08-21T00:00:00+00:00" },
    }],
  });
  vi.spyOn(extensionsApi, "repository").mockResolvedValue({ ok: true, packages: [] });

  render(<MemoryRouter initialEntries={["/extensions"]}><ExtensionRegistryProvider><ExtensionCenter /></ExtensionRegistryProvider></MemoryRouter>);
  const link = await screen.findByRole("link", { name: "打开网络巡检" });
  expect(link).toHaveAttribute("href", "/extensions/network.operations/overview");
});

test("does not link a runtime extension whose frontend was not bundled", async () => {
  vi.spyOn(extensionsApi, "list").mockResolvedValue({
    ok: true,
    count: 1,
    extensions: [{
      extension_id: "vendor.runtime",
      name: "运行时扩展",
      version: "1.0.0",
      description: "后端已安装，前端未编入当前构建",
      capabilities: ["runtime"],
      tools: ["vendor.runtime.read"],
      frontend_routes: [{
        path: "/extensions/vendor.runtime/overview",
        module: "frontend/Overview.tsx",
        label: "运行时扩展",
      }],
      source: "installed",
      lifecycle: { enabled: true, status: "ready", failure_count: 0, last_error: "", updated_at: "2026-08-21T00:00:00+00:00" },
    }],
  });
  vi.spyOn(extensionsApi, "repository").mockResolvedValue({ ok: true, packages: [] });

  render(<MemoryRouter initialEntries={["/extensions"]}><ExtensionRegistryProvider><ExtensionCenter /></ExtensionRegistryProvider></MemoryRouter>);

  await screen.findByText("运行时扩展");
  await waitFor(() => expect(screen.getByText("该扩展的前端模块未包含在当前平台构建中，不能直接打开。")).toBeInTheDocument());
  expect(screen.queryByRole("link", { name: "打开运行时扩展" })).not.toBeInTheDocument();
});
