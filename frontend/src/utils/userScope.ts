export const ACTIVE_USER_KEY = "agent_platform_active_user";
export const ACTIVE_WORKSPACE_KEY = "agent_platform_active_workspace";

function read(key: string): string {
  try { return typeof localStorage === "undefined" ? "" : localStorage.getItem(key) || ""; } catch { return ""; }
}

export function setActiveUserScope(username: string, workspaceId = "") {
  try {
    if (username) localStorage.setItem(ACTIVE_USER_KEY, username);
    else localStorage.removeItem(ACTIVE_USER_KEY);
    if (workspaceId) localStorage.setItem(ACTIVE_WORKSPACE_KEY, workspaceId);
    else localStorage.removeItem(ACTIVE_WORKSPACE_KEY);
  } catch { /* storage may be unavailable */ }
}

export function setActiveWorkspaceScope(workspaceId: string) {
  try {
    if (workspaceId) localStorage.setItem(ACTIVE_WORKSPACE_KEY, workspaceId);
    else localStorage.removeItem(ACTIVE_WORKSPACE_KEY);
  } catch { /* storage may be unavailable */ }
}

export function activeUsername(): string {
  return read(ACTIVE_USER_KEY);
}

export function scopedLocalStorageKey(base: string, includeWorkspace = true): string {
  const username = encodeURIComponent(activeUsername() || "public");
  if (!includeWorkspace) return `${base}:${username}`;
  const workspaceId = encodeURIComponent(read(ACTIVE_WORKSPACE_KEY) || "default");
  return `${base}:${username}:${workspaceId}`;
}
