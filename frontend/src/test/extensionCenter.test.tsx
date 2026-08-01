import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { extensionsApi } from "../api";
import { ExtensionCenter } from "../pages/ExtensionCenter/ExtensionCenter";

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
