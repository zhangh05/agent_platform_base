import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { extensionsApi, toolsApi, workflowsApi, workflowTemplatesApi } from "../api";
import { WorkflowStudio } from "../pages/WorkflowStudio/WorkflowStudio";
vi.mock("../api/client", () => ({ apiRequest: vi.fn().mockResolvedValue({ assets: [] }) }));

const template = { template_id: "network-operations-asset-inventory", name: "网络资产清单核对", description: "读取当前工作区已登记的网络设备资产。", audience: "网络运维", expected_result: "返回已登记资产列表。", input_example: {} };
const workflow = { workflow_id: "network-operations-asset-inventory-20260821", name: "网络资产清单核对", description: template.description, version: 1, status: "active" as const, failure_policy: "fail_fast" as const, nodes: [{ node_id: "list_assets", name: "读取网络资产", tool_id: "network.operations.assets_read", arguments: { action: "list" }, depends_on: [] }] };

test("keeps the default workflow page compact and opens templates only on demand", async () => {
  vi.spyOn(workflowsApi, "list").mockResolvedValueOnce({ ok: true, workflows: [] }).mockResolvedValue({ ok: true, workflows: [workflow] });
  vi.spyOn(toolsApi, "catalog").mockResolvedValue({ tools: [{ tool_id: "network.operations.assets_read", canonical_tool_id: "network.operations.assets_read", category: "ops", group: "network", action: "read", display_name: "读取网络资产", risk_level: "low", requires_approval: false, enabled: true, callable_by_llm: true }], categories: [], count: 1 });
  vi.spyOn(extensionsApi, "list").mockResolvedValue({ ok: true, count: 1, extensions: [] });
  vi.spyOn(workflowTemplatesApi, "list").mockResolvedValue({ templates: [template] });
  const instantiate = vi.spyOn(workflowTemplatesApi, "instantiate").mockResolvedValue({ workflow, template });
  render(<WorkflowStudio />);
  expect(await screen.findByRole("heading", { name: "流程" })).toBeInTheDocument();
  expect(screen.queryByText("选择要完成的工作：")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "新建流程" }));
  expect(screen.getByText("选择要完成的工作：")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /网络资产清单核对/ }));
  await waitFor(() => expect(instantiate).toHaveBeenCalledWith("default", "network-operations-asset-inventory"));
  expect(screen.getByRole("heading", { name: "网络资产清单核对" })).toBeInTheDocument();
  expect(screen.getByText("此流程不需要填写额外参数。")).toBeInTheDocument();
});
