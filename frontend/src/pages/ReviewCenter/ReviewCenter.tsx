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
  pending: "s-pending",
  accepted: "s-accepted",
  ignored: "s-ignored",
  modified: "s-modified",
};

const STATUS_LABEL: Record<ReviewStatus, string> = {
  pending: "待处理",
  accepted: "已接受",
  ignored: "已忽略",
  modified: "已修改",
};

/**
 * Review Center — list / update review items. Never mutates the
 * original artifact; only writes `user_note` and `status`.
 *
 * Real backend endpoints (v1.0.1):
 *   GET /api/workspaces/<ws_id>/review-items?status=
 *   PUT /api/review-items/<item_id>?workspace_id=&artifact_id=
 */
export function ReviewCenter() {
  const { currentWorkspaceId } = useSessionStore();
  const toast = useToastStore((s) => s.show);
  const [filter, setFilter] = useState<ReviewStatus | "all">("pending");
  const [editing, setEditing] = useState<ReviewItem | null>(null);
  const [note, setNote] = useState("");

  const list = useAsync<{ items: ReviewItem[]; count: number }>(
    (s) =>
      currentWorkspaceId
        ? reviewsApi.list(currentWorkspaceId, filter === "all" ? undefined : filter, s)
        : Promise.resolve({ items: [], count: 0 }),
    [currentWorkspaceId, filter],
    (d) => (d.items ?? []).length === 0,
  );

  async function onSave() {
    if (!editing || !currentWorkspaceId) return;
    const artifact_id = editing.artifact_id;
    if (!artifact_id) {
      toast({
        kind: "error",
        title: "无法更新",
        body: "这条复核记录缺少关联产出，暂时无法定位原始内容。",
      });
      return;
    }
    try {
      await reviewsApi.update(editing.item_id, {
        status: editing.status,
        user_note: note,
        workspace_id: currentWorkspaceId,
        artifact_id,
      });
      toast({ kind: "success", title: "复核记录已更新", body: editing.item_id });
      setEditing(null);
      setNote("");
      list.reload();
    } catch (e: unknown) {
      toast({
        kind: "error",
        title: "更新失败",
        body: isApiError(e) ? e.message : String(e),
        request_id: isApiError(e) ? e.request_id : undefined,
      });
    }
  }

  return (
    <div className="page review-center" data-testid="page-reviews">
      <PageHeader
        title="人工复核"
        subtitle={<>记录人工判断和备注，<strong>不</strong>修改原始任务产出</>}
      />
      <div className="page-body">
        <FilterBar className="mb-2">
          {STATUS_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={"btn sm " + (filter === o.value ? "primary" : "")}
              onClick={() => setFilter(o.value)}
              data-testid={`filter-${o.value}`}
            >
              {o.label}
            </button>
          ))}
        </FilterBar>

        {list.state.kind === "empty" ? (
          <ReviewEmptyState
            filter={filter}
            onReload={list.reload}
            onShowAll={() => setFilter("all")}
          />
        ) : (
          <AsyncView
            state={list.state}
            onRetry={list.reload}
            emptyText="暂无复核记录"
            emptyHint="切换筛选条件，或等待需要人工确认的任务结果"
          >
            {(d) => (
              <DataTable
                data-testid="review-tbl"
                rows={d.items ?? []}
                keyExtractor={(it) => it.item_id}
                rowDataTestId={(it) => `review-${it.item_id}`}
                empty={{ text: "暂无复核记录", hint: "切换筛选条件，或等待需要人工确认的任务结果" }}
                columns={[
                  {
                    key: "reason",
                    header: "需要你看的问题",
                    render: (it) => {
                      const artifactId = it.artifact_id;
                      return (
                        <>
                          <div className="text-sm">{reviewReason(it)}</div>
                          <details className="collapse mt-1">
                            <summary className="text-xs muted">技术详情</summary>
                            <div className="text-xs muted mt-1">
                              记录：<InlineCode>{it.item_id}</InlineCode>
                              {artifactId && <> · 关联产出：<InlineCode>{artifactId}</InlineCode></>}
                              {it.category && (
                                <> · 类型：{it.category}</>
                              )}
                            </div>
                          </details>
                        </>
                      );
                    },
                  },
                  { key: "severity", header: "影响", width: 90, render: (it) => <Badge kind={severityKind(it.severity)}>{severityLabel(it.severity)}</Badge> },
                  { key: "status", header: "状态", width: 100, render: (it) => <Badge kind={STATUS_KIND[it.status]} withDot>{STATUS_LABEL[it.status]}</Badge> },
                  { key: "note", header: "备注", render: (it) => <span className="text-sm muted">{it.user_note || "—"}</span> },
                  {
                    key: "actions",
                    header: "操作",
                    width: 90,
                    align: "right",
                    render: (it) => (
                      <button
                        className="btn sm"
                        type="button"
                        onClick={() => {
                          setEditing(it);
                          setNote(it.user_note ?? "");
                        }}
                        data-testid={`btn-edit-${it.item_id}`}
                      >
                        编辑
                      </button>
                    ),
                  },
                ]}
              />
            )}
          </AsyncView>
        )}
      </div>

      <PortalModal
        open={!!editing}
        onClose={() => setEditing(null)}
        testId="review-modal"
        className="review-modal"
      >
        {editing && (
          <>
            <div className="modal-title">
              <InlineCode>{editing.item_id}</InlineCode>
              <Badge kind={STATUS_KIND[editing.status]} withDot>
                {STATUS_LABEL[editing.status]}
              </Badge>
            </div>
            <div className="text-sm muted mb-3 review-reason-box">
              {editing.reason ||
                editing.category ||
                "(无说明)"}
            </div>
            <textarea
              className="input"
              rows={4}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="填写复核备注（可选）"
              data-testid="review-note-input"
            />
            <div className="row-flex mt-3 review-modal-actions">
              <select
                className="input review-status-select"
                value={editing.status}
                onChange={(e) =>
                  setEditing({ ...editing, status: e.target.value as ReviewStatus })
                }
                data-testid="review-status-select"
              >
                {(["pending", "accepted", "ignored", "modified"] as ReviewStatus[]).map(
                  (s) => (
                    <option key={s} value={s}>
                      {STATUS_LABEL[s]}
                    </option>
                  ),
                )}
              </select>
              <span className="spacer" />
              <div className="modal-actions review-modal-footer">
                <button
                  type="button"
                  className="btn"
                  onClick={() => setEditing(null)}
                >
                  取消
                </button>
                <button
                  type="button"
                  className="btn primary"
                  onClick={onSave}
                  data-testid="btn-save-review"
                >
                  保存
                </button>
              </div>
            </div>
          </>
        )}
      </PortalModal>
    </div>
  );
}

function ReviewEmptyState({
  filter,
  onReload,
  onShowAll,
}: {
  filter: ReviewStatus | "all";
  onReload: () => void;
  onShowAll: () => void;
}) {
  const isPending = filter === "pending";
  return (
    <div className="review-empty" data-testid="review-empty-state">
      <div className="review-empty-icon">
        {isPending ? <IconCheck size={22} /> : <IconAlert size={22} />}
      </div>
      <div>
        <h2>{isPending ? "当前没有待处理评审" : "这个筛选下没有评审项"}</h2>
        <p>
          这里只收集需要人工确认的结果，例如高风险动作、敏感内容或需要复核的任务产出。
        </p>
      </div>
      <div className="review-empty-steps">
        <span>1. 在工作台发起一次网络任务</span>
        <span>2. 有风险的任务产出会自动进入这里</span>
        <span>3. 人工接受、忽略或备注后再继续</span>
      </div>
      <div className="row-flex review-empty-actions">
        <button className="btn primary" type="button" onClick={onReload}>
          <IconRefresh size={12} /> 刷新
        </button>
        {!isPending && (
          <button className="btn" type="button" onClick={onShowAll}>
            查看全部
          </button>
        )}
      </div>
    </div>
  );
}

function severityKind(severity: ReviewItem["severity"]): "err" | "warn" | "info" {
  if (severity === "error") return "err";
  if (severity === "warning") return "warn";
  return "info";
}

function severityLabel(severity: ReviewItem["severity"]): string {
  if (severity === "error") return "高影响";
  if (severity === "warning") return "需确认";
  return "提示";
}

function reviewReason(item: ReviewItem): string {
  return item.reason ||
    item.category ||
    "需要人工确认后再继续";
}
