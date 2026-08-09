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

The configured `Admin` environment-login account is the protected platform
administrator. It is the only account exposed by the product for user and access
management. The backend keeps `admin` and `owner` roles readable for migration
compatibility, but the management API creates ordinary users only.

Ordinary users have three assignable roles: `viewer`, `operator`, and `developer`.
`Admin` explicitly grants one or more logical workspaces and the first grant is the
user's default workspace. Business data is then isolated by the composite key
`(username, workspace_id)`: including `Admin`, two users in the same workspace do
not share sessions, runs, memory, knowledge, artifacts, files, or data-center
results. Frontend session selection, conversation cache,
drafts, and diagnostics use the same user/workspace scope. Accounts can be disabled
without deleting their data, and access changes do not require a password reset. API
tokens remain platform-level service credentials and should be restricted and rotated.

Useful endpoints:

- `GET/POST /api/identity/organizations`
- `GET/POST /api/identity/organizations/<id>/memberships`
- `GET/POST /api/identity/users`
- `PUT /api/identity/users/<username>`
- `GET/POST /api/workspaces`

`GET /api/auth/status` exposes only the current safe session projection: role,
organization ID, accessible workspace IDs, whether identity mode is active, and
whether the session is a platform administrator. The 用户与权限 page and its route
chunk are available only to that administrator; ordinary users are redirected to
the workbench and the same restriction is enforced again by the backend API.
