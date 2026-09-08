/**
 * API layer — typed endpoint groups. Each module is a thin function layer; the page
 * components only call these functions and never call axios directly.
 * No business logic, no caching, no transformation. If a field is missing
 * from the backend response, the function returns `null`/empty list and
 * the page renders the empty / error state.
 *
 * All endpoints are aligned to the real backend contracts. v1.0.1 fix
 * pass: corrected /agent/message, /knowledge/sources/from-artifact,
 * /knowledge/search, /knowledge/chunks, /review-items, etc.
 */

import { apiRequest, TIMEOUTS } from "./client";
import { openSSE, type SSEConnection } from "./sse";
import type {
  AgentResult,
  Artifact,
  BusinessCapability,
  KnowledgeChunk,
  KnowledgeSearchResult,
  KnowledgeSource,
  RuntimeAuditTurn,
  RuntimeSummary,
  Session,
  SessionMessage,
  ToolCatalogResponse,
  Workspace,
  AppVersion,
  LlmConfig,
  LlmStatus,
  LlmTestResult,
  LlmTestRequest,
  ProviderConfig,
  ProviderListResponse,
  ProviderSaveResponse,
  ProviderActivateResponse,
  JobItem,
  JobEvent,
  MemoryRecord,
  ManagedFile,
  DataOverview,
  ArchivedDataItem,
  ToolPermission
} from "../types";

export const systemApi = {
  version: (signal?: AbortSignal): Promise<AppVersion> =>
    apiRequest<AppVersion>({ method: "GET", url: "/version" }, signal),
};

export interface ExtensionRouteDefinition {
  path: string;
  module: string;
  label: string;
  description?: string;
  icon?: string;
  order?: number;
}

export interface InstalledExtension {
  extension_id: string;
  name: string;
  version: string;
  description: string;
  capabilities: string[];
  tools: string[];
  frontend_routes: ExtensionRouteDefinition[];
  permissions?: string[];
  metadata?: { minimum_role?: string; minimum_write_role?: string; quotas?: Record<string, number> };
  lifecycle?: { enabled: boolean; status: string; failure_count: number; last_error: string; updated_at: string };
  source?: "bundled" | "installed";
}

export interface ExtensionPackageRecord {
  extension_id: string;
  version: string;
  created_at: string;
  published_at: string;
  algorithm: string;
  key_id: string;
}

export const extensionsApi = {
  list: (signal?: AbortSignal): Promise<{ ok: boolean; extensions: InstalledExtension[]; count: number }> =>
    apiRequest<{ ok: boolean; extensions: InstalledExtension[]; count: number }>(
      { method: "GET", url: "/extensions" },
      signal,
    ),
  enable: (extensionId: string) =>
    apiRequest<{ ok: boolean }>({ method: "POST", url: `/extensions/${extensionId}/enable` }),
  disable: (extensionId: string) =>
    apiRequest<{ ok: boolean }>({ method: "POST", url: `/extensions/${extensionId}/disable` }),
  migrate: (extensionId: string, workspace_id: string) =>
    apiRequest<{ ok: boolean; schema_version: number }>({ method: "POST", url: `/extensions/${extensionId}/migrate`, data: { workspace_id } }),
  repository: () =>
    apiRequest<{ ok: boolean; packages: ExtensionPackageRecord[] }>({ method: "GET", url: "/extensions/repository" }),
  publish: (file: File) => {
    const form = new FormData();
    form.append("package", file);
    return apiRequest<{ ok: boolean; package: ExtensionPackageRecord }>({ method: "POST", url: "/extensions/repository/publish", data: form });
  },
  install: (extensionId: string, version: string, upgrade: boolean) =>
    apiRequest<{ ok: boolean; restart_required: boolean }>({ method: "POST", url: `/extensions/repository/${extensionId}/${version}/install`, data: { upgrade } }),
  uninstall: (extensionId: string) =>
    apiRequest<{ ok: boolean; restart_required: boolean; recoverable_path: string }>({ method: "POST", url: `/extensions/${extensionId}/uninstall` }),
};

export interface AuthStatus {
  ok: boolean;
  login_enabled: boolean;
  authenticated: boolean;
  username: string;
  role?: string;
  organization_id?: string;
  workspace_ids?: string[];
  home_workspace_id?: string;
  identity_enabled?: boolean;
  oidc_enabled?: boolean;
  platform_admin?: boolean;
  auth_type?: "api_token" | "session" | "none";
}

export const authApi = {
  status: (signal?: AbortSignal): Promise<AuthStatus> =>
    apiRequest<AuthStatus>({ method: "GET", url: "/auth/status" }, signal),
  login: (username: string, password: string): Promise<{ ok: boolean; username: string }> =>
    apiRequest<{ ok: boolean; username: string }>({
      method: "POST",
      url: "/auth/login",
      data: { username, password },
    }),
  logout: (): Promise<{ ok: boolean }> =>
    apiRequest<{ ok: boolean }>({ method: "POST", url: "/auth/logout" }),
};

export interface WorkflowNode {
  node_id: string; name: string; tool_id: string; arguments: Record<string, unknown>;
  depends_on: string[]; when?: unknown;
}

export interface OrganizationRecord { organization_id: string; name: string; workspace_ids: string[] }
export interface IdentityUser { username: string; role: string; organization_id: string; workspace_ids: string[]; home_workspace_id?: string; enabled?: boolean }
export interface MembershipRecord { username: string; role: string; organization_id: string; workspace_ids: string[] }
export const identityApi = {
  organizations: () => apiRequest<{ ok: boolean; organizations: OrganizationRecord[] }>({ method: "GET", url: "/identity/organizations" }),
  createOrganization: (organization_id: string, name: string) => apiRequest<{ ok: boolean; organization: OrganizationRecord }>({ method: "POST", url: "/identity/organizations", data: { organization_id, name } }),
  users: () => apiRequest<{ ok: boolean; users: IdentityUser[] }>({ method: "GET", url: "/identity/users" }),
  saveUser: (user: IdentityUser & { password: string }) => apiRequest<{ ok: boolean; user: IdentityUser }>({ method: "POST", url: "/identity/users", data: user }),
  updateUser: (username: string, user: Omit<IdentityUser, "username"> & { password?: string }) => apiRequest<{ ok: boolean; user: IdentityUser }>({ method: "PUT", url: `/identity/users/${encodeURIComponent(username)}`, data: user }),
  deleteUser: (username: string) => apiRequest<{ ok: boolean; deleted: boolean; user: IdentityUser }>({ method: "DELETE", url: `/identity/users/${encodeURIComponent(username)}` }),
  memberships: (organizationId: string) => apiRequest<{ ok: boolean; memberships: MembershipRecord[] }>({ method: "GET", url: `/identity/organizations/${organizationId}/memberships` }),
};

/* ──────────────────────── 1. agent ──────────────────────── */

export interface AgentRunRequest {
  message: string;
  workspace_id: string;
  session_id?: string | null;
  metadata?: Record<string, unknown>;
}

export const agentApi = {
  /** POST /api/agent/message — SSOT Runtime endpoint.
   *  This is the SLOW endpoint (LLM + tool calls + optional web search).
   *  Expected 30-120s, 180s timeout to avoid false positives. */
  run: (req: AgentRunRequest, signal?: AbortSignal): Promise<AgentResult> =>
    apiRequest<AgentResult>(
      { method: "POST", url: "/agent/message", data: req },
      signal,
      TIMEOUTS.agentTurn,
    ),
};

export const sessionsApi = {
  list: (
    workspace_id: string,
    status?: string,
    signal?: AbortSignal,
  ): Promise<{ sessions: Session[]; counts?: Record<string, number> }> =>
    apiRequest<{ sessions: Session[]; counts?: Record<string, number> }>(
      {
        method: "GET",
        url: "/sessions",
        params: { workspace_id, status: status || "active", limit: 200 },
      },
      signal,
    ),
  get: (
    session_id: string,
    workspace_id: string,
    signal?: AbortSignal,
  ): Promise<{ session: Session; messages?: unknown[] }> =>
    apiRequest<{ session: Session; messages?: unknown[] }>(
      {
        method: "GET",
        url: `/sessions/${session_id}`,
        params: { workspace_id, include_messages: 1 },
      },
      signal,
    ),
  /**
   * GET /api/sessions/<id>/messages — chat history reconstructed from
   * durable message records. Used by the workbench for cross-device refresh.
   */
  messages: (
    session_id: string,
    workspace_id: string,
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; messages: SessionMessage[]; count: number }> =>
    apiRequest<{ ok: boolean; messages: SessionMessage[]; count: number }>(
      {
        method: "GET",
        url: `/sessions/${session_id}/messages`,
        params: { workspace_id },
      },
      signal,
    ),
  create: (
    workspace_id: string,
    title?: string,
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; session: Session }> =>
    apiRequest<{ ok: boolean; session: Session }>(
      {
        method: "POST",
        url: "/sessions",
        data: { workspace_id, title: title || "" },
      },
      signal,
    ),
  archive: (
    session_id: string,
    workspace_id: string,
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; session: Session }> =>
    apiRequest<{ ok: boolean; session: Session }>(
      {
        method: "POST",
        url: `/sessions/${session_id}/archive`,
        params: { workspace_id },
      },
      signal,
    ),
  rename: (
    session_id: string,
    workspace_id: string,
    title: string,
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; session: Session }> =>
    apiRequest<{ ok: boolean; session: Session }>(
      {
        method: "PUT",
        url: `/sessions/${session_id}`,
        params: { workspace_id },
        data: { title },
      },
      signal,
    ),
  delete: (
    session_id: string,
    workspace_id: string,
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; message?: string }> =>
    apiRequest<{ ok: boolean; message?: string }>(
      {
        method: "DELETE",
        url: `/sessions/${session_id}`,
        params: { workspace_id, confirm: "true" },
      },
      signal,
    ),
};

export const workspacesApi = {
  list: (signal?: AbortSignal): Promise<{ workspaces: Workspace[] }> =>
    apiRequest<{ workspaces: Workspace[] }>(
      { method: "GET", url: "/workspaces" },
      signal,
    ),
  get: (
    workspace_id: string,
    signal?: AbortSignal,
  ): Promise<{ workspace: Workspace }> =>
    apiRequest<{ workspace: Workspace }>(
      { method: "GET", url: `/workspaces/${workspace_id}/state` },
      signal,
    ),
  recentRuns: (
    workspace_id: string,
    session_id?: string | null,
    signal?: AbortSignal,
    limit?: number,
  ): Promise<{ runs: RuntimeAuditTurn[] }> =>
    apiRequest<{ runs: RuntimeAuditTurn[] }>(
      {
        method: "GET",
        url: "/runs/recent",
        params: session_id
          ? { workspace_id, session_id, ...(limit ? { limit } : {}) }
          : { workspace_id, session_status: "", ...(limit ? { limit } : {}) },
      },
      signal,
    ),
};

/* ──────────────────────── 3b. runtime summary ──────────────────────── */

export const runtimeApi = {
  summary: (signal?: AbortSignal): Promise<RuntimeSummary> =>
    apiRequest<RuntimeSummary>({ method: "GET", url: "/runtime/summary" }, signal),
  health: (workspace_id: string, signal?: AbortSignal) =>
    apiRequest<Record<string, unknown>>(
      { method: "GET", url: "/runtime/health", params: { workspace_id } }, signal),
  selfcheck: (workspace_id: string, signal?: AbortSignal) =>
    apiRequest<Record<string, unknown>>(
      { method: "GET", url: "/runtime/selfcheck", params: { workspace_id } }, signal),
};

export const jobsApi = {
  /** GET /api/jobs */
  list: (workspace_id: string, signal?: AbortSignal) =>
    apiRequest<{ jobs: JobItem[] }>({ method: "GET", url: "/jobs", params: { workspace_id } }, signal),

  /** GET /api/jobs/:id */
  get: (job_id: string, workspace_id: string, signal?: AbortSignal) =>
    apiRequest<{ ok: boolean; job: JobItem }>({ method: "GET", url: `/jobs/${job_id}`, params: { workspace_id } }, signal),

  /** POST /api/jobs/:id/cancel */
  cancel: (job_id: string, workspace_id: string, client_request_id?: string) =>
    apiRequest<{ ok: boolean }>({
      method: "POST",
      url: `/jobs/${job_id}/cancel`,
      data: { workspace_id, ...(client_request_id ? { client_request_id } : {}) },
    }),

  /** POST /api/jobs/:id/retry */
  retry: (job_id: string, workspace_id: string) =>
    apiRequest<{ ok: boolean }>({ method: "POST", url: `/jobs/${job_id}/retry`, data: { workspace_id } }),

  /** DELETE /api/jobs/:id — terminal task record and its event/log directory */
  delete: (job_id: string, workspace_id: string) =>
    apiRequest<{ ok: boolean; deleted: boolean }>({
      method: "DELETE",
      url: `/jobs/${job_id}`,
      data: { workspace_id, confirmation: `DELETE ${job_id}` },
    }),

  /** DELETE /api/jobs/batch-delete — permanently remove selected terminal tasks. */
  deleteMany: (job_ids: string[], workspace_id: string) => {
    const sorted = [...job_ids].sort();
    return apiRequest<{ ok: boolean; deleted: boolean; job_ids: string[] }>({
      method: "DELETE",
      url: "/jobs/batch-delete",
      data: { workspace_id, job_ids: sorted, confirmation: `DELETE JOBS ${sorted.join(",")}` },
    });
  },

  /** GET /api/jobs/:id/events */
  events: (job_id: string, workspace_id: string, signal?: AbortSignal) =>
    apiRequest<{ events: JobEvent[] }>({ method: "GET", url: `/jobs/${job_id}/events`, params: { workspace_id } }, signal),

  /** GET /api/jobs/:id/logs */
  logs: (job_id: string, workspace_id: string, signal?: AbortSignal) =>
    apiRequest<{ logs: string }>({ method: "GET", url: `/jobs/${job_id}/logs`, params: { workspace_id } }, signal),

  /** GET /api/jobs/:id/artifacts */
  artifacts: (job_id: string, workspace_id: string, signal?: AbortSignal) =>
    apiRequest<{
      input_artifacts: string[];
      output_artifacts: string[];
      report_artifacts: string[];
    }>({ method: "GET", url: `/jobs/${job_id}/artifacts`, params: { workspace_id } }, signal),
};

export const capabilitiesApi = {
  /** GET /api/capabilities — business capability catalog projection. */
  manifest: (
    signal?: AbortSignal,
  ): Promise<{ capabilities: BusinessCapability[] }> =>
    apiRequest<{ capabilities: BusinessCapability[] }>(
      { method: "GET", url: "/capabilities" },
      signal,
    ),
};

export const toolsApi = {
  catalog: (signal?: AbortSignal): Promise<ToolCatalogResponse> =>
    apiRequest<ToolCatalogResponse>({ method: "GET", url: "/tools/catalog" }, signal),
  dryRun: (data: { tool_id: string; params: Record<string, unknown>; workspace_id: string }) =>
    apiRequest<{ ok: boolean }>({
      method: "POST",
      url: "/tools/dry-run",
      params: { workspace_id: data.workspace_id },
      data: { tool_id: data.tool_id, arguments: data.params },
    }),
  permissions: (signal?: AbortSignal) =>
    apiRequest<{
      workspace_id: string;
      tools: ToolPermission[];
      forbidden_count: number;
      high_risk_count: number;
    }>({ method: "GET", url: "/tools/permissions" }, signal),
};

export const storageApi = {
  overview: (workspace_id: string, signal?: AbortSignal) =>
    apiRequest<{ ok: boolean; overview: DataOverview }>(
      { method: "GET", url: "/storage/overview", params: { workspace_id } }, signal,
    ),
  files: (workspace_id: string, lifecycle = "active", signal?: AbortSignal) =>
    apiRequest<{ ok: boolean; files: ManagedFile[]; count: number }>(
      { method: "GET", url: "/storage/files", params: { workspace_id, lifecycle } }, signal,
    ),
  content: (workspace_id: string, file_id: string, signal?: AbortSignal) =>
    apiRequest<{ ok: boolean; file_id: string; binary: boolean; content: string; truncated: boolean }>(
      { method: "GET", url: `/storage/files/${file_id}/content`, params: { workspace_id } }, signal,
    ),
  relations: (workspace_id: string, file_id: string, signal?: AbortSignal) =>
    apiRequest<{ ok: boolean; relations: { file_id: string; in_use: boolean; artifacts: Array<Record<string, unknown>>; references: Array<Record<string, unknown>> } }>(
      { method: "GET", url: `/storage/files/${file_id}/relations`, params: { workspace_id } }, signal,
    ),
  delete: (workspace_id: string, file_id: string) =>
    apiRequest<{ ok: boolean; file_id: string }>({
      method: "DELETE", url: `/storage/files/${file_id}`, params: { workspace_id, confirm: "true", force: "true" },
    }),
  events: (workspace_id: string): SSEConnection =>
    openSSE(`/storage/events?workspace_id=${encodeURIComponent(workspace_id)}`),
};

export const knowledgeApi = {
  listSources: (
    workspace_id: string,
    scope?: "workspace" | "global" | "session",
    signal?: AbortSignal,
  ): Promise<{ sources: KnowledgeSource[]; counts?: Record<string, number> }> =>
    apiRequest<{ sources: KnowledgeSource[]; counts?: Record<string, number> }>(
      { method: "GET", url: "/knowledge/sources", params: { workspace_id, scope } },
      signal,
    ),
  /**
   * POST /api/knowledge/sources/from-artifact
   * Body: { workspace_id, artifact_id } — JSON (NOT multipart).
   */
  importFromArtifact: (
    workspace_id: string,
    artifact_id: string,
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; source: KnowledgeSource }> =>
    apiRequest<{ ok: boolean; source: KnowledgeSource }>(
      {
        method: "POST",
        url: "/knowledge/sources/from-artifact",
        data: { workspace_id, artifact_id },
      },
      signal,
      TIMEOUTS.knowledgeImport,
    ),
  upload: (
    workspace_id: string,
    file: File,
    opts?: { title?: string; tags?: string; source_type?: string; scope?: string; language?: string },
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; source: KnowledgeSource; summary?: string }> => {
    const form = new FormData();
    form.append("workspace_id", workspace_id);
    form.append("file", file);
    if (opts?.title) form.append("title", opts.title);
    if (opts?.tags) form.append("tags", opts.tags);
    if (opts?.source_type) form.append("source_type", opts.source_type);
    if (opts?.scope) form.append("scope", opts.scope);
    if (opts?.language) form.append("language", opts.language);
    return apiRequest<{ ok: boolean; source: KnowledgeSource; summary?: string }>(
      {
        method: "POST",
        url: "/knowledge/upload",
        data: form,
      },
      signal,
      TIMEOUTS.knowledgeImport,
    );
  },
  reindex: (
    source_id: string,
    workspace_id: string,
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; source?: KnowledgeSource }> =>
    apiRequest<{ ok: boolean; source?: KnowledgeSource }>(
      {
        method: "POST",
        url: `/knowledge/sources/${source_id}/reindex`,
        params: { workspace_id },
      },
      signal,
    ),
  delete: (
    source_id: string,
    workspace_id: string,
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; summary: string }> =>
    apiRequest<{ ok: boolean; summary: string }>(
      {
        method: "DELETE",
        url: `/knowledge/sources/${source_id}`,
        params: { workspace_id },
      },
      signal,
    ),
  rename: (
    source_id: string,
    workspace_id: string,
    title: string,
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; source: KnowledgeSource }> =>
    apiRequest<{ ok: boolean; source: KnowledgeSource }>(
      {
        method: "PATCH",
        url: `/knowledge/sources/${source_id}`,
        data: { workspace_id, title },
      },
      signal,
    ),
  setEnabled: (
    source_id: string,
    workspace_id: string,
    enabled: boolean,
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; source: KnowledgeSource }> =>
    apiRequest<{ ok: boolean; source: KnowledgeSource }>(
      {
        method: "PATCH",
        url: `/knowledge/sources/${source_id}`,
        data: { workspace_id, enabled },
      },
      signal,
    ),
  getSource: (
    source_id: string,
    workspace_id: string,
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; source: KnowledgeSource & { chunks?: any[] } }> =>
    apiRequest<{ ok: boolean; source: KnowledgeSource & { chunks?: any[] } }>(
      {
        method: "GET",
        url: `/knowledge/sources/${source_id}`,
        params: { workspace_id },
      },
      signal,
    ),
  search: (
    q: string,
    workspace_id: string,
    opts?: { limit?: number; source_id?: string; scope?: "workspace" | "global" | "session" },
    signal?: AbortSignal,
  ): Promise<KnowledgeSearchResult> =>
    apiRequest<KnowledgeSearchResult>(
      {
        method: "GET",
        url: "/knowledge/search",
        params: {
          q,
          workspace_id,
          limit: opts?.limit ?? 20,
          source_id: opts?.source_id,
          scope: opts?.scope,
        },
      },
      signal,
    ),
  getChunk: (
    chunk_id: string,
    workspace_id: string,
    signal?: AbortSignal,
  ): Promise<{ chunk: KnowledgeChunk }> =>
    apiRequest<{ chunk: KnowledgeChunk }>(
      {
        method: "GET",
        url: `/knowledge/chunks/${chunk_id}`,
        params: { workspace_id },
      },
      signal,
    ),
};

/* ──────────────────────── 6b. memory ──────────────────────── */

export const memoryApi = {
  list: (
    params: { workspace_id: string; include_deleted?: boolean; limit?: number },
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; records: MemoryRecord[]; count?: number }> =>
    apiRequest<{ ok: boolean; records: MemoryRecord[]; count?: number }>(
      { method: "GET", url: "/memory/list", params },
      signal,
    ),

  search: (
    data: { query: string; workspace_id: string; limit?: number },
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; results: MemoryRecord[]; count?: number }> =>
    apiRequest<{ ok: boolean; results: MemoryRecord[]; count?: number }>(
      { method: "POST", url: "/memory/search", data },
      signal,
    ),

  create: (
    data: {
      title: string;
      content: string;
      workspace_id: string;
      scope?: string;
      tags?: string[];
      memory_type?: "core_rule" | "semantic_fact" | "episodic_case" | "procedural_rule" | "knowledge_note" | "profile";
      user_confirmed?: boolean;
    },
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; memory_id: string; status?: string; conflict?: boolean }> =>
    apiRequest<{ ok: boolean; memory_id: string; status?: string; conflict?: boolean }>(
      { method: "POST", url: "/memory/write", data },
      signal,
    ),

  deleteHard: (
    memoryId: string,
    workspaceId: string,
    signal?: AbortSignal,
  ): Promise<{ ok: boolean }> =>
    apiRequest<{ ok: boolean }>(
      { method: "DELETE", url: `/memory/${encodeURIComponent(memoryId)}`, params: { workspace_id: workspaceId, confirm: "true" } },
      signal,
    ),

  batchHardDelete: (
    workspaceId: string,
    memoryIds: string[],
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; deleted_count: number; requested: number }> =>
    apiRequest<{ ok: boolean; deleted_count: number; requested: number }>(
      { method: "POST", url: "/memory/batch-delete", data: { workspace_id: workspaceId, memory_ids: memoryIds, confirm: true } },
      signal,
    ),

  getProfile: (
    workspaceId: string,
    signal?: AbortSignal,
  ): Promise<unknown> =>
    apiRequest<unknown>(
      { method: "GET", url: "/memory/status", params: { workspace_id: workspaceId } },
      signal,
    ),

  setProfile: (
    data: { scope?: string; memory_type?: string; source?: string; title?: string; content?: string },
    signal?: AbortSignal,
  ): Promise<{ ok: boolean }> =>
    apiRequest<{ ok: boolean }>(
      { method: "POST", url: "/memory/write", data },
      signal,
    ),

  confirm: (
    data: {
      workspace_id: string;
      memory_id: string;
    },
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; status?: string; error?: string }> =>
    apiRequest<{ ok: boolean; status?: string; error?: string }>(
      {
        method: "POST",
        url: "/memory/confirm",
        data,
      },
      signal,
    ),

  reject: (
    data: {
      workspace_id: string;
      memory_id: string;
    },
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; status?: string; error?: string }> =>
    apiRequest<{ ok: boolean; status?: string; error?: string }>(
      {
        method: "POST",
        url: "/memory/reject",
        data,
      },
      signal,
    ),
};

export interface ArtifactGovernanceSummary {
  policy: string;
  evidence_streams: number;
  authoritative: number;
  current_state_authoritative: number;
  evidence_current: number;
  contextual: number;
  provisional: number;
  incomplete: number;
  historical: number;
  deliverables: number;
}

export const artifactsApi = {
  list: (
    workspace_id: string,
    signal?: AbortSignal,
    evidence_view: "" | "current" | "history" | "deliverables" = "",
    producer_id = "",
  ): Promise<{ artifacts: Artifact[]; governance?: ArtifactGovernanceSummary }> =>
    apiRequest<{ artifacts: Artifact[]; governance?: ArtifactGovernanceSummary }>(
      { method: "GET", url: `/workspaces/${workspace_id}/artifacts`, params: { evidence_view: evidence_view || undefined, producer_id: producer_id || undefined } },
      signal,
    ),
  /** POST /api/workspaces/<ws>/artifacts — create artifact from JSON payload. */
  create: (
    workspace_id: string,
    data: {
      content: string;
      artifact_type: string;
      title: string;
      scope?: string;
      sensitivity?: string;
      run_id?: string;
      metadata?: Record<string, unknown>;
      tags?: string[];
      source?: string;
    },
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; artifact: Artifact }> =>
    apiRequest<{ ok: boolean; artifact: Artifact }>(
      {
        method: "POST",
        url: `/workspaces/${workspace_id}/artifacts`,
        data,
      },
      signal,
    ),
  get: (
    workspace_id: string,
    artifact_id: string,
    signal?: AbortSignal,
  ): Promise<{ artifact: Artifact }> =>
    apiRequest<{ artifact: Artifact }>(
      {
        method: "GET",
        url: `/workspaces/${workspace_id}/artifacts/${artifact_id}`,
      },
      signal,
    ),
  /** GET /api/workspaces/<ws>/artifacts/<art>/content — full content (text). */
  content: (
    workspace_id: string,
    artifact_id: string,
    signal?: AbortSignal,
  ): Promise<{ content: string; metadata?: Record<string, unknown> }> =>
    apiRequest<{ content: string; metadata?: Record<string, unknown> }>(
      {
        method: "GET",
        url: `/workspaces/${workspace_id}/artifacts/${artifact_id}/content`,
      },
      signal,
    ),
  /**
   * GET /api/workspaces/<ws>/artifacts/<art>/summarize — backend summary.
   * Returns the artifact metadata plus a `summary` field if the
   * backend has computed one. Surface this in the "摘要" tab.
   */
  summarize: (
    workspace_id: string,
    artifact_id: string,
    signal?: AbortSignal,
  ): Promise<{
    ok: boolean;
    summary: {
      artifact_id: string;
      artifact_type: string;
      title: string;
      summary: string;
      sensitivity?: string;
      sha256_short?: string;
      size_bytes?: number;
      created_at?: string;
    };
  }> =>
    apiRequest<{
      ok: boolean;
      summary: {
        artifact_id: string;
        artifact_type: string;
        title: string;
        summary: string;
        sensitivity?: string;
        sha256_short?: string;
        size_bytes?: number;
        created_at?: string;
      };
    }>(
      {
        method: "GET",
        url: `/workspaces/${workspace_id}/artifacts/${artifact_id}/summarize`,
      },
      signal,
      TIMEOUTS.summarize,
    ),
  batchDelete: (
    workspace_id: string,
    artifact_ids: string[],
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; deleted: number; total: number }> =>
    apiRequest<{ ok: boolean; deleted: number; total: number }>(
      {
        method: "POST",
        url: `/workspaces/${workspace_id}/artifacts/batch-delete`,
        data: { artifact_ids, confirm: true },
      },
      signal,
    ),
};

export const runtimeAuditApi = {
    recent: (
      workspace_id: string,
      signal?: AbortSignal,
    ): Promise<{ runs: RuntimeAuditTurn[] }> =>
      apiRequest<{ runs: RuntimeAuditTurn[] }>(
        { method: "GET", url: "/runs/recent", params: { workspace_id, session_status: "" } },
        signal,
      ),
  run: (
    workspace_id: string,
    run_id: string,
    signal?: AbortSignal,
  ): Promise<unknown> =>
    apiRequest<unknown>(
      { method: "GET", url: `/runs/${run_id}`, params: { workspace_id } },
      signal,
    ),
  trace: (
    workspace_id: string,
    run_id: string,
    signal?: AbortSignal,
  ): Promise<{ events: RuntimeAuditTurn["events"] }> =>
    apiRequest<{ events: RuntimeAuditTurn["events"] }>(
      {
        method: "GET",
        url: `/workspaces/${workspace_id}/runs/${run_id}/trace`,
      },
      signal,
    ),
};

export const settingsApi = {
  llmConfig: (signal?: AbortSignal): Promise<LlmConfig> =>
    apiRequest<LlmConfig>({ method: "GET", url: "/agent/llm/config" }, signal),

  updateLlmConfig: (
    update: Partial<LlmConfig> & { clear_api_key?: boolean; api_key?: string },
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; config: LlmConfig }> =>
    apiRequest<{ ok: boolean; config: LlmConfig }>(
      { method: "POST", url: "/agent/llm/config", data: update },
      signal,
    ),

  deleteLlmConfig: (signal?: AbortSignal): Promise<{ ok: boolean; deleted: boolean }> =>
    apiRequest<{ ok: boolean; deleted: boolean }>(
      { method: "DELETE", url: "/agent/llm/config" },
      signal,
    ),

  llmStatus: (signal?: AbortSignal): Promise<LlmStatus> =>
    apiRequest<LlmStatus>({ method: "GET", url: "/agent/llm/status" }, signal),

  llmTest: (req: LlmTestRequest): Promise<LlmTestResult> => {
    const { signal, ...body } = req;
    return apiRequest<LlmTestResult>(
      {
        method: "POST",
        url: "/agent/llm/test",
        data: {
          task: body.task ?? "result_summarize",
          message: body.message ?? "ping",
          base_url: body.base_url,
          model: body.model,
          api_key: body.api_key,
          provider: body.provider,
        },
      },
      signal,
    );
  },

  // Per-provider config endpoints (v3.1.2+)
  providersList: (signal?: AbortSignal): Promise<ProviderListResponse> =>
    apiRequest<ProviderListResponse>({ method: "GET", url: "/agent/llm/providers" }, signal),

  providerGet: (providerId: string, signal?: AbortSignal): Promise<ProviderSaveResponse> =>
    apiRequest<ProviderSaveResponse>({ method: "GET", url: `/agent/llm/providers/${providerId}` }, signal),

  providerSave: (
    providerId: string,
    update: Partial<ProviderConfig> & { clear_api_key?: boolean; api_key?: string },
    signal?: AbortSignal,
  ): Promise<ProviderSaveResponse> =>
    apiRequest<ProviderSaveResponse>(
      { method: "POST", url: `/agent/llm/providers/${providerId}`, data: update },
      signal,
    ),

  providerDelete: (providerId: string, signal?: AbortSignal): Promise<{ ok: boolean; deleted: boolean }> =>
    apiRequest<{ ok: boolean; deleted: boolean }>(
      { method: "DELETE", url: `/agent/llm/providers/${providerId}` },
      signal,
    ),

  llmActivate: (
    providerId: string,
    config?: Partial<ProviderConfig> & { clear_api_key?: boolean; api_key?: string },
    signal?: AbortSignal,
  ): Promise<ProviderActivateResponse> =>
    apiRequest<ProviderActivateResponse>(
      { method: "POST", url: "/agent/llm/activate", data: { provider: providerId, ...config } },
      signal,
    ),

  // Workspace-level settings (stored in state.json)
  workspaceSettings: (wsId: string, signal?: AbortSignal): Promise<{ workspace: Record<string, unknown> }> =>
    apiRequest<{ workspace: Record<string, unknown> }>({ method: "GET", url: `/workspaces/${wsId}/state` }, signal),

  updateWorkspaceSettings: (
    patch: Record<string, string | boolean>,
    wsId: string,
  ): Promise<{ ok: boolean; workspace: Record<string, unknown> }> =>
    apiRequest<{ ok: boolean; workspace: Record<string, unknown> }>(
      { method: "PUT", url: `/workspaces/${wsId}/settings`, data: patch },
    ),
};

export type OperationLedgerSummary = {
  operation_id: string;
  turn_id: string;
  workspace_id: string;
  session_id: string;
  canonical_tool: string;
  call_id: string;
  status: "planned" | "running" | "succeeded" | "failed" | "unknown" | "blocked" | "reconciled" | string;
  risk_level?: string;
  idempotency?: string;
  error_code?: string;
  error?: string;
  result_summary?: string;
  planned_at?: string;
  updated_at?: string;
  resource_kind?: string;
  resource_id?: string;
  resolved_at?: string;
  resolved_by?: string;
  resolution_reason?: string;
};

export const operationLedgerApi = {
  list: (workspaceId: string, signal?: AbortSignal) =>
    apiRequest<{
      ok: boolean;
      operations: OperationLedgerSummary[];
      count: number;
      counts: Record<string, number>;
    }>({
      method: "GET",
      url: "/admin/operation-ledger",
      params: { workspace_id: workspaceId },
    }, signal),

  resolve: (workspaceId: string, operationId: string, status: "succeeded" | "failed", reason: string) =>
    apiRequest<{ ok: boolean; operation_id: string; status: string; resolved_by: string }>({
      method: "POST",
      url: `/admin/operation-ledger/${encodeURIComponent(operationId)}/resolve`,
      data: {
        workspace_id: workspaceId,
        status,
        reason,
        confirmation: `RESOLVE ${operationId}`,
      },
    }),
};

/* ──────────────────────── 13. system status ──────────────────────── */

export const agentUsageApi = {
  /** GET /api/agent/usage — returns flat fields (no .usage wrapper) */
  get: (workspace_id: string, signal?: AbortSignal) =>
    apiRequest<{
      ok: boolean;
      input_tokens: number;
      output_tokens: number;
      total_tokens: number;
      estimated_cost: number;
      call_count: number;
      last_updated: string;
      cache_creation_input_tokens: number;
      cache_read_input_tokens: number;
      cache_hit_ratio: number;
      prompt_cache_strategies: Record<string, number>;
      latest_prompt_profile: {
        strategy?: string;
        stable_prefix_fingerprint?: string;
        stable_prefix_estimated_tokens?: number;
        selected_skill?: boolean;
        layers?: Record<string, { estimated_tokens?: number; present?: boolean; cacheable?: boolean }>;
      };
    }>({ method: "GET", url: "/agent/usage", params: { workspace_id } }, signal),
};

export const contextApi = {
  status: (signal?: AbortSignal) =>
    apiRequest<{
      context_runtime_enabled: boolean;
      supported_refs: string[];
      default_budget: { max_items: number; max_chars: number };
    }>({ method: "GET", url: "/context/status" }, signal),

  resolve: (data: { workspace_id: string; context_ref: string }) =>
    apiRequest<unknown>({ method: "POST", url: "/context/resolve", data }),

  build: (data: { workspace_id: string; session_id: string }) =>
    apiRequest<unknown>({ method: "POST", url: "/context/build", data }),
};

export const promptsApi = {
  list: (signal?: AbortSignal) =>
    apiRequest<{ prompts: unknown[] }>({ method: "GET", url: "/prompts" }, signal),

  get: (prompt_id: string, signal?: AbortSignal) =>
    apiRequest<unknown>({ method: "GET", url: `/prompts/${prompt_id}` }, signal),

  render: (data: { prompt_id: string; variables: Record<string, string> }) =>
    apiRequest<unknown>({ method: "POST", url: "/prompts/render", data }),
};

export const retentionApi = {
  preview: (workspace_id: string, signal?: AbortSignal) =>
    apiRequest<LifecyclePreview>(
      { method: "GET", url: `/workspaces/${workspace_id}/retention/preview` },
      signal,
    ),

  apply: (workspace_id: string) =>
    apiRequest<LifecyclePreview>({
      method: "POST",
      url: `/workspaces/${workspace_id}/retention/apply`,
      data: { dry_run: false, confirm: true },
    }),

  audits: (workspace_id: string, signal?: AbortSignal) =>
    apiRequest<{ audits: unknown[] }>(
      { method: "GET", url: `/workspaces/${workspace_id}/retention/audits` },
      signal,
    ),

  auditDetail: (workspace_id: string, audit_id: string, signal?: AbortSignal) =>
    apiRequest<{ audit: unknown }>(
      { method: "GET", url: `/workspaces/${workspace_id}/retention/audits/${audit_id}` },
      signal,
    ),
};

export const archiveApi = {
  preview: (workspace_id: string, signal?: AbortSignal) =>
    apiRequest<LifecyclePreview>(
      { method: "GET", url: `/workspaces/${workspace_id}/archive/preview` },
      signal,
    ),

  apply: (workspace_id: string) =>
    apiRequest<LifecyclePreview>({
      method: "POST",
      url: `/workspaces/${workspace_id}/archive/apply`,
      data: { dry_run: false, confirm: true },
    }),

  audits: (workspace_id: string, signal?: AbortSignal) =>
    apiRequest<{ audits: unknown[] }>(
      { method: "GET", url: `/workspaces/${workspace_id}/archive/audits` },
      signal,
    ),

  auditDetail: (workspace_id: string, audit_id: string, signal?: AbortSignal) =>
    apiRequest<{ audit: unknown }>(
      { method: "GET", url: `/workspaces/${workspace_id}/archive/audits/${audit_id}` },
      signal,
    ),
  items: (workspace_id: string, signal?: AbortSignal) =>
    apiRequest<{ ok: boolean; items: ArchivedDataItem[]; count: number }>(
      { method: "GET", url: `/workspaces/${workspace_id}/archive/items` }, signal,
    ),
  restore: (workspace_id: string, item: ArchivedDataItem) =>
    apiRequest<{ ok: boolean; item: Pick<ArchivedDataItem, "month" | "kind" | "name"> }>({
      method: "POST",
      url: `/workspaces/${workspace_id}/archive/restore`,
      data: { month: item.month, kind: item.kind, name: item.name, confirm: true },
    }),
};

export interface LifecyclePreview {
  dry_run: boolean;
  workspace_id: string;
  policy: Record<string, unknown>;
  candidate_counts: Record<string, number>;
  candidates: Array<{ type: string; name: string; sid?: string; count?: number }>;
  blocked_items: Array<{ path: string; reason: string }>;
  deleted_counts?: Record<string, number>;
  moved_counts?: Record<string, number>;
  warnings: string[];
}

export const sessionExtApi = {
  /** GET /api/sessions/default */
  default: (signal?: AbortSignal) =>
    apiRequest<{ session: Session }>({ method: "GET", url: "/sessions/default" }, signal),

  /** POST /api/sessions/:id/restore */
  restore: (session_id: string, workspace_id: string) =>
    apiRequest<{ ok: boolean }>({ method: "POST", url: `/sessions/${session_id}/restore`, params: { workspace_id } }),
};
