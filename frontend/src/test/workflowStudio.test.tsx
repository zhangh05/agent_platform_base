import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { extensionsApi, toolsApi, workflowsApi, workflowTemplatesApi } from "../api";
import { WorkflowStudio } from "../pages/WorkflowStudio/WorkflowStudio";

const template = {
  template_id: "network-operations-asset-inventory",
  name: "网络资产清单核对",
  description: "读取当前工作区已登记的网络设备资产。",
  audience: "网络运维",
  expected_result: "返回已登记资产列表。",
  input_example: {},
};

const workflow = {
  workflow_id: "network-operations-asset-inventory-20260821",
  name: "网络资产清单核对",
  description: template.description,
  version: 1,
  status: "active" as const,
  failure_policy: "fail_fast" as const,
  nodes: [{ node_id: "list_assets", name: "读取网络资产", tool_id: "network.operations.assets_read", arguments: { action: "list" }, depends_on: [] }],
};

test("creates a user-facing workflow from a business template", async () => {
  vi.spyOn(workflowsApi, "list").mockResolvedValue({ ok: true, workflows: [] });
  vi.spyOn(toolsApi, "catalog").mockResolvedValue({ tools: [{ tool_id: "network.operations.assets_read", canonical_tool_id: "network.operations.assets_read", category: "ops", group: "network", action: "read", display_name: "读取网络资产", risk_level: "low", requires_approval: false, enabled: true, callable_by_llm: true }], categories: [], count: 1 });
  vi.spyOn(extensionsApi, "list").mockResolvedValue({ ok: true, count: 1, extensions: [{ extension_id: "network.operations", name: "网络巡检", version: "1.2.0", description: "", capabilities: [], tools: [], frontend_routes: [] }] });
  vi.spyOn(workflowTemplatesApi, "list").mockResolvedValue({ templates: [template] });
  const instantiate = vi.spyOn(workflowTemplatesApi, "instantiate").mockResolvedValue({ workflow, template });

  render(<WorkflowStudio />);
  expect(await screen.findByText("网络资产清单核对")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "创建并打开" }));

  await waitFor(() => expect(instantiate).toHaveBeenCalledWith("default", "network-operations-asset-inventory"));
  expect(screen.getByDisplayValue("网络资产清单核对")).toBeInTheDocument();
  expect(screen.getByPlaceholderText("步骤名称")).toHaveValue("读取网络资产");
  expect(screen.getByRole("option", { name: "读取网络资产" })).toBeInTheDocument();
});
