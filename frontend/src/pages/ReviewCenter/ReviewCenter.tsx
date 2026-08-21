import { useState } from "react";
import { reviewsApi } from "../../api";
import { useAsync, AsyncView, Badge, InlineCode } from "../../components/common";
import { useSessionStore } from "../../stores/session";
import { useToastStore } from "../../stores/toast";
import { isApiError } from "../../types";
import type { ReviewItem, ReviewStatus } from "../../types";
import { IconAlert, IconCheck, IconRefresh } from "../../components/Icon";
import { PortalModal } from "../../components/PortalModal";
import { PageHeader, FilterBar, DataTable } from "../../components/ui";

const STATUS_OPTIONS: { value: ReviewStatus | "all"; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "pending", label: "待处理" },
  { value: "accepted", label: "已接受" },
  { value: "ignored", label: "已忽略" },
  { value: "modified", label: "已修改" },
];

const STATUS_KIND: Record<ReviewStatus, "s-pending" | "s-accepted" | "s-ignored" | "s-modified"> = {
  pending: "s-pending", accepted: "s-accepted", ignored: "s-ignored", modified: "s-modified",
};
const STATUS_LABEL: Record<ReviewStatus, string> = {
  pending: "待处理", accepted: "已接受", ignored: "已忽略", modified: "已修改",
};

type ReviewDraft = { title: string; category: string; severity: "info" | "warning" | "error"; reason: string };
const EMPTY_DRAFT: ReviewDraft = { title: "", category: "人工复核", severity: "warning", reason: "" };

/** A durable human-review inbox. It records decisions without altering source artifacts. */
export function ReviewCenter() {
  const { currentWorkspaceId } = useSessionStore();
  const toast = useToastStore((s) => s.show);
  const [filter, setFilter] = useState<ReviewStatus | "all">("pending");
  const [editing, setEditing] = useState<ReviewItem | null>(null);
  const [creating, setCreating] = useState(false);
  const [note, setNote] = useState("");
  const [draft, setDraft] = useState<ReviewDraft>(EMPTY_DRAFT);
  const [saving, setSaving] = useState(false);

  const list = useAsync<{ items: ReviewItem[]; count: number }>(
    (signal) => currentWorkspaceId
      ? reviewsApi.list(currentWorkspaceId, filter === "all" ? undefined : filter, signal)
      : Promise.resolve({ items: [], count: 0 }),
    [currentWorkspaceId, filter],
    (data) => (data.items ?? []).length === 0,
  );

  function openCreate() {
    setDraft(EMPTY_DRAFT);
    setCreating(true);
  }

  async function onCreate() {
    if (!currentWorkspaceId || !draft.title.trim() || !draft.reason.trim()) return;
    setSaving(true);
    try {
      await reviewsApi.create(currentWorkspaceId, draft);
      toast({ kind: "success", title: "复核事项已发起", body: "已进入待处理收件箱" });
      setCreating(false);
      setDraft(EMPTY_DRAFT);
      setFilter("pending");
      list.reload();
    } catch (error: unknown) {
      toast({ kind: "error", title: "发起失败", body: isApiError(error) ? error.message : String(error), request_id: isApiError(error) ? error.request_id : undefined });
    } finally {
      setSaving(false);
    }
  }

  async function onSave() {
    if (!editing || !currentWorkspaceId) return;
    setSaving(true);
    try {
      await reviewsApi.update(editing.item_id, { status: editing.status, user_note: note, workspace_id: currentWorkspaceId, artifact_id: editing.artifact_id });
      toast({ kind: "success", title: "复核记录已更新", body: editing.title || editing.item_id });
      setEditing(null);
      setNote("");
      list.reload();
    } catch (error: unknown) {
      toast({ kind: "error", title: "更新失败", body: isApiError(error) ? error.message : String(error), request_id: isApiError(error) ? error.request_id : undefined });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="page review-center" data-testid="page-reviews">
      <PageHeader title="人工复核" subtitle={<>把需要确认的配置建议、巡检异常或流程失败放入收件箱；处理结果会留下可追溯备注。</>} />
      <div className="page-body">
        <div className="row-flex mb-2">
          <FilterBar>
            {STATUS_OPTIONS.map((option) => <button key={option.value} type="button" className={"btn sm " + (filter === option.value ? "primary" : "")} onClick={() => setFilter(option.value)} data-testid={`filter-${option.value}`}>{option.label}</button>)}
          </FilterBar>
          <span className="spacer" />
          <button className="btn primary" type="button" onClick={openCreate} data-testid="btn-create-review">发起复核</button>
        </div>
        {list.state.kind === "empty" ? (
          <ReviewEmptyState filter={filter} onCreate={openCreate} onReload={list.reload} onShowAll={() => setFilter("all")} />
        ) : (
          <AsyncView state={list.state} onRetry={list.reload} emptyText="暂无复核记录" emptyHint="发起一条复核，或切换筛选条件查看历史记录">
            {(data) => <DataTable data-testid="review-tbl" rows={data.items ?? []} keyExtractor={(item) => item.item_id} rowDataTestId={(item) => `review-${item.item_id}`} empty={{ text: "暂无复核记录", hint: "发起一条复核，或切换筛选条件查看历史记录" }} columns={[
              { key: "reason", header: "复核事项", render: (item) => <><div className="text-sm"><strong>{item.title || "人工复核"}</strong></div><div className="text-sm muted mt-1">{reviewReason(item)}</div><details className="collapse mt-1"><summary className="text-xs muted">技术详情</summary><div className="text-xs muted mt-1">记录：<InlineCode>{item.item_id}</InlineCode>{item.artifact_id && <> · 来源：<InlineCode>{item.artifact_id}</InlineCode></>}{item.category && <> · 类型：{item.category}</>}</div></details></> },
              { key: "severity", header: "影响", width: 90, render: (item) => <Badge kind={severityKind(item.severity)}>{severityLabel(item.severity)}</Badge> },
              { key: "status", header: "状态", width: 100, render: (item) => <Badge kind={STATUS_KIND[item.status]} withDot>{STATUS_LABEL[item.status]}</Badge> },
              { key: "note", header: "备注", render: (item) => <span className="text-sm muted">{item.user_note || "—"}</span> },
              { key: "actions", header: "操作", width: 90, align: "right", render: (item) => <button className="btn sm" type="button" onClick={() => { setEditing(item); setNote(item.user_note ?? ""); }} data-testid={`btn-edit-${item.item_id}`}>处理</button> },
            ]} />}
          </AsyncView>
        )}
      </div>

      <PortalModal open={creating} onClose={() => !saving && setCreating(false)} testId="review-create-modal" className="review-modal">
        <div className="modal-title">发起人工复核</div>
        <p className="text-sm muted mb-3">用于记录需要他人确认的巡检发现、配置建议或流程异常；不会直接执行设备变更。</p>
        <label>事项标题<input className="input" value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} placeholder="例如：核心路由器 BGP 邻居状态需确认" data-testid="review-create-title" /></label>
        <div className="row-flex mt-2"><label className="flex-1">类型<input className="input" value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value })} placeholder="巡检异常 / 配置建议" /></label><label>影响<select className="input" value={draft.severity} onChange={(event) => setDraft({ ...draft, severity: event.target.value as ReviewDraft["severity"] })}><option value="info">提示</option><option value="warning">需确认</option><option value="error">高影响</option></select></label></div>
        <label className="mt-2">需要确认什么<textarea className="input" rows={5} value={draft.reason} onChange={(event) => setDraft({ ...draft, reason: event.target.value })} placeholder="说明发现、影响范围、建议和需要确认的决策" data-testid="review-create-reason" /></label>
        <div className="modal-actions mt-3"><button className="btn" type="button" onClick={() => setCreating(false)} disabled={saving}>取消</button><button className="btn primary" type="button" onClick={() => void onCreate()} disabled={saving || !draft.title.trim() || !draft.reason.trim()} data-testid="btn-save-created-review">{saving ? "提交中…" : "加入复核收件箱"}</button></div>
      </PortalModal>

      <PortalModal open={!!editing} onClose={() => !saving && setEditing(null)} testId="review-modal" className="review-modal">
        {editing && <><div className="modal-title"><InlineCode>{editing.item_id}</InlineCode><Badge kind={STATUS_KIND[editing.status]} withDot>{STATUS_LABEL[editing.status]}</Badge></div><div className="text-sm muted mb-3 review-reason-box"><strong>{editing.title || "人工复核"}</strong><br />{editing.reason || editing.category || "(无说明)"}</div><textarea className="input" rows={4} value={note} onChange={(event) => setNote(event.target.value)} placeholder="填写复核备注（可选）" data-testid="review-note-input" /><div className="row-flex mt-3 review-modal-actions"><select className="input review-status-select" value={editing.status} onChange={(event) => setEditing({ ...editing, status: event.target.value as ReviewStatus })} data-testid="review-status-select">{(["pending", "accepted", "ignored", "modified"] as ReviewStatus[]).map((status) => <option key={status} value={status}>{STATUS_LABEL[status]}</option>)}</select><span className="spacer" /><div className="modal-actions review-modal-footer"><button type="button" className="btn" onClick={() => setEditing(null)} disabled={saving}>取消</button><button type="button" className="btn primary" onClick={() => void onSave()} disabled={saving} data-testid="btn-save-review">{saving ? "保存中…" : "保存"}</button></div></div></>}
      </PortalModal>
    </div>
  );
}

function ReviewEmptyState({ filter, onCreate, onReload, onShowAll }: { filter: ReviewStatus | "all"; onCreate: () => void; onReload: () => void; onShowAll: () => void }) {
  const isPending = filter === "pending";
  return <div className="review-empty" data-testid="review-empty-state"><div className="review-empty-icon">{isPending ? <IconCheck size={22} /> : <IconAlert size={22} />}</div><div><h2>{isPending ? "当前没有待处理复核" : "这个筛选下没有复核项"}</h2><p>你可以直接发起复核；流程运行失败时也会自动进入这里，供人工判断和留痕。</p></div><div className="review-empty-steps"><span>1. 记录需要确认的异常、建议或风险</span><span>2. 相关人员填写处理结论和备注</span><span>3. 历史状态可随时查询与追溯</span></div><div className="row-flex review-empty-actions"><button className="btn primary" type="button" onClick={onCreate}>发起复核</button><button className="btn" type="button" onClick={onReload}><IconRefresh size={12} /> 刷新</button>{!isPending && <button className="btn" type="button" onClick={onShowAll}>查看全部</button>}</div></div>;
}

function severityKind(severity: ReviewItem["severity"]): "err" | "warn" | "info" { return severity === "error" ? "err" : severity === "warning" ? "warn" : "info"; }
function severityLabel(severity: ReviewItem["severity"]): string { return severity === "error" ? "高影响" : severity === "warning" ? "需确认" : "提示"; }
function reviewReason(item: ReviewItem): string { return item.reason || item.category || "需要人工确认后再继续"; }
