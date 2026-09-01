import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { capabilitiesApi, toolsApi } from "../api";
import { CapabilityCenter } from "../pages/CapabilityCenter/CapabilityCenter";
import type { BusinessCapability, ToolCatalogItem } from "../types";

const cap = (id: string): BusinessCapability => ({
  capability_id: id,
  description: `${id} 能力说明`,
  category: "workspace",
  intent: "inspect",
  module: "workspace",
  tool_ids: [],
  risk_level: "low",
  can_create_sensitive_output: false,
  requires_verification: false,
  requires_human_review: false,
});

const tool = (id: string): ToolCatalogItem => ({
  tool_id: id,
  canonical_tool_id: id,
  category: "workspace",
  group: "read",
  action: "inspect",
  display_name: id,
  risk_level: "low",
  enabled: true,
  callable_by_llm: true,
  planner_visible: true,
  governance_status: "active",
});

test("aligns capability and tool counts with explicit wording", async () => {
  vi.spyOn(capabilitiesApi, "manifest").mockResolvedValue({
    capabilities: [cap("workspace_read"), cap("knowledge_qa")],
  });
  vi.spyOn(toolsApi, "catalog").mockResolvedValue({
    tools: [tool("workspace.read"), tool("knowledge.search"), tool("report.render")],
    categories: [{
      id: "workspace",
      name: "Workspace 工作区",
      description: "",
      count: 3,
      groups: [{
        id: "read",
        name: "Read 读取",
        count: 3,
        tools: [tool("workspace.read"), tool("knowledge.search"), tool("report.render")],
      }],
    }],
    count: 3,
    planner_visible_count: 3,
  });

  render(<CapabilityCenter />);

  await waitFor(() => expect(screen.getByRole("heading", { name: "能力中心" })).toBeInTheDocument());
  expect(screen.getByText("2 类能力")).toBeInTheDocument();
  expect(screen.getByText("3 个工具")).toBeInTheDocument();
  expect(screen.getByText("能力概览")).toBeInTheDocument();
  expect(screen.getByText("底层工具目录")).toBeInTheDocument();
  expect(screen.getByText("AI 当前可调用 3 个底层工具")).toBeInTheDocument();
});
