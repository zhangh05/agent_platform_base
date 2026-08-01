import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { authApi, identityApi, workspacesApi } from "../api";
import { UserManagement } from "../pages/UserManagement/UserManagement";

test("shows the dedicated access manager to the platform administrator", async () => {
  vi.spyOn(authApi, "status").mockResolvedValue({ ok: true, login_enabled: true, authenticated: true, username: "Admin", role: "admin", organization_id: "default", workspace_ids: ["default"], identity_enabled: true, platform_admin: true });
  vi.spyOn(identityApi, "organizations").mockResolvedValue({ ok: true, organizations: [{ organization_id: "default", name: "默认组织", workspace_ids: ["default"] }] });
  vi.spyOn(identityApi, "users").mockResolvedValue({ ok: true, users: [{ username: "alice", role: "viewer", organization_id: "default", workspace_ids: ["default"], enabled: true }] });
  vi.spyOn(workspacesApi, "list").mockResolvedValue({ workspaces: [{ workspace_id: "default", name: "默认工作区", created_at: "", is_default: true, stats: { session_count: 0, artifact_count: 0, knowledge_source_count: 0 } }] });
  render(<UserManagement />);
  await waitFor(() => expect(screen.getByText("平台管理员 · 权限不可被普通用户修改")).toBeInTheDocument());
  fireEvent.click(screen.getByText("alice"));
  expect(screen.getByText("编辑用户权限")).toBeInTheDocument();
  expect(screen.getByText("只读用户")).toBeInTheDocument();
});

test("refuses to render management controls for an ordinary user", async () => {
  vi.spyOn(authApi, "status").mockResolvedValue({ ok: true, login_enabled: true, authenticated: true, username: "alice", role: "viewer", organization_id: "default", workspace_ids: ["default"], identity_enabled: true, platform_admin: false });
  render(<UserManagement />);
  await waitFor(() => expect(screen.getByText("无权访问用户管理")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "新建用户" })).not.toBeInTheDocument();
});
