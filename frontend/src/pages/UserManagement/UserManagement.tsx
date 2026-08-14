import { useCallback, useEffect, useState } from "react";
import {
  authApi,
  identityApi,
  type AuthStatus,
  type IdentityUser,
  type OrganizationRecord,
} from "../../api";

const ROLE_OPTIONS = [
  { value: "viewer", label: "只读用户", note: "可查看数据，不能修改数据" },
  { value: "operator", label: "执行用户", note: "可运行任务和流程，不能编辑流程" },
  { value: "developer", label: "开发用户", note: "可配置流程并使用开发能力" },
];

const FIXED_WORKSPACE_ID = "default";

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
  workspace_ids: [FIXED_WORKSPACE_ID],
  enabled: true,
});

export function UserManagement() {
  const [session, setSession] = useState<AuthStatus | null>(null);
  const [users, setUsers] = useState<IdentityUser[]>([]);
  const [organizations, setOrganizations] = useState<OrganizationRecord[]>([]);
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
    const [userData, organizationData] = await Promise.all([
      identityApi.users(),
      identityApi.organizations(),
    ]);
    setUsers(userData.users || []);
    setOrganizations(organizationData.organizations || []);
  }, []);

  useEffect(() => {
    load().catch(() => setError("用户与权限数据读取失败"));
  }, [load]);

  function selectUser(user: IdentityUser) {
    setCreating(false);
    setSelectedUsername(user.username);
    setDraft({
      username: user.username,
      password: "",
      role: user.role,
      organization_id: user.organization_id || "default",
      workspace_ids: [FIXED_WORKSPACE_ID],
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
        workspace_ids: [FIXED_WORKSPACE_ID],
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

  async function removeSelectedUser() {
    const username = selectedUsername || draft.username.trim();
    if (!username || !window.confirm(`删除普通用户「${username}」？\n\n账号将无法再登录，历史会话和产物会保留用于审计。`)) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await identityApi.deleteUser(username);
      await load();
      setCreating(false);
      setSelectedUsername("");
      setDraft(emptyDraft(organizations[0]?.organization_id || "default"));
      setNotice(`用户 ${username} 已删除，历史数据已保留`);
    } catch (err) {
      setError(String((err as { message?: string })?.message || "删除失败，请稍后重试"));
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
      <div><h1>用户与权限 <span>User Access</span></h1><p className="subtitle">由管理员统一创建普通用户，并控制角色和账户状态。</p></div>
      <button className="btn primary" onClick={startCreate}>新建用户</button>
    </header>
    <div className="page-body">
      {error ? <div className="extension-center-error" role="alert">{error}</div> : null}
      {notice ? <div className="user-management-notice" role="status">{notice}</div> : null}
      <section className="user-access-summary">
        <div><b>{users.length}</b><span>普通用户</span></div>
        <div><b>{enabledCount}</b><span>已启用</span></div>
        <article><span className="user-avatar admin">A</span><div><b>{session?.username || "Admin"}</b><small>平台管理员 · 权限不可被普通用户修改</small></div></article>
      </section>
      <div className="user-access-layout">
        <aside className="user-access-list">
          <div className="user-access-list-title"><h2>普通用户</h2><span>{users.length}</span></div>
          {users.length ? users.map((user) => <button key={user.username} className={selectedUsername === user.username ? "selected" : ""} onClick={() => selectUser(user)}>
            <span className={`user-status-dot ${user.enabled === false ? "disabled" : ""}`} />
            <span><b>{user.username}</b><small>{ROLE_OPTIONS.find((item) => item.value === user.role)?.label || user.role}</small></span>
          </button>) : <div className="user-access-empty"><b>还没有普通用户</b><p>点击“新建用户”添加第一个账户。</p></div>}
        </aside>
        <main className="user-access-editor">
          {!creating && !selectedUsername ? <div className="user-access-placeholder"><span>👤</span><h2>选择一个用户</h2><p>在左侧选择用户查看和修改权限，或新建普通用户。</p></div> : <>
            <div className="user-access-editor-head"><div><span>{creating ? "创建普通用户" : "编辑用户权限"}</span><h2>{creating ? "新用户" : draft.username}</h2></div>{!creating ? <label className="user-enabled-switch"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /><span>{draft.enabled ? "账户已启用" : "账户已停用"}</span></label> : null}</div>
            <section className="user-access-section">
              <div className="user-access-section-title"><h3>账户信息</h3><p>用户名创建后不可修改；编辑时密码留空表示保持原密码。</p></div>
              <div className="user-access-fields"><label><span>用户名</span><input className="input" value={draft.username} disabled={!creating} onChange={(event) => setDraft({ ...draft, username: event.target.value })} placeholder="例如 zhangsan" /></label><label><span>{creating ? "初始密码" : "重置密码（可选）"}</span><input className="input" type="password" value={draft.password} onChange={(event) => setDraft({ ...draft, password: event.target.value })} autoComplete="new-password" /></label><label><span>所属组织</span><select className="input" value={draft.organization_id} onChange={(event) => setDraft({ ...draft, organization_id: event.target.value, workspace_ids: [FIXED_WORKSPACE_ID] })}>{organizations.map((item) => <option key={item.organization_id} value={item.organization_id}>{item.name}</option>)}</select></label></div>
            </section>
            <section className="user-access-section">
              <div className="user-access-section-title"><h3>角色权限</h3><p>{selectedRole?.note}</p></div>
              <div className="user-role-options">{ROLE_OPTIONS.map((option) => <label key={option.value} className={draft.role === option.value ? "selected" : ""}><input type="radio" name="user-role" value={option.value} checked={draft.role === option.value} onChange={() => setDraft({ ...draft, role: option.value })} /><span><b>{option.label}</b><small>{option.note}</small></span></label>)}</div>
            </section>
            <div className="user-access-actions">{!creating ? <button className="btn danger" disabled={busy} onClick={() => void removeSelectedUser()}>删除用户</button> : null}<button className="btn primary" disabled={busy || !draft.username.trim() || (creating && !draft.password)} onClick={() => void save()}>{busy ? "保存中…" : creating ? "创建用户" : "保存权限"}</button></div>
          </>}
        </main>
      </div>
    </div>
  </div>;
}
