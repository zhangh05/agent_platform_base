import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { authApi, identityApi } from "../api";
import { OrganizationCenter } from "../pages/OrganizationCenter/OrganizationCenter";

test("explains when organization identity is not enabled", async () => {
  vi.spyOn(authApi, "status").mockResolvedValue({ ok: true, login_enabled: true, authenticated: true, username: "Admin", identity_enabled: false });
  render(<OrganizationCenter />);
  await waitFor(() => expect(screen.getByText("尚未启用组织身份模式")).toBeInTheDocument());
});

test("shows organization membership and workspace scope", async () => {
  vi.spyOn(authApi, "status").mockResolvedValue({ ok: true, login_enabled: true, authenticated: true, username: "owner", role: "owner", organization_id: "org_a", workspace_ids: ["team_a"], identity_enabled: true });
  vi.spyOn(identityApi, "organizations").mockResolvedValue({ ok: true, organizations: [{ organization_id: "org_a", name: "甲组织", workspace_ids: ["team_a"] }] });
  vi.spyOn(identityApi, "users").mockResolvedValue({ ok: true, users: [{ username: "owner", role: "owner", organization_id: "org_a", workspace_ids: ["team_a"] }] });
  vi.spyOn(identityApi, "memberships").mockResolvedValue({ ok: true, memberships: [{ username: "owner", role: "owner", organization_id: "org_a", workspace_ids: ["team_a"] }] });
  render(<OrganizationCenter />);
  await waitFor(() => expect(screen.getAllByText("甲组织")).toHaveLength(2));
  expect(screen.getByText("team_a")).toBeInTheDocument();
  expect(screen.getByText("owner（owner）")).toBeInTheDocument();
});
