/**
 * Workbench store — chat history + run results keyed by session_id,
 * persisted to localStorage so F5 不会丢历史 (plan-C 方案).
 *
 * 状态:
 *  - bySession: Record<session_id, ChatMsg[]> 持久化到 localStorage
 *  - results:  Record<session_id, AgentResult[]>  各 session 的运行记录
 *  - currentSessionId: 镜像 useSessionStore.currentSessionId
 *  - sending: 是否在等后端
 *
 * 持久化策略:
 *  - 每个会话最多 100 条消息
 *  - 最多保留 20 个最近会话
 *  - 超出后按最近一条消息时间执行 LRU 淘汰
 *  - localStorage key: "na_workbench"
 */
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { StateStorage } from "zustand/middleware";
import type { AgentResult, SessionMessage, MessageStatus, InlineToolCall, RuntimeEvent } from "../types";
import { sanitizeAssistantText } from "../utils/displayText";
import { runtimeAuditApi } from "../api";

// ── Debounced localStorage — batched writes to avoid UI stutter ──
function debouncedStorage(key: string, delayMs = 300): StateStorage {
  let timer: ReturnType<typeof setTimeout> | null = null;
  return {
    getItem: () => localStorage.getItem(key),
    setItem: (_: string, value: string) => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        try { localStorage.setItem(key, value); } catch {}
        timer = null;
      }, delayMs);
    },
    removeItem: () => {
      if (timer) clearTimeout(timer);
      try { localStorage.removeItem(key); } catch {}
    },
  };
}

export interface ChatMsg {
  id: string;
  message_id?: string;
  role: "user" | "assistant" | "system";
  text: string;
  created_at: string;
  /** Message lifecycle status */
  status: MessageStatus;
  /** attached to assistant msgs only */
  result?: AgentResult;
  /** v1.0.3.2: run_id from the backend, used for dedup in mergeFromBackend */
  run_id?: string;
  /** Inline tool calls for structured rendering */
  toolCalls?: InlineToolCall[];
  /** Error message when status === "error" */
  error?: string;
  /** Trace ID for context linking */
  trace_id?: string;
  /** Friendly Chinese label for the current SSOT Runtime stage, updated
   *  by realtime ``stage_*`` events from the WebSocket. Replaces the
   *  12s "思考中" blank with a live "正在分析任务…", "正在执行工具…",
   *  "整理最终回复…" label. Set during streaming, kept for history. */
  progressText?: string;
  /** Monotonic timer (ms) for the latest SSOT Runtime stage, used to
   *  render the small "(已等待 5.4s)" suffix. */
  progressElapsedMs?: number;
}

const MAX_MSGS_PER_SESSION = 100;
const MAX_SESSIONS = 20;

let msgSeq = 0;
function nextId(): string {
  msgSeq += 1;
  return `msg-${Date.now()}-${msgSeq}`;
}

/**
 * Cap a session's history to MAX_MSGS_PER_SESSION (drop oldest, keep newest).
 * Also cap the entire map to MAX_SESSIONS entries by simple LRU on
 * session_id (good enough — chat sessions are not high-cardinality).
 */
function capHistory(
  map: Record<string, ChatMsg[]>,
  keepSessionId?: string,
): Record<string, ChatMsg[]> {
  if (!map || typeof map !== "object") return {};
  // Per-session cap
  const capped: Record<string, ChatMsg[]> = {};
  for (const [k, v] of Object.entries(map)) {
    if (!Array.isArray(v)) continue;
    if (v.length > MAX_MSGS_PER_SESSION) {
      capped[k] = v.slice(v.length - MAX_MSGS_PER_SESSION);
    } else {
      capped[k] = v;
    }
  }
  // Global cap (LRU: drop sessions with oldest most-recent message)
  const keys = Object.keys(capped);
  if (keys.length > MAX_SESSIONS) {
    // Get timestamp of newest message per session
    const latestTs: Record<string, number> = {};
    for (const k of keys) {
      const msgs = capped[k];
      if (msgs.length === 0) { latestTs[k] = 0; continue; }
      const newest = msgs[msgs.length - 1];
      latestTs[k] = new Date(newest.created_at || 0).getTime();
    }
    const sorted = [...keys]
      .filter((key) => key !== keepSessionId)
      .sort((a, b) => latestTs[a] - latestTs[b]);
    const toDelete = sorted.slice(0, keys.length - MAX_SESSIONS);
    for (const k of toDelete) delete capped[k];
  }
  return capped;
}

function messageKey(m: Pick<ChatMsg, "message_id" | "run_id" | "role" | "text" | "created_at">): string {
  if (m.message_id) return `id:${m.message_id}`;
  if (m.run_id) return `run:${m.run_id}:${m.role}`;
  return `fallback:${m.role}:${m.created_at}:${m.text}`;
}

function dedupeMessages(messages: ChatMsg[]): ChatMsg[] {
  const byStable = new Set<string>();
  const out: ChatMsg[] = [];
  for (const message of messages) {
    const stable = messageKey(message);
    if (byStable.has(stable)) continue;
    byStable.add(stable);
    out.push(message);
  }
  return out;
}

function validChatMessage(message: unknown): message is ChatMsg {
  if (!message || typeof message !== "object") return false;
  const m = message as Partial<ChatMsg>;
  return (
    typeof m.id === "string" &&
    typeof m.text === "string" &&
    typeof m.created_at === "string" &&
    typeof m.role === "string" &&
    ["user", "assistant", "system"].includes(m.role)
  );
}

function findLocalForServer(serverMsg: ChatMsg, localMessages: ChatMsg[], matchedIds: Set<string>): ChatMsg | undefined {
  const stable = messageKey(serverMsg);
  const exact = localMessages.find((m) => messageKey(m) === stable && !matchedIds.has(m.id));
  if (exact) return exact;
  // Server messages carry message_id which takes priority in messageKey;
  // local placeholders often only have run_id (set during finalize).
  // Match by run_id before falling through to heuristics.
  if (serverMsg.run_id) {
    const runMatch = localMessages.find(
      (m) => m.run_id && m.run_id === serverMsg.run_id && m.role === serverMsg.role && !matchedIds.has(m.id),
    );
    if (runMatch) return runMatch;
  }
  if (serverMsg.role === "assistant") {
    // Match any local assistant placeholder that lacks server-side identity.
    return localMessages.find(
      (m) =>
        m.role === "assistant" &&
        !m.message_id &&
        !m.run_id &&
        !matchedIds.has(m.id),
    );
  }
  if (serverMsg.role === "user") {
    return localMessages.find(
      (m) => m.role === "user" && !m.message_id && m.text.trim() === serverMsg.text.trim() && !matchedIds.has(m.id),
    );
  }
  return undefined;
}

interface WorkbenchState {
  bySession: Record<string, ChatMsg[]>;
  currentSessionId: string | null;
  sending: boolean;
  lastUserInput: string;
  /**
   * Lazy-loaded run detail cache, keyed by run_id (not by session).
   * Populated when the Timeline tab expands a card and we need the full
   * events/tool_calls trace. Not persisted — re-fetched on reload.
   */
  runDetails: Record<string, AgentResult>;
  /**
   * Track in-flight run detail loads as shared Promises so concurrent
   * callers await the same load instead of polling. Replaces the old
   * `Record<string, boolean>` + 6s polling loop.
   */
  runDetailLoading: Record<string, Promise<AgentResult | null>>;
  /** Track failed loads so the Timeline can show a retry hint. */
  runDetailError: Record<string, string>;

  switchSession: (session_id: string | null) => void;
  moveSessionMessages: (from_session_id: string, to_session_id: string) => void;
  appendUser: (text: string, session_id: string | null) => void;
  /** Create a streaming assistant placeholder before response arrives */
  appendAssistantStreaming: (session_id: string | null) => string;
  /** Update an existing message (streaming→ready/error, append tool calls) */
  updateAssistant: (
    msgId: string,
    patch: Partial<Pick<ChatMsg, "status" | "text" | "error" | "toolCalls" | "trace_id" | "result" | "run_id" | "progressText" | "progressElapsedMs">>,
    session_id?: string,
  ) => void;
  setSending: (v: boolean) => void;
  /**
   * Attach a finalized AgentResult to the matching assistant message in `sid`
   * (defaults to currentSessionId). Matched by ChatMsg.run_id === result.turn_id.
   * No-op if the message is not yet present (caller should ensure messages
   * are loaded for `sid` first).
   */
  setLatestResult: (r: AgentResult, sid?: string) => void;
  /** Drop local history for current (or specified) session. */
  clear: (session_id?: string) => void;
  mergeFromBackend: (session_id: string, serverMsgs: SessionMessage[]) => void;
  /**
   * Lazily fetch the full run detail (GET /runs/<id> + /runs/<id>/trace)
   * and attach the merged AgentResult to the matching assistant message in
   * `sid`. Cached by run_id so repeat expansions are instant.
   *
   * Returns the AgentResult on success, or null on failure (the cache is
   * left with the error message).
   */
  loadRunDetail: (
    workspace_id: string,
    run_id: string,
    sid?: string,
  ) => Promise<AgentResult | null>;
}

export const useWorkbenchStore = create<WorkbenchState>()(
  persist(
    (set, get) => ({
      bySession: {},
      currentSessionId: null,
      sending: false,
      lastUserInput: "",
      runDetails: {},
      runDetailLoading: {},
      runDetailError: {},

      switchSession: (session_id) => {
        set((s) => {
          if (session_id && !s.bySession[session_id]) {
            return {
              currentSessionId: session_id,
              bySession: { ...s.bySession, [session_id]: [] },
            };
          }
          return { currentSessionId: session_id };
        });
      },

      moveSessionMessages: (from_session_id, to_session_id) => {
        const fromSid = String(from_session_id || "").trim();
        const toSid = String(to_session_id || "").trim();
        if (!fromSid || !toSid || fromSid === toSid) return;
        set((s) => {
          const source = Array.isArray(s.bySession[fromSid])
            ? s.bySession[fromSid].filter(validChatMessage)
            : [];
          if (source.length === 0) return s;
          const target = Array.isArray(s.bySession[toSid])
            ? s.bySession[toSid].filter(validChatMessage)
            : [];
          const nextBySession = { ...s.bySession };
          nextBySession[toSid] = dedupeMessages([...target, ...source]);
          delete nextBySession[fromSid];
          return {
            bySession: capHistory(nextBySession, toSid),
            currentSessionId: s.currentSessionId === fromSid ? toSid : s.currentSessionId,
          };
        });
      },

      appendUser: (text, session_id) => {
        const sid = session_id ?? get().currentSessionId ?? "_scratch";
        const msg: ChatMsg = {
          id: nextId(),
          role: "user",
          text,
          status: "ready",
          created_at: new Date().toISOString(),
        };
        set((s) => {
          const cur = s.bySession[sid] ?? [];
          const next = capHistory({ ...s.bySession, [sid]: [...cur, msg] }, sid);
          return { bySession: next, lastUserInput: text };
        });
      },

      appendAssistantStreaming: (session_id) => {
        const sid = session_id ?? get().currentSessionId ?? "_scratch";
        const msgId = nextId();
        const msg: ChatMsg = {
          id: msgId,
          role: "assistant",
          text: "",
          status: "streaming",
          created_at: new Date().toISOString(),
          toolCalls: [],
        };
        set((s) => {
          const cur = s.bySession[sid] ?? [];
          const next = capHistory({ ...s.bySession, [sid]: [...cur, msg] }, sid);
          return { bySession: next };
        });
        return msgId;
      },

      updateAssistant: (msgId, patch, session_id) => {
        const sid = session_id ?? get().currentSessionId ?? "_scratch";
        set((s) => {
          const cur = s.bySession[sid] ?? [];
          const idx = cur.findIndex((m) => m.id === msgId);
          if (idx < 0) return s;
          // Skip set() if all patch fields are already identical to
          // the current message — avoids triggering re-renders for no-op
          // updates during high-frequency streaming (every 50ms flush).
          const existing = cur[idx];
          let changed = false;
          for (const key of Object.keys(patch) as Array<keyof typeof patch>) {
            if (existing[key] !== patch[key]) { changed = true; break; }
          }
          if (!changed) return s;
          const updated = { ...existing, ...patch };
          const next = capHistory(
            { ...s.bySession, [sid]: [...cur.slice(0, idx), updated, ...cur.slice(idx + 1)] },
            sid,
          );
          return { bySession: next };
        });
      },

      setSending: (v) => set({ sending: v }),
      // C-plan refactor: setLatestResult attaches the AgentResult to the
      // matching assistant ChatMsg (by run_id == turn_id). Timeline view
      // derives runs from bySession, so this single source-of-truth works
      // across session switches / page reloads.
      setLatestResult: (r, sid) => {
        const targetSid = sid ?? get().currentSessionId;
        if (!targetSid) return;
        set((s) => {
          const msgs = s.bySession[targetSid];
          if (!Array.isArray(msgs) || msgs.length === 0) return s;
          // Match the latest assistant message with this run_id
          let idx = -1;
          for (let i = msgs.length - 1; i >= 0; i--) {
            const m = msgs[i];
            if (m.role === "assistant" && m.run_id === r.turn_id) {
              idx = i;
              break;
            }
          }
          if (idx < 0) return s;
          const updated: ChatMsg = { ...msgs[idx], result: r };
          const nextMsgs = [
            ...msgs.slice(0, idx),
            updated,
            ...msgs.slice(idx + 1),
          ];
          return { bySession: { ...s.bySession, [targetSid]: nextMsgs } };
        });
      },

      // ────────────────────────────────────────────────────────────────────
      // Load run detail — lazy fetch full run trace/tool_calls.
      //
      // Timeline cards start with no AgentResult attached (the messages API
      // doesn't include events/tool_calls). When the user expands a card and
      // the assistant message has no `result`, this action:
      //   1. calls GET /api/runs/<id>?workspace_id=...    → run record (38 keys incl. tool_calls)
      //   2. calls GET /api/workspaces/<ws>/runs/<id>/trace → events array
      //   3. merges into an AgentResult, attaches to the matching assistant msg,
      //      and stores in `runDetails` cache for repeat expansions.
      // ────────────────────────────────────────────────────────────────────
      loadRunDetail: function loadRunDetailImpl(workspace_id, run_id, sid) {
        // Return a shared Promise so concurrent callers await the
        // same load instead of polling. This avoids the 6s polling loop
        // and eliminates the need for cancellation mechanisms.
        const targetSid = sid ?? get().currentSessionId;
        const state = get();
        // Already cached → return immediately.
        if (state.runDetails[run_id]) {
          const cached = state.runDetails[run_id];
          get().setLatestResult(cached, targetSid ?? undefined);
          return Promise.resolve(cached);
        }
        // Already in-flight → return the same Promise.
        const existing = state.runDetailLoading[run_id];
        if (existing) {
          return existing.then((result) => {
            if (result) get().setLatestResult(result, targetSid ?? undefined);
            return result;
          });
        }

        // Clear any stale error for this run_id before starting.
        set((s) => ({
          runDetailError: { ...s.runDetailError, [run_id]: "" },
        }));

        // Kick off the actual fetch and store the Promise directly.
        const loadPromise = (async (): Promise<AgentResult | null> => {
          try {
            // Use allSettled so a partial trace response (or a 5xx on the
            // runtime endpoint) still surfaces whatever data is available —
            // Promise.all would force-bail and lose both result + trace data.
            const [runSettled, traceSettled] = await Promise.allSettled([
              runtimeAuditApi.run(workspace_id, run_id),
              runtimeAuditApi.trace(workspace_id, run_id),
            ]);
            const runResp = runSettled.status === "fulfilled" ? runSettled.value : {};
            const traceResp = traceSettled.status === "fulfilled" ? traceSettled.value : {};
            const runRecord = (runResp && typeof runResp === "object" ? runResp : {}) as Record<string, unknown>;
            const traceData = (traceResp && typeof traceResp === "object" ? traceResp : {}) as {
              events?: RuntimeEvent[];
            };
            const metadata = (runRecord.metadata && typeof runRecord.metadata === "object"
              ? runRecord.metadata
              : {}) as Record<string, unknown>;
            const runtimeMetadata = (metadata.ssot_runtime && typeof metadata.ssot_runtime === "object"
              ? metadata.ssot_runtime
              : {}) as Record<string, unknown>;

            const merged: AgentResult = {
              ok: runRecord.ok !== false,
              final_response: (runRecord.final_response_summary as string) || "",
              events: Array.isArray(traceData.events) ? traceData.events : [],
              trace_id: (runRecord.trace_id as string) || "",
              session_id: (runRecord.session_id as string) || "",
              turn_id: run_id,
              tool_calls: Array.isArray(runRecord.tool_calls) ? (runRecord.tool_calls as AgentResult["tool_calls"]) : [],
              warnings: Array.isArray(runRecord.warnings) ? (runRecord.warnings as string[]) : [],
              errors: [
                ...(Array.isArray(runRecord.errors) ? (runRecord.errors as string[]) : []),
                ...(((runRecord as Record<string, unknown>).error as string | null) ? [(runRecord.error as string)] : []),
              ],
              tool_decision: (runRecord.tool_decision as AgentResult["tool_decision"]) || { needed: false },
              no_tool_reason: (runRecord.no_tool_reason as string) || "",
              metadata: {
                selected_capabilities: (metadata.selected_capabilities as string[]) || [],
                visible_tools: (metadata.visible_tools as string[]) || [],
                retry_summary: (metadata.retry_summary as AgentResult["metadata"]["retry_summary"])
                  || (runtimeMetadata.retry_summary as AgentResult["metadata"]["retry_summary"])
                  || undefined,
                retry_events: (metadata.retry_events as AgentResult["metadata"]["retry_events"])
                  || (runtimeMetadata.retry_events as AgentResult["metadata"]["retry_events"])
                  || [],
                validation_correction_summary:
                  (metadata.validation_correction_summary as AgentResult["metadata"]["validation_correction_summary"])
                  || (runtimeMetadata.validation_correction_summary as AgentResult["metadata"]["validation_correction_summary"])
                  || undefined,
                validation_correction_events:
                  (metadata.validation_correction_events as AgentResult["metadata"]["validation_correction_events"])
                  || (runtimeMetadata.validation_correction_events as AgentResult["metadata"]["validation_correction_events"])
                  || [],
                tool_recovery_events:
                  (metadata.tool_recovery_events as AgentResult["metadata"]["tool_recovery_events"])
                  || (runtimeMetadata.tool_recovery_events as AgentResult["metadata"]["tool_recovery_events"])
                  || [],
                context_compacted: Boolean(metadata.context_compacted ?? runtimeMetadata.context_compacted),
                context_estimated_tokens: Number(
                  metadata.context_estimated_tokens ?? runtimeMetadata.context_estimated_tokens ?? 0,
                ),
                context_budget: (metadata.context_budget as AgentResult["metadata"]["context_budget"])
                  || (runtimeMetadata.context_budget as AgentResult["metadata"]["context_budget"])
                  || undefined,
                output_truncated: Boolean(metadata.output_truncated ?? runtimeMetadata.output_truncated),
                output_truncation_reason: String(
                  metadata.output_truncation_reason ?? runtimeMetadata.output_truncation_reason ?? "",
                ),
                source_count: 0,
                workspace_id,
              },
            };

            set((s) => {
              const nextDetails = { ...s.runDetails, [run_id]: merged };
              const nextLoading = { ...s.runDetailLoading };
              delete nextLoading[run_id];
              let nextBySession = s.bySession;
              if (targetSid) {
                const msgs = s.bySession[targetSid];
                if (Array.isArray(msgs) && msgs.length > 0) {
                  let idx = -1;
                  for (let i = msgs.length - 1; i >= 0; i--) {
                    const m = msgs[i];
                    if (m.role === "assistant" && m.run_id === run_id) {
                      idx = i;
                      break;
                    }
                  }
                  if (idx >= 0 && !msgs[idx].result) {
                    const updated: ChatMsg = { ...msgs[idx], result: merged };
                    const nextMsgs = [
                      ...msgs.slice(0, idx),
                      updated,
                      ...msgs.slice(idx + 1),
                    ];
                    nextBySession = { ...s.bySession, [targetSid]: nextMsgs };
                  }
                }
              }
              return {
                runDetails: nextDetails,
                runDetailLoading: nextLoading,
                bySession: nextBySession,
              };
            });
            get().setLatestResult(merged, targetSid ?? undefined);
            return merged;
          } catch (e) {
            const msg = (e && typeof e === "object" && "message" in e)
              ? String((e as { message?: unknown }).message)
              : "加载失败";
            set((s) => {
              const nextLoading = { ...s.runDetailLoading };
              delete nextLoading[run_id];
              return {
                runDetailLoading: nextLoading,
                runDetailError: { ...s.runDetailError, [run_id]: msg },
              };
            });
            return null;
          }
        })();

        // Store the Promise in the loading map BEFORE any await resolves.
        set((s) => ({
          runDetailLoading: { ...s.runDetailLoading, [run_id]: loadPromise },
        }));

        return loadPromise;
      },

      clear: (session_id) => {
        const sid = session_id ?? get().currentSessionId;
        if (!sid) return;
        set((s) => {
          const nextBySession = { ...s.bySession };
          delete nextBySession[sid];
          return { bySession: nextBySession };
        });
      },

      mergeFromBackend: (session_id, serverMsgs) => {
        if (!session_id) return;
        const converted: ChatMsg[] = dedupeMessages(serverMsgs.map((m) => ({
          id:
            m.message_id ??
            `srv-${m.run_id ?? `${m.role}-${m.created_at}-${m.content}`}:${m.role}`,
          message_id: m.message_id,
          role: m.role,
          text: m.role === "assistant" ? sanitizeAssistantText(m.content) : m.content,
          status: "ready",
          created_at: m.created_at,
          run_id: m.run_id,
          // `result` 不可从后端还原, 渲染为纯文本气泡 (无 inline 工具调用)
        })));
        set((s) => {
          const persisted = s.bySession[session_id];
          const cur = Array.isArray(persisted)
            ? dedupeMessages(persisted.filter(validChatMessage))
            : [];

          // Merge strategy: server messages are the authoritative timeline.
          // For assistant messages (with run_id): prefer local copy if already
          // rendered (rich toolCalls/result data), fall back to server copy.
          // For user messages without ids: match only local optimistic messages
          // that have not yet received a backend identity.
          const combined: ChatMsg[] = [];
          const seenKeys = new Set<string>();
          const matchedIds = new Set<string>();

          for (const serverMsg of converted) {
            const stable = messageKey(serverMsg);
            if (seenKeys.has(stable)) continue;
            const localMatch = findLocalForServer(serverMsg, cur, matchedIds);
            const nextMsg = localMatch
              ? {
                  ...localMatch,
                  message_id: serverMsg.message_id ?? localMatch.message_id,
                  run_id: serverMsg.run_id ?? localMatch.run_id,
                  created_at: serverMsg.created_at || localMatch.created_at,
                  status: localMatch.status === "streaming" ? "ready" : localMatch.status,
                  text:
                    serverMsg.role === "assistant" && serverMsg.text.trim()
                      ? serverMsg.text
                      : localMatch.text,
                }
              : serverMsg;
            combined.push(nextMsg);
            seenKeys.add(stable);
            if (localMatch) {
              matchedIds.add(localMatch.id);
              seenKeys.add(messageKey(localMatch));
              if (serverMsg.role === "user") {
                for (const duplicateLocal of cur) {
                  if (
                    duplicateLocal.id !== localMatch.id
                    && duplicateLocal.role === "user"
                    && !duplicateLocal.message_id
                    && !duplicateLocal.run_id
                    && duplicateLocal.text.trim() === serverMsg.text.trim()
                  ) {
                    matchedIds.add(duplicateLocal.id);
                    seenKeys.add(messageKey(duplicateLocal));
                  }
                }
              }
            }
          }

          // Append local-only messages not covered by server (e.g. streaming)
          for (const localMsg of cur) {
            if (matchedIds.has(localMsg.id)) continue;
            if (seenKeys.has(messageKey(localMsg))) continue;
            combined.push(localMsg);
            seenKeys.add(messageKey(localMsg));
          }

          // Sort by created_at ascending
          combined.sort((a, b) => (a.created_at || "").localeCompare(b.created_at || ""));
          const cleaned = dedupeMessages(combined);
          const next = capHistory(
            { ...s.bySession, [session_id]: cleaned },
            session_id,
          );
          return { bySession: next };
        });
      },
    }),
    {
      name: "na_workbench",
      version: 3,
      storage: createJSONStorage(() => debouncedStorage("na_workbench")),
      // v3: drop `results` from persistence — Timeline derives runs from
      // bySession now, so we only need to persist bySession + lastUserInput.
      migrate: (persisted: unknown, _version: number) => {
        // No-op: old `results` field is simply ignored. bySession carries the
        // agent results inside ChatMsg.result, which is what Timeline reads.
        return persisted as WorkbenchState;
      },
      partialize: (s): Partial<WorkbenchState> => ({
        bySession: s.bySession,
        lastUserInput: s.lastUserInput,
      }),
      merge: (persisted: unknown, current: WorkbenchState): WorkbenchState => {
        const p = persisted as Record<string, unknown> | null | undefined;
        const safe = p?.bySession;
        const merged: Partial<WorkbenchState> = {};
        if (safe && typeof safe === "object" && !Array.isArray(safe)) {
          merged.bySession = safe as Record<string, ChatMsg[]>;
        }
        return { ...current, ...merged };
      },
    },
  ),
);
