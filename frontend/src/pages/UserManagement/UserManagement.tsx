import { useCallback, useEffect, useMemo, useState } from "react";
import {
  authApi,
  identityApi,
  workspacesApi,
  type AuthStatus,
  type IdentityUser,
  type OrganizationRecord,
} from "../../api";

const ROLE_OPTIONS = [
  { value: "viewer", label: "只读用户", note: "可查看授权工作区，不能修改数据" },
  { value: "operator", label: "执行用户", note: "可运行任务和流程，不能编辑流程" },
  { value: "developer", label: "开发用户", note: "可配置流程并使用开发能力" },
];

type UserDraft = {
  username: string;
  password: string;
  role: string;
  organization_id: string;
  workspace_ids: string[];
  enabled: boolean;
};

const emptyDraft = (organizationId = "default"): UserDraft => ({
  username: "",
  password: "",
  role: "viewer",
  organization_id: organizationId,
  workspace_ids: [],
  enabled: true,
});

export function UserManagement() {
  const [session, setSession] = useState<AuthStatus | null>(null);
  const [users, setUsers] = useState<IdentityUser[]>([]);
  const [organizations, setOrganizations] = useState<OrganizationRecord[]>([]);
  const [workspaces, setWorkspaces] = useState<Array<{ workspace_id: string; name?: string }>>([]);
  const [selectedUsername, setSelectedUsername] = useState("");
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<UserDraft>(emptyDraft());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    const status = await authApi.status();
    setSession(status);
    if (!status.identity_enabled || !status.platform_admin) return;
    const [userData, organizationData, workspaceData] = await Promise.all([
      identityApi.users(),
      identityApi.organizations(),
      workspacesApi.list(),
    ]);
    setUsers(userData.users || []);
    setOrganizations(organizationData.organizations || []);
    setWorkspaces(workspaceData.workspaces || []);
  }, []);

  useEffect(() => {
    load().catch(() => setError("用户与权限数据读取失败"));
  }, [load]);

  const availableWorkspaces = useMemo(() => {
    const organization = organizations.find((item) => item.organization_id === draft.organization_id);
    const allowed = new Set(organization?.workspace_ids || []);
    return workspaces.filter((item) => allowed.has(item.workspace_id));
  }, [draft.organization_id, draft.username, organizations, workspaces]);

  function selectUser(user: IdentityUser) {
    setCreating(false);
    setSelectedUsername(user.username);
    setDraft({
      username: user.username,
      password: "",
      role: user.role,
      organization_id: user.organization_id || "default",
      workspace_ids: [...(user.workspace_ids || [])],
      enabled: user.enabled !== false,
    });
    setError("");
    setNotice("");
  }

  function startCreate() {
    const organizationId = organizations[0]?.organization_id || "default";
    setCreating(true);
    setSelectedUsername("");
    setDraft(emptyDraft(organizationId));
    setError("");
    setNotice("");
  }

  function toggleWorkspace(workspaceId: string) {
    setDraft((current) => ({
      ...current,
      workspace_ids: current.workspace_ids.includes(workspaceId)
        ? current.workspace_ids.filter((item) => item !== workspaceId)
        : [...current.workspace_ids, workspaceId],
    }));
  }

  async function save() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const payload = {
        username: draft.username.trim(),
        password: draft.password,
        role: draft.role,
        organization_id: draft.organization_id,
        workspace_ids: draft.workspace_ids,
        enabled: draft.enabled,
      };
      const result = creating
        ? await identityApi.saveUser(payload)
        : await identityApi.updateUser(draft.username, payload);
      await load();
      selectUser(result.user);
      setNotice(creating ? "用户已创建" : "权限已更新");
    } catch (err) {
      setError(String((err as { message?: string })?.message || "保存失败，请检查账户和权限设置"));
    } finally {
      setBusy(false);
    }
  }

  if (session && (!session.identity_enabled || !session.platform_admin)) {
    return <div className="page user-management"><div className="page-body"><section className="user-access-denied"><h1>无权访问用户管理</h1><p>此页面仅对平台管理员开放。</p></section></div></div>;
  }

  const enabledCount = users.filter((item) => item.enabled !== false).length;
  const selectedRole = ROLE_OPTIONS.find((item) => item.value === draft.role);

  return <div className="page user-management">
    <header className="page-header ui-page-header">
      <div><h1>用户与权限 <span>User Access</span></h1><p className="subtitle">由管理员统一创建普通用户，并控制角色、状态和可访问工作区。</p></div>
      <button className="btn primary" onClick={startCreate}>新建用户</button>
    </header>
    <div className="page-body">
      {error ? <div className="extension-center-error" role="alert">{error}</div> : null}
      {notice ? <div className="user-management-notice" role="status">{notice}</div> : null}
      <section className="user-access-summary">
        <div><b>{users.length}</b><span>普通用户</span></div>
        <div><b>{enabledCount}</b><span>已启用</span></div>
        <div><b>{workspaces.length}</b><span>可分配工作区</span></div>
        <article><span className="user-avatar admin">A</span><div><b>{session?.username || "Admin"}</b><small>平台管理员 · 权限不可被普通用户修改</small></div></article>
      </section>
      <div className="user-access-layout">
        <aside className="user-access-list">
          <div className="user-access-list-title"><h2>普通用户</h2><span>{users.length}</span></div>
          {users.length ? users.map((user) => <button key={user.username} className={selectedUsername === user.username ? "selected" : ""} onClick={() => selectUser(user)}>
            <span className={`user-status-dot ${user.enabled === false ? "disabled" : ""}`} />
            <span><b>{user.username}</b><small>{ROLE_OPTIONS.find((item) => item.value === user.role)?.label || user.role} · {user.workspace_ids.length} 个工作区</small></span>
          </button>) : <div className="user-access-empty"><b>还没有普通用户</b><p>点击“新建用户”添加第一个账户。</p></div>}
        </aside>
        <main className="user-access-editor">
          {!creating && !selectedUsername ? <div className="user-access-placeholder"><span>👤</span><h2>选择一个用户</h2><p>在左侧选择用户查看和修改权限，或新建普通用户。</p></div> : <>
            <div className="user-access-editor-head"><div><span>{creating ? "创建普通用户" : "编辑用户权限"}</span><h2>{creating ? "新用户" : draft.username}</h2></div>{!creating ? <label className="user-enabled-switch"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /><span>{draft.enabled ? "账户已启用" : "账户已停用"}</span></label> : null}</div>
            <section className="user-access-section">
              <div className="user-access-section-title"><h3>账户信息</h3><p>用户名创建后不可修改；编辑时密码留空表示保持原密码。</p></div>
              <div className="user-access-fields"><label><span>用户名</span><input className="input" value={draft.username} disabled={!creating} onChange={(event) => setDraft({ ...draft, username: event.target.value })} placeholder="例如 zhangsan" /></label><label><span>{creating ? "初始密码" : "重置密码（可选）"}</span><input className="input" type="password" value={draft.password} onChange={(event) => setDraft({ ...draft, password: event.target.value })} autoComplete="new-password" /></label><label><span>所属组织</span><select className="input" value={draft.organization_id} onChange={(event) => setDraft({ ...draft, organization_id: event.target.value, workspace_ids: [] })}>{organizations.map((item) => <option key={item.organization_id} value={item.organization_id}>{item.name}</option>)}</select></label></div>
            </section>
            <section className="user-access-section">
              <div className="user-access-section-title"><h3>角色权限</h3><p>{selectedRole?.note}</p></div>
              <div className="user-role-options">{ROLE_OPTIONS.map((option) => <label key={option.value} className={draft.role === option.value ? "selected" : ""}><input type="radio" name="user-role" value={option.value} checked={draft.role === option.value} onChange={() => setDraft({ ...draft, role: option.value })} /><span><b>{option.label}</b><small>{option.note}</small></span></label>)}</div>
            </section>
            <section className="user-access-section">
              <div className="user-access-section-title"><h3>工作区范围</h3><p>选择用户可进入的工作区；同一工作区内的数据仍按用户独立保存。</p></div>
              <div className="user-workspace-options">{availableWorkspaces.length ? availableWorkspaces.map((workspace) => <label key={workspace.workspace_id} className={draft.workspace_ids.includes(workspace.workspace_id) ? "selected" : ""}><input type="checkbox" checked={draft.workspace_ids.includes(workspace.workspace_id)} onChange={() => toggleWorkspace(workspace.workspace_id)} /><span><b>{workspace.name || workspace.workspace_id}</b><small>{workspace.workspace_id} · 数据按用户独立存储</small></span></label>) : <p>该组织暂时没有可分配工作区。</p>}</div>
            </section>
            <div className="user-access-actions"><button className="btn primary" disabled={busy || !draft.username.trim() || (creating && !draft.password) || !draft.workspace_ids.length} onClick={() => void save()}>{busy ? "保存中…" : creating ? "创建用户" : "保存权限"}</button></div>
          </>}
        </main>
      </div>
    </div>
  </div>;
}
