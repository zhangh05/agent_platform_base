import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { memoryApi } from "../../api";
import { useSessionStore } from "../../stores/session";
import { Badge, StatusDot } from "../../components/common";
import { IconSearch, IconPlus, IconRefresh, IconClose, IconCheck, IconTrash } from "../../components/Icon";
import { PageHeader, FilterBar } from "../../components/ui";

interface MemEntry {
  memory_id?: string;
  title?: string;
  summary?: string;
  content?: string;
  value_preview?: string;
  status?: string;
  scope?: string;
  source?: string;
  confidence?: number;
  memory_type?: string;
  tags?: string[];
  metadata?: Record<string, unknown>;
}

export function MemoryPage() {
  const currentWorkspaceId = useSessionStore((s) => s.currentWorkspaceId);
  const wsId = currentWorkspaceId;
  const [entries, setEntries] = useState<MemEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQ, setSearchQ] = useState("");
  const [searchRes, setSearchRes] = useState<MemEntry[] | null>(null);
  const [typeFilter, setTypeFilter] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [sel, setSel] = useState<MemEntry | null>(null);
  const [err, setErr] = useState("");
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);
  const toastTimerRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const d = await memoryApi.list({ workspace_id: wsId, limit: 500 });
      if (d.ok) setEntries((d.records || []) as MemEntry[]);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "加载失败");
    }
    setLoading(false);
  }, [wsId]);

  useEffect(() => { load(); }, [load]);

  const display = useMemo(() => {
    const source = searchRes ?? entries;
    return typeFilter ? source.filter((entry) => entry.memory_type === typeFilter) : source;
  }, [entries, searchRes, typeFilter]);

  const handleSearch = () => {
    if (!searchQ.trim()) {
      setSearchRes(null);
      return;
    }
    memoryApi.search({ query: searchQ, workspace_id: wsId }).then((d) => setSearchRes((d.results || []) as MemEntry[]));
  };

  const handleCreate = async () => {
    if (!title || !content) return;
    setErr("");
    try {
      const result = await memoryApi.create({
        title,
        content,
        workspace_id: wsId,
        memory_type: "knowledge_note",
        user_confirmed: true,
      });
      if (!result.ok) throw new Error("记忆门控拒绝了该内容");
      setTitle("");
      setContent("");
      setShowCreate(false);
      load();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "创建记忆失败");
    }
  };

  const handleDeleteHard = async (memoryId: string) => {
    try {
      await memoryApi.deleteHard(memoryId, wsId);
      load();
      showToast("ok", "已永久删除");
    } catch (e: unknown) {
      showToast("err", "删除失败");
    }
    setDeleteConfirm(null);
  };

  const handleReview = async (memoryId: string, decision: "confirm" | "reject") => {
    try {
      const result = decision === "confirm"
        ? await memoryApi.confirm({ memory_id: memoryId, workspace_id: wsId })
        : await memoryApi.reject({ memory_id: memoryId, workspace_id: wsId });
      if (!result.ok) throw new Error("review_failed");
      showToast("ok", decision === "confirm" ? "记忆已确认并开始生效" : "记忆已拒绝");
      await load();
    } catch {
      showToast("err", decision === "confirm" ? "确认失败" : "拒绝失败");
    }
  };

  const handleBatchDelete = async () => {
    if (checked.size === 0) return;
    const ids = Array.from(checked);
    if (!confirm(`永久删除选中的 ${ids.length} 条记忆？此操作不可逆。`)) return;
    try {
      const res = await memoryApi.batchHardDelete(wsId, ids);
      showToast("ok", `已删除 ${res.deleted_count} 条`);
      setChecked(new Set());
      load();
    } catch {
      showToast("err", "批量删除失败");
    }
  };

  const showToast = (kind: "ok" | "err", msg: string) => {
    setToast({ kind, msg });
    if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
    toastTimerRef.current = window.setTimeout(() => setToast(null), 2000);
  };

  const allVisibleIds = useMemo(() => display.map(e => e.memory_id ?? "__").filter(Boolean), [display]);
  const allSelected = checked.size > 0 && allVisibleIds.every(id => checked.has(id));

  const toggleSelectAll = () => {
    if (allSelected) {
      setChecked(new Set());
    } else {
      setChecked(new Set(allVisibleIds));
    }
  };

  // ── Empty state ──
  if (!loading && !err && display.length === 0) {
    return (
      <div className="page">
        <PageHeader title="记忆管理" subtitle="核心规则、稳定事实、故障案例与操作方法分层管理">
          <button className="btn sm" onClick={() => setShowCreate(!showCreate)}>
            <IconPlus size={14} /> 新建记忆
          </button>
        </PageHeader>
        <div className="page-body">
          {showCreate && (
            <div className="card card-highlight">
              <div className="card-title memory-form-title">新建记忆</div>
              <input className="input memory-form-input" type="text" placeholder="标题" value={title} onChange={(e) => setTitle(e.target.value)} />
              <textarea className="input memory-form-textarea" placeholder="记忆内容..." value={content} onChange={(e) => setContent(e.target.value)} rows={3} />
              <div className="memory-form-actions">
                <button className="btn primary sm" onClick={handleCreate} disabled={!title || !content}>保存</button>
                <button className="btn sm" onClick={() => { setShowCreate(false); setTitle(""); setContent(""); }}>取消</button>
              </div>
            </div>
          )}
          <div className="hero">
            <div className="hero-mark hero-mark-memory">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2L2 7l10 5 10-5-10-5z" />
                <path d="M2 17l10 5 10-5" />
                <path d="M2 12l10 5 10-5" />
              </svg>
            </div>
            <h2 className="hero-title">暂无记忆数据</h2>
            <p className="hero-sub">Agent 在处理任务时会自动记录重要的决策、偏好和知识。你也可以手动创建新的记忆条目。</p>
            <button className="btn primary hero-create-btn" onClick={() => setShowCreate(true)}>
              <IconPlus size={14} /> 创建第一条记忆
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <PageHeader title="记忆管理" subtitle="核心规则、稳定事实、故障案例与操作方法分层管理">
        <button className="btn sm" onClick={() => setShowCreate(!showCreate)}>
          <IconPlus size={14} /> 新建
        </button>
        <button className="btn sm ghost" onClick={load} title="刷新">
          <IconRefresh size={14} />
        </button>
      </PageHeader>

      <div className="page-body">
        {/* Search + filter bar */}
        <FilterBar>
          <div className="search-input-wrapper">
            <svg className="search-input-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" />
            </svg>
            <input className={`input ${searchQ ? "search-input--has-clear" : ""}`} type="text" placeholder="搜索记忆..." value={searchQ}
              onChange={(e) => setSearchQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleSearch(); }} />
            {searchQ && (
              <button className="search-input-clear" onClick={() => { setSearchQ(""); setSearchRes(null); }}>
                <IconClose size={12} />
              </button>
            )}
          </div>
          <button className="btn sm" onClick={handleSearch}><IconSearch size={14} /> 搜索</button>
          <select className="input" value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="">全部分类</option>
            <option value="core_rule">核心规则</option>
            <option value="semantic_fact">稳定事实</option>
            <option value="episodic_case">故障案例</option>
            <option value="procedural_rule">操作方法</option>
            <option value="knowledge_note">知识笔记</option>
            <option value="profile">用户档案</option>
          </select>
          {searchRes && (
            <button className="btn sm ghost" onClick={() => { setSearchRes(null); setSearchQ(""); }}>清除结果</button>
          )}
          <div className="spacer" />
          {display.length > 0 && (
            <label className="select-all">
              <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} />
              全选 ({checked.size})
            </label>
          )}
          {checked.size > 0 && (
            <button className="btn sm danger" onClick={handleBatchDelete}>
              <IconTrash size={12} /> 删除 {checked.size} 条
            </button>
          )}
          <span className="count">
            {searchRes ? `搜索结果 ${display.length} 条` : `共 ${display.length} 条记忆`}
          </span>
        </FilterBar>

        {showCreate && (
          <div className="card card-highlight">
            <div className="card-title memory-form-title">新建记忆</div>
            <input className="input memory-form-input" type="text" placeholder="标题" value={title} onChange={(e) => setTitle(e.target.value)} />
            <textarea className="input memory-form-textarea" placeholder="记忆内容..." value={content} onChange={(e) => setContent(e.target.value)} rows={3} />
            <div className="memory-form-actions">
              <button className="btn primary sm" onClick={handleCreate} disabled={!title || !content}>保存</button>
              <button className="btn sm" onClick={() => setShowCreate(false)}>取消</button>
            </div>
          </div>
        )}

        {err && (
          <div className="card memory-error-card">
            <div className="memory-error-row">
              <span className="memory-error-icon">⚠</span>
              <span className="memory-error-text">{err}</span>
            </div>
          </div>
        )}

        {loading && (
          <div className="empty">
            <div className="empty-icon"><span className="spinner" /></div>
            <div className="empty-text">加载中…</div>
          </div>
        )}

        {!loading && (
          <div className="memory-list">
            {display.map((e, i) => {
              const isSelected = sel?.memory_id === e.memory_id;
              const isChecked = checked.has(e.memory_id ?? "");
              const isInactive = e.status === "rejected" || e.status === "expired";
              const dotColor = isInactive ? "err" : e.status === "pending" || e.status === "conflict" ? "warn" : "ok";
              const meta = e.metadata || {};
              const origin = String(meta.generation_origin || meta.extraction_method || e.source || "");
              const reason = String(meta.extraction_reason || "");
              const score = meta.llm_score != null ? Number(meta.llm_score) : undefined;
              const confidence = e.confidence != null ? Number(e.confidence) : undefined;
              const evidenceSource = meta.evidence_source ? String(meta.evidence_source) : "";
              const authority = meta.authority ? String(meta.authority) : "";
              const memoryKey = meta.memory_key ? String(meta.memory_key) : "";
              const evidenceEventIds = Array.isArray(meta.evidence_event_ids)
                ? meta.evidence_event_ids.map((value) => String(value || "")).filter(Boolean)
                : [];
              const mergedFrom = Array.isArray(meta.merged_from)
                ? meta.merged_from.map((v) => String(v || "")).filter(Boolean)
                : [];

              return (
                <div key={e.memory_id || i} className={`card memory-card ${isChecked ? "checked" : ""} ${isInactive ? "inactive" : ""}`}
                  onClick={() => setSel(isSelected ? null : e)}>
                  <div className="memory-card-row">
                    {/* Checkbox */}
                    <input type="checkbox" checked={isChecked}
                      onChange={(ev) => {
                        const next = new Set(checked);
                        if (ev.target.checked) next.add(e.memory_id ?? "");
                        else next.delete(e.memory_id ?? "");
                        setChecked(next);
                      }}
                      onClick={(ev) => ev.stopPropagation()}
                      className="memory-checkbox" />

                    <div className="memory-card-content">
                      <div className="memory-card-title">
                        <StatusDot status={dotColor} />
                        <strong>{e.title || e.summary || e.content?.slice(0, 80) || "未命名记忆"}</strong>
                        {e.status && e.status !== "active" && (
                          <Badge kind={isInactive ? "err" : "warn"}>{e.status}</Badge>
                        )}
                        {e.memory_type && <Badge kind="info">{memoryTypeLabel(e.memory_type)}</Badge>}
                        {e.scope && <Badge kind="muted">{e.scope}</Badge>}
                        {origin && <Badge kind="muted">{memoryOriginLabel(origin)}</Badge>}
                        {authority && <Badge kind={authority === "explicit_user" ? "ok" : "muted"}>{memoryAuthorityLabel(authority)}</Badge>}
                        {score != null && Number.isFinite(score) && <Badge kind={score >= 4 ? "ok" : "warn"}>score {score}</Badge>}
                      </div>
                      <div className="memory-card-preview">
                        {e.value_preview || e.content?.substring(0, 150) || "(无内容)"}
                      </div>
                      {(reason || confidence != null) && (
                        <div className="memory-card-preview muted text-xs">
                          {reason ? `提取原因：${memoryReasonLabel(reason)}` : ""}
                          {confidence != null && Number.isFinite(confidence) ? ` · 置信度 ${Math.round(confidence * 100)}%` : ""}
                        </div>
                      )}
                    </div>

                    <button className="btn sm ghost memory-card-delete" title="永久删除"
                      onClick={(ev) => { ev.stopPropagation(); setDeleteConfirm(e.memory_id || null); }}>
                      <IconTrash size={13} />
                    </button>
                    <span className="memory-card-chevron">
                      {isSelected ? "▾" : "▸"}
                    </span>
                  </div>

                  {deleteConfirm === e.memory_id && (
                    <div className="memory-delete-confirm">
                      <span>永久删除这条记忆？此操作不可逆。</span>
                      <button className="btn sm danger" onClick={() => handleDeleteHard(e.memory_id!)}>
                        <IconCheck size={11} /> 确认
                      </button>
                      <button className="btn sm ghost" onClick={(ev) => { ev.stopPropagation(); setDeleteConfirm(null); }}>
                        <IconClose size={11} /> 取消
                      </button>
                    </div>
                  )}

                  {isSelected && (
                    <div className="memory-card-detail">
                      {e.content && (
                        <pre>{e.content}</pre>
                      )}
                      {(reason || evidenceSource || authority || memoryKey || evidenceEventIds.length > 0 || mergedFrom.length > 0) && (
                        <div className="memory-explain-box">
                          {reason && <div>为什么记：{memoryReasonLabel(reason)}</div>}
                          {authority && <div>权威来源：{memoryAuthorityLabel(authority)}</div>}
                          {evidenceSource && <div>证据来源：{evidenceSource}</div>}
                          {memoryKey && <div>记忆主题：{memoryKey}</div>}
                          {evidenceEventIds.length > 0 && <div>经历证据：{evidenceEventIds.length} 条</div>}
                          {mergedFrom.length > 0 && (
                            <div>合并来源：{mergedFrom.join(" + ")}</div>
                          )}
                        </div>
                      )}
                      {e.tags && e.tags.length > 0 && (
                        <div className="tags">
                          {e.tags.map((t: string) => <Badge key={t} kind="accent">{t}</Badge>)}
                        </div>
                      )}
                      {(e.status === "pending" || e.status === "conflict") && e.memory_id && (
                        <div className="actions">
                          <button className="btn primary sm" onClick={() => void handleReview(e.memory_id!, "confirm")}>
                            <IconCheck size={12} /> 确认并启用
                          </button>
                          <button className="btn sm" onClick={() => void handleReview(e.memory_id!, "reject")}>
                            <IconClose size={12} /> 拒绝
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Toast notification */}
      {toast && (
        <div className={`toast-notification ${toast.kind}`}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}

function memoryOriginLabel(origin: string): string {
  const value = String(origin || "");
  if (value.includes("task_reflection") || value.includes("memory_consolidator")) return "任务反思";
  if (value.includes("user")) return "用户明确设置";
  if (value.includes("llm")) return "Agent 反思";
  return value;
}

function memoryTypeLabel(memoryType: string): string {
  const map: Record<string, string> = {
    core_rule: "核心规则",
    semantic_fact: "稳定事实",
    episodic_case: "故障案例",
    procedural_rule: "操作方法",
    knowledge_note: "知识笔记",
    profile: "用户档案",
  };
  return map[memoryType] || memoryType;
}

function memoryAuthorityLabel(authority: string): string {
  const map: Record<string, string> = {
    explicit_user: "用户明确规则",
    manual_confirm: "人工确认",
    verified_tool: "工具证据确认",
    agent_inference: "Agent 推断",
  };
  return map[authority] || authority;
}

function memoryReasonLabel(reason: string): string {
  const map: Record<string, string> = {
    explicit_user_memory_command: "用户明确要求长期记住",
    explicit_user_forget_command: "用户明确要求忘记",
  };
  return map[reason] || reason;
}
