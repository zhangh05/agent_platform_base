import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { extensionsApi, toolsApi, workflowsApi, workflowTemplatesApi } from "../api";
import { apiRequest } from "../api/client";
import { WorkflowStudio } from "../pages/WorkflowStudio/WorkflowStudio";
vi.mock("../api/client", () => ({ apiRequest: vi.fn().mockResolvedValue({ assets: [] }) }));

const template = { template_id: "network-operations-asset-inventory", name: "网络资产清单核对", description: "读取当前工作区已登记的网络设备资产。", audience: "网络运维", expected_result: "返回已登记资产列表。", input_example: {} };
const workflow = { workflow_id: "network-operations-asset-inventory-20260821", template_id: template.template_id, name: "网络资产清单核对", description: template.description, version: 1, status: "active" as const, failure_policy: "fail_fast" as const, nodes: [{ node_id: "list_assets", name: "读取网络资产", tool_id: "network.operations.assets_read", arguments: { action: "list" }, depends_on: [] }] };

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

test("renders extension-owned workflow inputs without hardcoding extension records", async () => {
  const inspectionTemplate = {
    ...template,
    template_id: "network-operations-readonly-inspection",
    name: "批量只读巡检",
    input_fields: [{
      name: "asset_ids", label: "巡检设备", type: "multi_select" as const, required: true,
      source: { url: "/api/extensions/network.operations/assets", collection: "assets", value_field: "asset_id", label_field: "name", detail_fields: ["host"] },
    }],
  };
  const inspectionWorkflow = { ...workflow, workflow_id: "inspection-1", template_id: inspectionTemplate.template_id, name: inspectionTemplate.name };
  vi.spyOn(workflowsApi, "list").mockResolvedValue({ ok: true, workflows: [inspectionWorkflow] });
  vi.spyOn(workflowTemplatesApi, "list").mockResolvedValue({ templates: [inspectionTemplate] });
  vi.mocked(apiRequest).mockResolvedValue({ assets: [{ asset_id: "asset-1", name: "核心交换机", host: "10.0.0.1" }] });
  const run = vi.spyOn(workflowsApi, "run").mockResolvedValue({ ok: true, run: { run_id: "run-1", workflow_id: inspectionWorkflow.workflow_id, status: "queued", started_at: "2026-08-22T00:00:00Z", nodes: [] } });

  render(<WorkflowStudio />);
  await userEvent.click(await screen.findByRole("button", { name: /批量只读巡检/ }));
  await userEvent.click(await screen.findByRole("checkbox", { name: /核心交换机/ }));
  await userEvent.click(screen.getByRole("button", { name: "开始运行" }));

  await waitFor(() => expect(run).toHaveBeenCalledWith("default", "inspection-1", { asset_ids: ["asset-1"] }));
  expect(apiRequest).toHaveBeenCalledWith(expect.objectContaining({ url: "/extensions/network.operations/assets" }));
});
