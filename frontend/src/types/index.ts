/**
 * Domain types — strict 1:1 mapping from backend Python dataclasses `as_dict()`.
 * No fields are guessed. The frontend only renders what the backend actually
 * returns. See `agent/capabilities/catalog.py` and `agent/runtime/result.py`.
 */

export type ToolStatus = "enabled" | "planned" | "disabled";
export type RiskLevel = "low" | "medium" | "high" | "critical" | "forbidden";
export type Sensitivity = "public" | "internal" | "sensitive" | "secret";
export type ToolGovernanceStatus = "active" | "disabled" | "internal" | "forbidden";

/* ───────────────────────── BusinessCapability ─────────────────────────
 *
 * Wire shape produced by `/api/capabilities` from
 * `agent/capabilities/catalog.py`. These are business capability cards and
 * recommended canonical tools. They are not tool registrations or visibility
 * gates.
 */

export interface BusinessCapability {
  /** Unique capability id, e.g. `workspace.read`. */
  capability_id: string;
  /** Human-readable description (may be empty). */
  description: string;
  /** Category bucket, e.g. "workspace" | "knowledge" | "review". */
  category: string;
  /** Stable intent key, e.g. `analyze_data`. */
  intent: string;
  /** Module id (string), e.g. `knowledge`. */
  module: string;
  /** Canonical tools recommended for this capability. */
  tool_ids: string[];
  /** Risk level for downstream reasoning / UI. */
  risk_level: RiskLevel;
  /** True if outputs may contain sensitive material. */
  can_create_sensitive_output: boolean;
  /** True if a human reviewer is required before real-world use. */
  requires_verification: boolean;
  requires_human_review: boolean;
}

/* ───────────────────────── Tool Namespace Catalog ───────────────────────── */

export interface ToolCatalogItem {
  /** canonical_tool_id is the only public tool ID. */
  tool_id: string;
  canonical_tool_id: string;
  category: string;
  group: string;
  action: string;
  display_name: string;
  description?: string;
  usage_hint?: string;
  not_for?: string;
  actions?: string[];
  action_profiles?: {
    action: string;
    risk_level: RiskLevel;
    requires_approval: boolean;
    permission_action: string;
  }[];
  capability_actions?: string[];
  risk_level: RiskLevel;
  requires_approval: boolean;
  permission_action?: string;
  governance_status?: ToolGovernanceStatus;
  planner_visible?: boolean;
  enabled: boolean;
  callable_by_llm: boolean;
  input_schema?: Record<string, unknown>;
  allowed_callers?: string[];
  action_class?: string;
  destructive?: boolean;
  side_effects?: string;
  output_sensitivity?: Sensitivity;
  metadata?: Record<string, unknown>;
}

export interface ToolCatalogGroup {
  id: string;
  name: string;
  count: number;
  tools: ToolCatalogItem[];
}

export interface ToolCatalogCategory {
  id: string;
  name: string;
  description: string;
  count: number;
  groups: ToolCatalogGroup[];
}

export interface ToolCatalogResponse {
  tools: ToolCatalogItem[];
  categories: ToolCatalogCategory[];
  count: number;
  planner_visible_count?: number;
  governance_summary?: Record<ToolGovernanceStatus, number>;
  note?: string;
}

/* ──────────────────────── AgentResult & Tool Calls ──────────────────────── */

export interface ToolCallResult {
  call_id: string;
  tool_id: string;
  ok: boolean;
  duration_ms?: number | null;
  result?: unknown;
  summary?: string;
  artifacts?: Array<{ artifact_id: string; artifact_type: string; title: string }>;
  source_count?: number | null;
  manual_review_count?: number | null;
  errors?: string[];
  warnings?: string[];
  metadata?: Record<string, unknown>;
}

export interface AgentResult {
  ok: boolean;
  final_response: string;
  events: RuntimeEvent[];
  trace_id: string;
  session_id: string;
  turn_id: string;
  tool_calls: ToolCallResult[];
  warnings: string[];
  errors: string[];
  error_type?: string;
  /** v3.10: Structured content parts for sequenced inline rendering */
  content_parts?: ContentPart[];
  /** v2.1.2: Tool decision transparency */
  tool_decision?: {
    needed: boolean;
    selected_tools?: string[];
    failed_tools?: string[];
    blocked_by?: string[];
    approval_required?: boolean;
    reason?: string;
  };
  /** v2.1.2: Human-readable reason when no tools called */
  no_tool_reason?: string;
  metadata: {
    selected_capabilities?: string[];
    visible_tools?: string[];
    planner_mode?: string;
    source_count?: number;
    source_summary?: SourceSummary[];
    manual_review_count?: number;
    retrieval_backend?: string;
    scope?: string;
    workspace_id?: string;
    context_sources?: SourceSummary[];
    citations?: Array<Record<string, unknown>>;
    retrieval_diagnostics?: Record<string, unknown>;
    /** Runtime action retry summary surfaced from SSOT Runtime. */
    retry_summary?: {
      retry_attempts?: number;
      retried_nodes?: string[];
      retry_succeeded?: number;
      retry_failed?: number;
      retry_blocked?: number;
    };
    /** Runtime action retry events surfaced from SSOT Runtime. */
    retry_events?: Array<{
      type?: string;
      node_id?: string;
      tool_id?: string;
      attempt?: number;
      max_retries?: number;
      error_code?: string;
      original_error?: string;
      retry_allowed?: boolean;
      reason?: string;
      backoff_ms?: number;
      idempotent?: boolean;
      side_effect?: string;
      blocked_by_policy?: boolean;
      final_status?: string;
      duration_ms?: number;
    }>;
    /** Invalid tool arguments returned to the model for bounded correction. */
    validation_correction_summary?: {
      attempts?: number;
      max_attempts?: number;
      exhausted?: boolean;
    };
    validation_correction_events?: Array<{
      attempt?: number;
      max_attempts?: number;
      errors?: Array<Record<string, unknown>>;
    }>;
    orchestration_batches?: Array<{
      layers?: string[][];
      step_count?: number;
    }>;
    /** LLM-level replanning after a tool failure; distinct from replay retry. */
    tool_recovery_events?: Array<{
      iteration?: number;
      failed_tools?: string[];
      errors?: string[];
    }>;
    /** Background task tracking surfaced from SSOT Runtime. Distinct from retry. */
    tracking_summary?: {
      kind?: string;
      domain?: string;
      task_id?: string;
      status?: string;
      done?: boolean;
      terminal?: boolean;
      mode?: string;
      poll_count?: number;
      same_status_count?: number;
      stall_risk?: boolean;
      next_poll_seconds?: number;
      suggested_next_action?: string;
      progress?: Record<string, unknown>;
      summary?: Record<string, unknown>;
      raw?: Record<string, unknown>;
    };
    tracking_events?: Array<{
      node_id?: string;
      tool?: string;
      tracking?: Record<string, unknown>;
    }>;
    /** Input/output truncation is explicit so partial results are never silent. */
    context_compacted?: boolean;
    context_estimated_tokens?: number;
    context_budget?: {
      context_window_tokens?: number;
      max_input_tokens?: number;
      reserved_output_tokens?: number;
      safety_tokens?: number;
      tool_schema_tokens?: number;
      message_tokens?: number;
      history_tokens?: number;
      retrieved_context_tokens?: number;
      per_tool_result_tokens?: number;
      artifact_result_tokens?: number;
    };
    output_truncated?: boolean;
    output_truncation_reason?: string;
  };
}

export interface RuntimeEvent {
  event_id: string;
  event_type: string;
  type?: string;
  name?: string;
  status?: string;
  summary?: string;
  message?: string;
  tool_id?: string;
  level?: string;
  error?: string;
  /** v2.1.3: Event timing and metadata */
  occurred_at?: string;
  /** Backend sends Unix timestamp (float from time.time()). */
  timestamp?: number;
  started_at?: string;
  duration_ms?: number | null;
  /** v2.1.3: Approval and tool details */
  approval_id?: string;
  approval_status?: string;
  blocked_by?: string;
  input_preview?: string | Record<string, unknown>;
  output_preview?: string | Record<string, unknown>;
  /** Payload and metadata bags */
  payload?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

/* ──────────────────────────── Knowledge ──────────────────────────── */

export interface KnowledgeSource {
  source_id: string;
  workspace_id: string;
  title: string;
  summary?: string;
  source_type?: string;
  artifact_id?: string;
  status?: string;
  sensitivity?: Sensitivity;
  language?: string;
  scope?: "workspace" | "global" | "session";
  format?: string;
  tags: string[];
  enabled?: boolean;
  chunk_count: number;
  total_size_bytes?: number;
  created_at: string;
  updated_at?: string;
  metadata?: Record<string, unknown>;
}

export interface KnowledgeChunk {
  chunk_id: string;
  source_id: string;
  artifact_id?: string;
  title?: string;
  /** Backend returns a safe excerpt (never the full content). */
  safe_excerpt?: string;
  summary?: string;
  sensitivity?: Sensitivity;
  artifact_type?: string;
  tags?: string[];
  chunk_index?: number;
  llm_safe?: boolean;
  created_at?: string;
  metadata?: Record<string, unknown>;
}

export interface KnowledgeSearchResult {
  ok: boolean;
  query: string;
  /** Backend returns `results` (not `hits` / `source_summary`). */
  results: Array<{
    chunk_id: string;
    source_id: string;
    title?: string;
    summary?: string;
    safe_excerpt?: string;
    score: number;
    sensitivity?: Sensitivity;
    artifact_id?: string;
  }>;
  count: number;
  source_count: number;
  /** Best-effort derivation of `source_summary` for the UI. */
  source_summary: SourceSummary[];
  metadata: Record<string, unknown>;
  warnings: string[];
  note?: string;
}

export interface SourceSummary {
  source_id: string;
  chunk_id?: string;
  citation_id?: string;
  source_type?: string;
  evidence_type?: "knowledge" | "memory" | string;
  title: string;
  chapter?: string;
  section?: string;
  snippet: string;
  score: number;
}

/* ──────────────────────────── Artifacts ────────────────────────────
 *
 * Wire shape returned by
 *   GET /api/workspaces/<ws>/artifacts
 *   GET /api/workspaces/<ws>/artifacts/<art>
 * There is no `content_preview` field — full content is served by a
 * separate /content endpoint (see artifactsApi.content).
 */
export interface Artifact {
  artifact_id: string;
  workspace_id: string;
  artifact_type: string;
  title: string;
  created_at: string;
  updated_at: string;
  /** File size in bytes (server-reported). */
  size_bytes: number;
  /** File MIME type, e.g. "text/plain". May be empty. */
  mime_type: string;
  /** File extension, e.g. ".txt". May be empty. */
  file_ext: string;
  /** Truncated SHA-256, e.g. "abc12345". May be empty. */
  sha256_short: string;
  /** Managed file_id from FileStore. Required for read operations. */
  file_id: string;
  /** Path relative to the artifact store root. */
  relative_path: string;
  /** Lifecycle, e.g. "active" | "archived" | "deleted". */
  lifecycle: string;
  /** Where this artifact lives, e.g. "workspace" | "global". */
  scope: string;
  /** Origin, e.g. "user_upload" | "module_output" | "agent_run". */
  source: string;
  /** Sensitivity tier. */
  sensitivity: Sensitivity;
  /** Free-form tags. */
  tags: string[];
  /** Optional LLM-generated or module-supplied summary. */
  summary: string;
  /** Provenance: capability / module / skill that produced this artifact. */
  capability_id: string;
  module: string;
  skill: string;
  /** Run that produced this artifact (empty for user uploads). */
  run_id: string;
  created_by?: string;
  /** True if the content was redacted before persistence. */
  redaction_applied: boolean;
  /** Server-side metadata bag (varies by artifact_type). */
  metadata: Record<string, unknown>;
  governance?: {
    evidence_key?: string;
    evidence_role?: string;
    evidence_quality?: "complete" | "partial" | "unknown" | string;
    authority_domain?: "evidence" | "contextual" | string;
    authority_status: "authoritative" | "provisional" | "historical" | "incomplete" | "contextual" | "not_applicable" | string;
    authority_reason?: string;
    authority_policy?: string;
    authoritative_artifact_id?: string;
    latest_artifact_id?: string;
    is_latest_observation?: boolean;
    version?: number;
    version_count?: number;
  };
}

export interface ManagedFileArtifact {
  artifact_id: string;
  artifact_type: string;
  title: string;
  lifecycle: string;
  run_id?: string;
  authority?: string;
}

export interface ManagedFile {
  file_id: string;
  logical_type: string;
  file_kind: string;
  original_name: string;
  mime_type: string;
  binary: boolean;
  size_bytes: number;
  created_at: string;
  source: string;
  sensitivity: Sensitivity;
  lifecycle: string;
  session_id: string;
  run_id: string;
  metadata: Record<string, unknown>;
  artifacts: ManagedFileArtifact[];
  reference_count: number;
  reference_types: string[];
  references: Array<{ owner_type: string; owner_id: string; relation: string }>;
}

export interface DataOverview {
  workspace_id: string;
  files: {
    total: number;
    active: number;
    archived: number;
    soft_deleted: number;
    size_bytes: number;
    referenced: number;
    unreferenced: number;
  };
  artifacts: { total: number; active: number };
  types: Record<string, number>;
  health: {
    orphan_files: number;
    missing_on_disk: number;
    soft_deleted: number;
    ok: boolean;
  };
}

export interface ArchivedDataItem {
  month: string;
  kind: "runs" | "traces" | "jobs" | "tmp";
  name: string;
  size_bytes: number;
  archived_at: string;
}

/* ──────────────────────────── Reviews ──────────────────────────── */

export type ReviewStatus = "pending" | "accepted" | "ignored" | "modified";

export interface ReviewItem {
  item_id: string;
  workspace_id: string;
  artifact_id: string;
  severity: "info" | "warning" | "error";
  category: string;
  line_no: number | null;
  reason: string;
  requires_human_review: boolean;
  status: ReviewStatus;
  user_note: string;
  created_at: string;
  updated_at: string;
}

/* ──────────────────────────── Sessions / Workspaces ──────────────────────────── */

export interface Session {
  session_id: string;
  workspace_id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

/* ────────────────────────── SessionMessage ──────────────────────────
 *
 * Wire shape from GET /api/sessions/<id>/messages (see
 * storage/session_store.py::get_session_messages). The backend
 * reconstructs chat messages from run records (one user + one assistant
 * per run). Frontend maps these to ChatMsg in the workbench store.
 *
 * message_id is deterministic: <run_id>:user / <run_id>:assistant.
 * Frontend MUST NOT fabricate random IDs for dedup.
 */
export interface SessionMessage {
  message_id?: string;
  session_id?: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  run_id?: string;
  intent?: string;
  status?: string;
  capability?: string;
  trace_id?: string;
  quality_summary?: Record<string, unknown>;
  llm_metadata?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface Workspace {
  workspace_id: string;
  name: string;
  created_at: string;
  is_default: boolean;
  runs_count?: number;
  artifacts_count?: number;
  memory_count?: number;
  stats: {
    session_count: number;
    artifact_count: number;
    knowledge_source_count: number;
  };
}

export interface AppVersion {
  app?: string;
  version: string;
  build_commit?: string;
  product_ready?: boolean;
}

export interface RuntimeSummary {
  capabilities: {
    total: number;
    enabled: number;
  };
  tools: {
    registered: number;
    model_visible: number;
    hidden_or_non_llm: string[];
  };
}

/* ──────────────────────────── LLM Settings ──────────────────────────── */

export interface LlmConfig {
  enabled: boolean;
  provider: string;
  safe_mode: boolean;
  base_url: string;
  model: string;
  temperature: number;
  max_tokens: number;
  key_configured: boolean;
  key_preview?: string | null;
  updated_at?: string | null;
  source?: string;
  config_source?: string;
  config_path?: string;
  global?: boolean;
  note?: string;
  is_active?: boolean;
  active_provider?: string;
}

export interface ProviderConfig {
  provider: string;
  label: string;
  enabled: boolean;
  base_url: string;
  model: string;
  temperature: number;
  max_tokens: number;
  safe_mode: boolean;
  key_configured: boolean;
  key_preview?: string | null;
  hint?: string;
  updated_at?: string | null;
  is_active: boolean;
}

export interface ProviderListResponse {
  ok: boolean;
  providers: ProviderConfig[];
  active: string;
}

export interface ProviderSaveResponse {
  ok: boolean;
  config: ProviderConfig;
}

export interface ProviderActivateResponse {
  ok: boolean;
  config: ProviderConfig;
  active: string;
  message: string;
}

export interface LlmHealth {
  base_url_reachable: boolean;
  chat_completion_endpoint_reachable: boolean;
  chat_completion_ok: boolean;
  configured: boolean;
  connected: boolean;
  http_status: number | null;
  key_loaded: boolean;
  last_error?: string | null;
  last_error_type?: string | null;
  model: string;
  models_endpoint_ok: boolean;
  provider: string;
}

export interface LlmStatus {
  enabled: boolean;
  enabled_by_ui: boolean | null;
  provider: string;
  model: string;
  provider_type: string;
  vision_supported?: boolean;
  safe_mode: boolean;
  key_loaded: boolean;
  key_source: string;
  config_source: string;
  connected: boolean;
  settings_file_exists: boolean;
  health: LlmHealth;
  recent_failure?: {
    at: string;
    error_summary: string;
    error_type: string;
  } | null;
  last_success?: { at: string } | null;
  red_lines?: string[];
  allowed_tasks?: string[];
  blocked_tasks?: string[];
}

export interface LlmTestResult {
  ok: boolean;
  provider?: string;
  model?: string;
  llm_used: boolean;
  config_source: string;
  policy_pass: boolean;
  response: string;
  safe_to_show: boolean;
  fallback_reason?: string;
  warnings: string[];
  metadata: Record<string, unknown>;
}

export interface LlmTestRequest {
  task?: "result_summarize" | "context_qa" | "response_compose";
  message?: string;
  base_url?: string;
  model?: string;
  api_key?: string;
  provider?: string;
  signal?: AbortSignal;
}

/* ──────────────────────────── Runtime Audit ──────────────────────────── */

export interface RuntimeAuditTurn {
  run_id?: string;
  created_at?: string;
  user_input_summary?: string;
  intent?: string;
  turn_id: string;
  session_id: string;
  trace_id: string;
  started_at: string;
  finished_at: string;
  status: string;
  ok?: boolean;
  capability?: string;
  selected_capabilities: string[];
  visible_tools: string[];
  tool_call_count: number;
  error_count: number;
  warning_count: number;
  events: RuntimeEvent[];
  /** v2.1.2: Tool decision transparency */
  tool_decision?: {
    needed: boolean;
    selected_tools?: string[];
    failed_tools?: string[];
    blocked_by?: string[];
    approval_required?: boolean;
    reason?: string;
  };
  /** v2.1.2: Human-readable reason when no tools called */
  no_tool_reason?: string;
  /** v2.1.3: Additional trace detail fields */
  timeline_summary?: string;
  report_artifacts?: Array<{ artifact_id: string; title: string }>;
  artifact_refs?: Array<{ artifact_id: string; title: string }>;
  metadata?: Record<string, unknown>;
}

/* ──────────────────────────── ApiError ──────────────────────────── */

export type ApiErrorCode =
  | "network"
  | "timeout"
  | "rate_limited"
  | "http_4xx"
  | "http_5xx"
  | "parse"
  | "aborted"
  | "unknown";

export interface ApiError {
  ok: false;
  status: number;
  code: ApiErrorCode;
  message: string;
  request_id?: string;
  details?: unknown;
  url?: string;
  timestamp: string;
}

export function isApiError(e: unknown): e is ApiError {
  return (
    typeof e === "object" &&
    e !== null &&
    "ok" in e &&
    (e as { ok: unknown }).ok === false &&
    "code" in e &&
    "message" in e
  );
}

/* ──────────────────────────── AsyncState ──────────────────────────── */

export type AsyncState<T> =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "success"; data: T }
  | { kind: "empty"; reason?: string }
  | { kind: "error"; error: ApiError };

export function isSuccess<T>(s: AsyncState<T>): s is { kind: "success"; data: T } {
  return s.kind === "success";
}
export function isEmpty<T>(s: AsyncState<T>): s is { kind: "empty"; reason?: string } {
  return s.kind === "empty";
}
export function isError<T>(s: AsyncState<T>): s is { kind: "error"; error: ApiError } {
  return s.kind === "error";
}
export function isLoading<T>(s: AsyncState<T>): s is { kind: "loading" } {
  return s.kind === "loading";
}

/* ── API response types (v3.4 — frontend-backend alignment) ── */

export interface JobItem {
  job_id: string;
  job_type: string;
  title: string;
  status: string;
  workspace_id: string;
  created_at: string;
  updated_at?: string;
  finished_at?: string;
  error?: string;
  progress?: number;
  input_artifacts?: string[];
  output_artifacts?: string[];
}

export interface JobEvent {
  event_id: string;
  event_type: string;
  timestamp: string;
  data: Record<string, unknown>;
}

export interface MemoryRecord {
  memory_id: string;
  title: string;
  summary?: string;
  content: string;
  memory_type: string;
  scope: string;
  source?: string;
  confidence?: number;
  metadata?: Record<string, unknown>;
  tags: string[];
  status: string;
  value_preview?: string;
  created_at: string;
  updated_at?: string;
}

export interface ToolPermission {
  tool_id: string;
  enabled: boolean;
  risk_level: string;
  requires_approval: boolean;
}

/* ──────────────────────────── Message Status ──────────────────────────── */

export type MessageStatus = "streaming" | "ready" | "error";

/** Structured content part for inline sequenced rendering */
export type ContentPart =
  | { type: "text"; text: string }
  | { type: "tool_call"; tool_id: string; ok: boolean; summary: string; duration_ms?: number };

/** Inline tool call data for structured rendering within messages */
export interface InlineToolCall {
  tool_id: string;
  tool_name: string;
  ok: boolean;
  status?: string;
  summary?: string;
  duration_ms?: number;
  errors?: string[];
  artifacts?: Array<{ artifact_id: string; artifact_type: string; title: string }>;
  orchestration?: {
    step_id?: string;
    depends_on?: string[];
    layer?: number;
    parallel?: boolean;
    failure_policy?: string;
  };
}
