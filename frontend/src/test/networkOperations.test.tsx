import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, vi } from "vitest";
import NetworkOperations from "../../../extensions/network_operations/frontend/NetworkOperations";
import { apiRequest } from "../api/client";
import { ConfirmHost } from "../components/ConfirmDialog";

vi.mock("../api/client", () => ({ apiRequest: vi.fn() }));
const tools = ["network.operations.device.manage"];
const skill = { skill_id: "s1", name: "测试巡检", description: "", enabled: true, device_ids: ["d1"], connection_ids: ["c1"], allowed_tool_ids: tools };

beforeEach(() => {
  vi.mocked(apiRequest).mockImplementation(async (request) => {
    if (request.method !== "GET") return { ok: true } as never;
    if (request.url?.endsWith("/regions")) return { regions: [{ region_id: "r1", name: "测试区域" }] } as never;
    if (request.url?.endsWith("/devices")) return { devices: [{ device_id: "d1", name: "CE_1", host: "127.0.0.1", vendor: "h3c", region_id: "r1" }] } as never;
    if (request.url?.endsWith("/connections")) return { connections: [{ connection_id: "c1", device_id: "d1", protocol: "telnet", port: 30001, credential_configured: true, status: "untested", verified: false }] } as never;
    if (request.url?.endsWith("/context")) return {
      observations: [{ observation_id: "o1", source_id: "inspection-1", observed_at: "2026-09-06T00:00:00Z", completeness: "complete", target_ids: ["c1"] }],
      references: [{ reference_id: "ref1", name: "巡检候选参考", state: "candidate", authority: "observed", current: false, completeness: "complete", target_ids: ["c1"], updated_at: "2026-09-06T00:00:00Z" }],
      command_experience: [{ experience_id: "e1", connection_id: "c1", driver_id: "h3c.comware", command: "display version", status: "accepted", observations: 1, last_observed_at: "2026-09-06T00:00:00Z" }],
      sources: [{ source_id: "live_cli", kind: "live_observation", available: true, authority: "observed" }],
    } as never;
    return { skills: [skill] } as never;
  });
});

test("device inventory is first; editors are on demand and search works", async () => {
  render(<NetworkOperations />);
  await screen.findByTestId("device-card-d1");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("搜索设备"), { target: { value: "absent" } });
  expect(screen.getByText("没有匹配的设备")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("搜索设备"), { target: { value: "CE_1" } });
  fireEvent.click(screen.getByRole("button", { name: "编辑设备" }));
  expect(screen.getByRole("dialog", { name: "设备编辑面板" })).toBeInTheDocument();
  expect(screen.getByLabelText("设备名称")).toHaveValue("CE_1");
  fireEvent.click(screen.getByRole("button", { name: "关闭" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("published Skill has device configuration capability by default", async () => {
  render(<NetworkOperations />);
  await screen.findByTestId("device-card-d1");
  fireEvent.click(screen.getByRole("button", { name: /Skill 配置/ }));
  expect(screen.getByText("可执行设备配置")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "编辑 Skill" }));
  const dialog = screen.getByRole("dialog", { name: "Skill 编辑面板" });
  expect(within(dialog).queryByText(/实时设备只读操作/)).not.toBeInTheDocument();
  expect(within(dialog).queryByRole("checkbox", { name: /允许配置写入/ })).not.toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "保存 Skill" }));
  await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(expect.objectContaining({
    method: "PUT", data: expect.objectContaining({ allowed_tool_ids: tools }),
  })));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

test("operational context separates observations from explicitly confirmed references", async () => {
  render(<><NetworkOperations /><ConfirmHost /></>);
  await screen.findByTestId("device-card-d1");
  fireEvent.click(screen.getByRole("button", { name: /环境与证据/ }));
  expect(screen.getByText("巡检候选参考")).toBeInTheDocument();
  expect(screen.getByText(/巡检只产生候选参考/)).toBeInTheDocument();
  expect(screen.getByText("display version")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "确认参考" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "确认参考" }));
  await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(expect.objectContaining({
    method: "POST", url: "/extensions/network.operations/references/ref1", data: expect.objectContaining({ action: "confirm" }),
  })));
});

test("operational context exposes confirmed hard deletes", async () => {
  render(<><NetworkOperations /><ConfirmHost /></>);
  await screen.findByTestId("device-card-d1");
  fireEvent.click(screen.getByRole("button", { name: /环境与证据/ }));
  fireEvent.click(screen.getByRole("button", { name: "永久删除观察 inspection-1" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "永久删除" }));
  await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(expect.objectContaining({
    method: "DELETE", url: "/extensions/network.operations/observations/o1", data: { workspace_id: "default" },
  })));
  fireEvent.click(screen.getByRole("button", { name: "永久删除命令反馈 display version" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "永久删除" }));
  await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(expect.objectContaining({
    method: "DELETE", url: "/extensions/network.operations/command-experience/e1", data: { workspace_id: "default" },
  })));
});

test("context loading failure does not hide device and Skill management", async () => {
  const original = vi.mocked(apiRequest).getMockImplementation();
  vi.mocked(apiRequest).mockImplementation(async (request) => {
    if (request.url?.endsWith("/context")) throw new Error("context unavailable");
    return original?.(request) as never;
  });
  render(<NetworkOperations />);
  expect(await screen.findByTestId("device-card-d1")).toBeInTheDocument();
  expect(screen.queryByText("数据加载失败，请检查服务。")).not.toBeInTheDocument();
});
