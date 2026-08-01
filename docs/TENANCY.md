# Organization-level tenancy

Identity mode now models users, organizations, memberships, and organization-owned
workspaces. Workspace ownership is unique: a workspace cannot be assigned to two
organizations. Renaming or deleting a workspace updates organization, membership,
and user access records together.

Enable the control plane with:

```bash
export AGENT_PLATFORM_IDENTITY_ENABLED=true
export AGENT_PLATFORM_SESSION_SECRET='...'
export AGENT_PLATFORM_MASTER_KEY='...'
```

The configured environment-login account remains the platform bootstrap. An
identity user with the `owner` role can also manage all organizations. A regular
organization administrator can manage only users and memberships in the current
organization and cannot create another organization.

Roles are ordered as `viewer < operator < developer < admin < owner`. Unlike the
old single-tenant shortcut, `admin` no longer bypasses workspace membership.
Administrators receive all workspaces owned by their organization; lower roles
receive only explicitly assigned workspaces. API tokens remain platform-level
service credentials and should be restricted and rotated accordingly.

Useful endpoints:

- `GET/POST /api/identity/organizations`
- `GET/POST /api/identity/organizations/<id>/memberships`
- `GET/POST /api/identity/users`
- `GET/POST /api/workspaces`

`GET /api/auth/status` exposes only the current safe session projection: role,
organization ID, accessible workspace IDs, whether identity mode is active, and
whether the session is a platform administrator. The 组织与成员 page uses this
projection to hide platform-only controls and explain how to enable identity mode
when the deployment is still using single-account login.
