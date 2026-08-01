import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { extensionsApi, toolsApi, workflowsApi } from "../api";
import { WorkflowStudio } from "../pages/WorkflowStudio/WorkflowStudio";

test("shows applications and creates a clear workflow draft", async () => {
  vi.spyOn(workflowsApi, "list").mockResolvedValue({ ok: true, workflows: [] });
  vi.spyOn(toolsApi, "catalog").mockResolvedValue({ tools: [{ tool_id: "text.analyze", canonical_tool_id: "text.analyze", category: "text", group: "text", action: "read", display_name: "文本分析", risk_level: "low", requires_approval: false, enabled: true, callable_by_llm: true }], categories: [], count: 1 });
  vi.spyOn(extensionsApi, "list").mockResolvedValue({ ok: true, count: 1, extensions: [{ extension_id: "reference.insights", name: "文本洞察", version: "1.0.0", description: "", capabilities: [], tools: [], frontend_routes: [] }] });
  render(<WorkflowStudio />);
  await waitFor(() => expect(screen.getByText("文本洞察")).toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: "新建流程" }));
  expect(screen.getByDisplayValue("新流程")).toBeInTheDocument();
  expect(screen.getByDisplayValue("第一步")).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "文本分析" })).toBeInTheDocument();
});
