import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { apiRequest } from "../../api/client";
import {
  archiveApi,
  artifactsApi,
  retentionApi,
  storageApi,
  type ArtifactGovernanceSummary,
  type LifecyclePreview,
} from "../../api";
import { Badge, CodeBlock, EmptyState, LoadingState } from "../../components/common";
import { confirm } from "../../components/ConfirmDialog";
import { Button, DetailPanel, FilterBar, Input, PageHeader } from "../../components/ui";
import { useSessionStore } from "../../stores/session";
import { useToastStore } from "../../stores/toast";
import type { ArchivedDataItem, Artifact, DataOverview, ManagedFile } from "../../types";
import { isApiError } from "../../types";
import { formatFileSize, formatDate } from "../../utils/format";
import { shortId } from "../../utils/displayText";

type DataTab = "overview" | "files" | "artifacts" | "relations" | "lifecycle";
type ArtifactView = "" | "current" | "history" | "deliverables";

const TAB_LABELS: Array<[DataTab, string]> = [
  ["overview", "总览"],
  ["files", "全部数据"],
  ["artifacts", "证据与制品"],
  ["relations", "关系与来源"],
  ["lifecycle", "生命周期"],
];

const TYPE_LABELS: Record<string, string> = {
  user_upload: "上传文件",
  chat_attachment: "会话附件",
  document_input: "文档",
  data_input: "数据",
  knowledge_normalized: "知识文档",
  artifact_output: "任务产出",
  report: "报告",
  message_large_content: "大消息",
};

const SOURCE_LABELS: Record<string, string> = {
  artifact_upload: "用户上传",
  knowledge_import: "知识库",
  agent: "智能体",
  module_output: "模块产出",
};

export function DataCenter() {
  const workspaceId = useSessionStore((state) => state.currentWorkspaceId);
  const toast = useToastStore((state) => state.show);
  const [searchParams, setSearchParams] = useSearchParams();
  const producerId = searchParams.get("producer_id") || "";
  const [tab, setTab] = useState<DataTab>(producerId ? "artifacts" : "overview");
  const [overview, setOverview] = useState<DataOverview | null>(null);
  const [files, setFiles] = useState<ManagedFile[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [governance, setGovernance] = useState<ArtifactGovernanceSummary | null>(null);
  const [retention, setRetention] = useState<LifecyclePreview | null>(null);
  const [archive, setArchive] = useState<LifecyclePreview | null>(null);
  const [archivedItems, setArchivedItems] = useState<ArchivedDataItem[]>([]);
  const [artifactView, setArtifactView] = useState<ArtifactView>("");
  const [selectedFile, setSelectedFile] = useState<ManagedFile | null>(null);
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
  const [selectedFileIds, setSelectedFileIds] = useState<string[]>([]);
  const [content, setContent] = useState<string>("");
  const [contentNote, setContentNote] = useState<string>("");
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const uploadRef = useRef<HTMLInputElement>(null);
  const contentAbort = useRef<AbortController | null>(null);

  const loadData = useCallback(async (signal?: AbortSignal) => {
    if (!workspaceId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    const results = await Promise.allSettled([
      storageApi.overview(workspaceId, signal),
      storageApi.files(workspaceId, "active", signal),
      retentionApi.preview(workspaceId, signal),
      archiveApi.preview(workspaceId, signal),
      archiveApi.items(workspaceId, signal),
    ]);
    if (signal?.aborted) return;
    const [overviewResult, filesResult, retentionResult, archiveResult, archivedResult] = results;
    if (overviewResult.status === "fulfilled") setOverview(overviewResult.value.overview);
    if (filesResult.status === "fulfilled") setFiles(filesResult.value.files || []);
    if (retentionResult.status === "fulfilled") setRetention(retentionResult.value);
    if (archiveResult.status === "fulfilled") setArchive(archiveResult.value);
    if (archivedResult.status === "fulfilled") setArchivedItems(archivedResult.value.items || []);
    const failures = results.filter((item) => item.status === "rejected") as PromiseRejectedResult[];
    if (failures.length) setError(isApiError(failures[0].reason) ? failures[0].reason.message : String(failures[0].reason));
    setLoading(false);
  }, [workspaceId]);

  const loadArtifacts = useCallback(async (signal?: AbortSignal) => {
    if (!workspaceId) return;
    try {
      const response = await artifactsApi.list(workspaceId, signal, artifactView, producerId);
      if (!signal?.aborted) {
        setArtifacts(response.artifacts || []);
        setGovernance(response.governance || null);
      }
    } catch (reason) {
      if (!signal?.aborted) setError(isApiError(reason) ? reason.message : String(reason));
    }
  }, [workspaceId, artifactView, producerId]);

  useEffect(() => {
    const controller = new AbortController();
    void loadData(controller.signal);
    return () => controller.abort();
  }, [loadData]);

  useEffect(() => {
    const controller = new AbortController();
    void loadArtifacts(controller.signal);
    return () => controller.abort();
  }, [loadArtifacts]);

  useEffect(() => {
    if (producerId) setTab("artifacts");
  }, [producerId]);

  useEffect(() => () => contentAbort.current?.abort(), []);

  useEffect(() => {
    if (!workspaceId || typeof EventSource === "undefined") return;
    const stream = storageApi.events(workspaceId);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        void loadData();
        void loadArtifacts();
      }, 120);
    };
    stream.addEventListener("storage_changed", refresh);
    return () => {
      if (timer) clearTimeout(timer);
      stream.removeEventListener("storage_changed", refresh);
      stream.close();
    };
  }, [workspaceId, loadData, loadArtifacts]);

  const typeOptions = useMemo(
    () => Array.from(new Set(files.map((file) => file.logical_type))).sort(),
    [files],
  );
  const filteredFiles = useMemo(() => {
    const query = search.trim().toLowerCase();
    return files.filter((file) => {
      if (typeFilter !== "all" && file.logical_type !== typeFilter) return false;
      if (!query) return true;
      return [file.original_name, file.file_id, file.logical_type, file.source, file.run_id]
        .some((value) => String(value || "").toLowerCase().includes(query));
    });
  }, [files, search, typeFilter]);

  useEffect(() => {
    const currentIds = new Set(files.map((file) => file.file_id));
    setSelectedFileIds((ids) => ids.filter((id) => currentIds.has(id)));
  }, [files]);

  const selectFile = async (file: ManagedFile) => {
    setSelectedArtifact(null);
    setSelectedFile(file);
    setContent("");
    setContentNote(file.binary ? "二进制文件不提供文本预览" : "正在读取内容…");
    contentAbort.current?.abort();
    if (file.binary || !workspaceId) return;
    const controller = new AbortController();
    contentAbort.current = controller;
    try {
      const result = await storageApi.content(workspaceId, file.file_id, controller.signal);
      if (!controller.signal.aborted) {
        setContent(result.content || "");
        setContentNote(result.truncated ? "内容较大，当前只显示前 100,000 个字符" : "");
      }
    } catch (reason) {
      if (!controller.signal.aborted) setContentNote(isApiError(reason) ? reason.message : String(reason));
    }
  };

  const selectArtifact = async (artifact: Artifact) => {
    setSelectedFile(null);
    setSelectedArtifact(artifact);
    setContent("");
    setContentNote("正在读取内容…");
    contentAbort.current?.abort();
    if (!workspaceId) return;
    const controller = new AbortController();
    contentAbort.current = controller;
    try {
      const result = await artifactsApi.content(workspaceId, artifact.artifact_id, controller.signal);
      if (!controller.signal.aborted) {
        setContent(result.content || "");
        setContentNote("");
      }
    } catch (reason) {
      if (!controller.signal.aborted) setContentNote(isApiError(reason) ? reason.message : String(reason));
    }
  };

  const upload = async (file: File) => {
    if (!workspaceId) return;
    setBusy(true);
    const form = new FormData();
    form.append("workspace_id", workspaceId);
    form.append("file", file);
    form.append("artifact_type", "user_upload");
    form.append("title", file.name);
    try {
      await apiRequest({ method: "POST", url: `/workspaces/${workspaceId}/artifacts/upload`, data: form });
      toast({ kind: "success", title: "数据已导入", body: `${file.name} 已进入数据中心` });
      await Promise.all([loadData(), loadArtifacts()]);
      setTab("files");
    } catch (reason) {
      toast({ kind: "error", title: "导入失败", body: isApiError(reason) ? reason.message : String(reason) });
    } finally {
      setBusy(false);
    }
  };

  const deleteFiles = async (targets: ManagedFile[]) => {
    if (!workspaceId) return;
    const artifactIds = Array.from(new Set(targets.flatMap((file) => file.artifacts.filter((item) => item.lifecycle !== "deleted").map((item) => item.artifact_id))));
    const standaloneFiles = targets.filter((file) => !file.artifacts.some((item) => item.lifecycle !== "deleted"));
    const referencedCount = targets.filter((file) => file.reference_count > 0).length;
    const accepted = await confirm({
      title: targets.length > 1 ? `删除 ${targets.length} 项数据？` : artifactIds.length ? "删除文件及关联制品？" : "永久删除文件？",
      body: [
        `将删除 ${targets.length} 项数据${artifactIds.length ? `，包含 ${artifactIds.length} 个关联制品` : ""}。`,
        referencedCount ? `其中 ${referencedCount} 项存在引用关系，系统会同步清理这些引用记录。` : "",
        "删除后无法恢复。",
      ].filter(Boolean).join(" "),
      confirmLabel: "确认删除",
      destructive: true,
    });
    if (!accepted) return;
    setBusy(true);
    try {
      const artifactResult = artifactIds.length ? await artifactsApi.batchDelete(workspaceId, artifactIds) : null;
      const standaloneResults = await Promise.allSettled(standaloneFiles.map((file) => storageApi.delete(workspaceId, file.file_id)));
      const failedStandalone = standaloneResults
        .map((item, index) => ({ item, file: standaloneFiles[index] }))
        .filter((entry): entry is { item: PromiseRejectedResult; file: ManagedFile } => entry.item.status === "rejected");
      const artifactDeleted = artifactResult?.deleted ?? 0;
      const artifactFailed = Math.max(0, (artifactResult?.total ?? 0) - artifactDeleted);
      if (failedStandalone.length || artifactFailed) {
        const firstFailed = failedStandalone[0];
        const failedName = firstFailed?.file.original_name || firstFailed?.file.file_id || (artifactFailed ? `${artifactFailed} 个制品` : "");
        const failedReason = firstFailed ? (isApiError(firstFailed.item.reason) ? firstFailed.item.reason.message : String(firstFailed.item.reason)) : "部分关联制品删除失败";
        throw new Error(`${failedName} 删除失败：${failedReason}`);
      }
      const deletedCount = standaloneResults.length + artifactDeleted;
      setSelectedFile((current) => current && targets.some((file) => file.file_id === current.file_id) ? null : current);
      setSelectedFileIds((ids) => ids.filter((id) => !targets.some((file) => file.file_id === id)));
      toast({ kind: "success", title: "已删除", body: `已处理 ${deletedCount} 项数据。` });
      await Promise.all([loadData(), loadArtifacts()]);
    } catch (reason) {
      toast({ kind: "error", title: "删除失败", body: errorMessage(reason) });
    } finally {
      setBusy(false);
    }
  };

  const deleteFile = async (file: ManagedFile) => {
    await deleteFiles([file]);
  };

  const deleteArtifact = async (artifact: Artifact) => {
    if (!workspaceId) return;
    const accepted = await confirm({
      title: "删除制品？",
      body: `将删除「${artifact.title || artifact.artifact_id}」及其专属文件。此操作无法恢复。`,
      confirmLabel: "确认删除",
      destructive: true,
    });
    if (!accepted) return;
    try {
      await artifactsApi.batchDelete(workspaceId, [artifact.artifact_id]);
      setSelectedArtifact(null);
      await Promise.all([loadData(), loadArtifacts()]);
      toast({ kind: "success", title: "制品已删除" });
    } catch (reason) {
      toast({ kind: "error", title: "删除失败", body: isApiError(reason) ? reason.message : String(reason) });
    }
  };

  const applyLifecycle = async (kind: "retention" | "archive") => {
    if (!workspaceId) return;
    const preview = kind === "retention" ? retention : archive;
    const count = sumCounts(preview?.candidate_counts);
    if (!count) return;
    const accepted = await confirm({
      title: kind === "retention" ? "清理到期数据？" : "归档历史数据？",
      body: kind === "retention"
        ? `将永久清理 ${count} 项已到期数据。系统会保护仍被会话和制品引用的内容。`
        : `将把 ${count} 项历史数据移入归档区，之后可在本页恢复。`,
      confirmLabel: kind === "retention" ? "确认清理" : "确认归档",
      destructive: kind === "retention",
    });
    if (!accepted) return;
    setBusy(true);
    try {
      if (kind === "retention") await retentionApi.apply(workspaceId);
      else await archiveApi.apply(workspaceId);
      await loadData();
      toast({ kind: "success", title: kind === "retention" ? "清理完成" : "归档完成" });
    } catch (reason) {
      toast({ kind: "error", title: "执行失败", body: isApiError(reason) ? reason.message : String(reason) });
    } finally {
      setBusy(false);
    }
  };

  const restoreArchived = async (item: ArchivedDataItem) => {
    if (!workspaceId) return;
    try {
      await archiveApi.restore(workspaceId, item);
      await loadData();
      toast({ kind: "success", title: "已恢复", body: item.name });
    } catch (reason) {
      toast({ kind: "error", title: "恢复失败", body: isApiError(reason) ? reason.message : String(reason) });
    }
  };

  return (
    <div className="page data-center" data-testid="page-data-center">
      <PageHeader title="数据中心" subtitle="统一管理文件、证据制品、引用关系与数据生命周期">
        <input ref={uploadRef} type="file" className="file-upload-input" onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void upload(file);
          event.target.value = "";
        }} />
        <Button variant="primary" size="sm" disabled={busy} onClick={() => uploadRef.current?.click()}>
          {busy ? "处理中…" : "导入数据"}
        </Button>
        <Button size="sm" onClick={() => { void loadData(); void loadArtifacts(); }}>刷新</Button>
      </PageHeader>

      <FilterBar className="data-center-tabs">
        {TAB_LABELS.map(([key, label]) => (
          <Button key={key} size="sm" variant={tab === key ? "primary" : "default"} onClick={() => {
            setTab(key); setSelectedFile(null); setSelectedArtifact(null);
          }}>{label}</Button>
        ))}
        <div className="spacer" />
        {overview && <Badge kind={overview.health.ok ? "ok" : "err"}>{overview.health.ok ? "数据关系正常" : "发现数据问题"}</Badge>}
      </FilterBar>

      {error && <div className="callout error">{error}</div>}
      {loading && !overview ? <LoadingState text="正在读取数据平面…" /> : null}

      {tab === "overview" && <Overview overview={overview} files={files} onOpenFiles={() => setTab("files")} onOpenLifecycle={() => setTab("lifecycle")} />}
      {tab === "files" && (
        <FilesView
          files={filteredFiles} search={search} onSearch={setSearch}
          typeFilter={typeFilter} typeOptions={typeOptions} onTypeFilter={setTypeFilter}
          busy={busy}
          selected={selectedFile} onSelect={(file) => void selectFile(file)}
          selectedIds={selectedFileIds} onSelectedIds={setSelectedFileIds} onDelete={(file) => void deleteFile(file)}
          onBulkDelete={(targets) => void deleteFiles(targets)}
      detail={<FileDetail file={selectedFile} content={content} note={contentNote} busy={busy} onDelete={deleteFile} />}
        />
      )}
      {tab === "artifacts" && (
        <ArtifactsView
          artifacts={artifacts} governance={governance} view={artifactView} onView={setArtifactView}
          producerId={producerId} onClearProducer={() => {
            setSearchParams({}, { replace: true });
          }}
          selected={selectedArtifact} onSelect={(artifact) => void selectArtifact(artifact)}
          detail={<ArtifactDetail artifact={selectedArtifact} content={content} note={contentNote} onDelete={deleteArtifact} />}
        />
      )}
      {tab === "relations" && <RelationsView files={filteredFiles} search={search} onSearch={setSearch} onSelect={(file) => { setTab("files"); void selectFile(file); }} />}
      {tab === "lifecycle" && (
        <LifecycleView retention={retention} archive={archive} archivedItems={archivedItems} busy={busy} onApply={applyLifecycle} onRestore={restoreArchived} />
      )}
    </div>
  );
}

function Overview({ overview, files, onOpenFiles, onOpenLifecycle }: { overview: DataOverview | null; files: ManagedFile[]; onOpenFiles: () => void; onOpenLifecycle: () => void }) {
  if (!overview) return <EmptyState text="暂无数据概览" />;
  return <div className="data-overview">
    <div className="data-stat-grid">
      <Stat label="活跃文件" value={overview.files.active} hint={formatFileSize(overview.files.size_bytes)} />
      <Stat label="证据与制品" value={overview.artifacts.active} hint="可追溯业务产出" />
      <Stat label="已有业务引用" value={overview.files.referenced} hint="受关系保护" />
      <Stat label="独立文件" value={overview.files.unreferenced} hint="可直接管理" />
      <Stat label="已归档" value={overview.files.archived} hint="可恢复历史数据" />
      <Stat label="数据健康" value={overview.health.ok ? "正常" : "异常"} hint={`断链 ${overview.health.missing_on_disk} · 孤儿 ${overview.health.orphan_files}`} tone={overview.health.ok ? "ok" : "err"} />
    </div>
    <div className="data-overview-grid">
      <section className="card data-overview-card">
        <div className="card-title">数据构成</div>
        {Object.entries(overview.types).length ? Object.entries(overview.types).sort((a, b) => b[1] - a[1]).map(([type, count]) => (
          <div className="data-breakdown-row" key={type}><span>{typeLabel(type)}</span><b>{count}</b></div>
        )) : <EmptyState text="尚无数据" hint="导入文件或运行任务后会显示在这里" />}
        <Button size="sm" onClick={onOpenFiles}>查看全部数据</Button>
      </section>
      <section className="card data-overview-card">
        <div className="card-title">最近数据</div>
        {files.slice(0, 6).map((file) => <div className="data-recent-row" key={file.file_id}>
          <span><b>{file.original_name || file.file_id}</b><small>{typeLabel(file.logical_type)} · {sourceLabel(file.source)}</small></span>
          <span>{formatFileSize(file.size_bytes)}</span>
        </div>)}
        {!files.length && <EmptyState text="暂无文件" />}
      </section>
      <section className="card data-overview-card">
        <div className="card-title">生命周期</div>
        <p className="text-sm dim">清理和归档执行前会计算候选项，并保护仍被会话、任务和制品引用的数据。</p>
        <div className="data-breakdown-row"><span>待处理软删除</span><b>{overview.files.soft_deleted}</b></div>
        <div className="data-breakdown-row"><span>已归档文件</span><b>{overview.files.archived}</b></div>
        <Button size="sm" onClick={onOpenLifecycle}>管理生命周期</Button>
      </section>
    </div>
  </div>;
}

function Stat({ label, value, hint, tone = "" }: { label: string; value: string | number; hint: string; tone?: string }) {
  return <div className={`card data-stat ${tone}`}><span>{label}</span><strong>{value}</strong><small>{hint}</small></div>;
}

function FilesView({ files, search, onSearch, typeFilter, typeOptions, onTypeFilter, busy, selected, onSelect, selectedIds, onSelectedIds, onDelete, onBulkDelete, detail }: {
  files: ManagedFile[]; search: string; onSearch: (value: string) => void; typeFilter: string; typeOptions: string[];
  onTypeFilter: (value: string) => void; busy: boolean; selected: ManagedFile | null; onSelect: (file: ManagedFile) => void;
  selectedIds: string[]; onSelectedIds: (ids: string[]) => void; onDelete: (file: ManagedFile) => void; onBulkDelete: (files: ManagedFile[]) => void; detail: ReactNode;
}) {
  const selectedIdSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const selectedFiles = useMemo(() => files.filter((file) => selectedIdSet.has(file.file_id)), [files, selectedIdSet]);
  const allVisibleSelected = files.length > 0 && files.every((file) => selectedIdSet.has(file.file_id));
  const toggleAll = () => {
    const visibleIds = new Set(files.map((file) => file.file_id));
    if (allVisibleSelected) onSelectedIds(selectedIds.filter((id) => !visibleIds.has(id)));
    else onSelectedIds(Array.from(new Set([...selectedIds, ...visibleIds])));
  };
  const toggleOne = (fileId: string) => {
    onSelectedIds(selectedIdSet.has(fileId) ? selectedIds.filter((id) => id !== fileId) : [...selectedIds, fileId]);
  };
  const rowKeyDown = (event: KeyboardEvent<HTMLDivElement>, file: ManagedFile) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(file);
    }
  };

  return <>
    <FilterBar className="data-file-filters">
      <Input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="搜索文件名、ID、来源或任务" aria-label="搜索数据" />
      <select className="select" value={typeFilter} onChange={(event) => onTypeFilter(event.target.value)} aria-label="按数据类型筛选">
        <option value="all">全部类型</option>
        {typeOptions.map((type) => <option key={type} value={type}>{typeLabel(type)}</option>)}
      </select>
      <span className="status-pill">{files.length} 项</span>
    </FilterBar>
    <div className="split-shell data-split">
      <aside className="data-list" aria-label="数据列表">
        <div className="data-list-toolbar">
          <label className="data-check-all">
            <input type="checkbox" checked={allVisibleSelected} disabled={!files.length || busy} onChange={toggleAll} />
            <span>全选</span>
          </label>
          <span className="data-list-count">{selectedFiles.length ? `已选 ${selectedFiles.length}` : `${files.length} 项`}</span>
          <Button size="sm" variant="danger-ghost" disabled={!selectedFiles.length || busy} onClick={() => onBulkDelete(selectedFiles)}>批量删除</Button>
        </div>
        <div className="data-list-scroll">
          {!files.length && <EmptyState text="没有符合条件的数据" hint="调整筛选条件或导入文件" />}
          {files.map((file) => {
            return <div
              key={file.file_id}
              role="button"
              tabIndex={0}
              className={`data-row ${selected?.file_id === file.file_id ? "selected" : ""}`}
              onClick={() => onSelect(file)}
              onKeyDown={(event) => rowKeyDown(event, file)}
            >
              <label className="data-row-check" title="选择删除" onClick={(event) => event.stopPropagation()}>
                <input type="checkbox" checked={selectedIdSet.has(file.file_id)} disabled={busy} onChange={() => toggleOne(file.file_id)} />
              </label>
              <span className="data-row-main">
                <b>{file.original_name || file.file_id}</b>
                <small>{typeLabel(file.logical_type)} · {sourceLabel(file.source)} · {formatFileSize(file.size_bytes)} · {formatDate(file.created_at, "short")}</small>
              </span>
              <span className="data-row-badges">
                {file.artifacts.length > 0 && <Badge kind="info">{file.artifacts.length} 个制品</Badge>}
                <Badge kind={file.reference_count ? "warn" : "muted"}>{file.reference_count ? `${file.reference_count} 个引用` : "独立文件"}</Badge>
              </span>
              <Button size="sm" variant="danger-ghost" disabled={busy} title="删除这项数据" onClick={(event) => { event.stopPropagation(); onDelete(file); }}>删除</Button>
            </div>;
          })}
        </div>
      </aside>
      {detail}
    </div>
  </>;
}

function FileDetail({ file, content, note, busy, onDelete }: { file: ManagedFile | null; content: string; note: string; busy: boolean; onDelete: (file: ManagedFile) => void }) {
  if (!file) return <DetailPanel empty={{ text: "选择一项数据", hint: "查看内容、来源、制品和引用关系" }} />;
  return <DetailPanel title={file.original_name || file.file_id} subtitle={`${typeLabel(file.logical_type)} · ${formatFileSize(file.size_bytes)}`} actions={<>
    <Button size="sm" variant="danger-ghost" disabled={busy} title="删除这项数据" onClick={() => onDelete(file)}>删除</Button>
  </>}>
    <div className="info-grid-3 data-info-grid">
      <Info label="文件 ID" value={file.file_id} mono />
      <Info label="来源" value={sourceLabel(file.source)} />
      <Info label="敏感级别" value={sensitivityLabel(file.sensitivity)} />
      <Info label="关联任务" value={file.run_id ? shortId(file.run_id) : "无"} />
      <Info label="引用数量" value={String(file.reference_count)} />
      <Info label="生命周期" value={lifecycleLabel(file.lifecycle)} />
    </div>
    <section className="data-detail-section">
      <h4>关联制品</h4>
      {file.artifacts.length ? file.artifacts.map((artifact) => <div className="data-relation-item" key={artifact.artifact_id}>
        <span><b>{artifact.title || artifact.artifact_id}</b><small>{artifact.artifact_type}</small></span>
        <Badge kind={artifact.lifecycle === "active" ? "ok" : "muted"}>{artifact.lifecycle}</Badge>
      </div>) : <p className="dim text-sm">当前没有关联的证据或任务制品。</p>}
    </section>
    <section className="data-detail-section">
      <h4>业务引用</h4>
      {file.references.length ? file.references.map((reference, index) => <div className="data-relation-item" key={`${reference.owner_type}-${reference.owner_id}-${index}`}>
        <span><b>{referenceTypeLabel(reference.owner_type)}</b><small>{relationLabel(reference.relation)}</small></span>
        <span className="mono text-sm">{shortId(reference.owner_id)}</span>
      </div>) : <p className="dim text-sm">没有业务对象引用，可直接删除。</p>}
    </section>
    <section className="data-detail-section"><h4>内容预览</h4>{note && <p className="dim text-sm">{note}</p>}{content && <CodeBlock>{content}</CodeBlock>}</section>
  </DetailPanel>;
}

function ArtifactsView({ artifacts, governance, view, onView, producerId, onClearProducer, selected, onSelect, detail }: {
  artifacts: Artifact[]; governance: ArtifactGovernanceSummary | null; view: ArtifactView; onView: (view: ArtifactView) => void;
  producerId: string; onClearProducer: () => void; selected: Artifact | null; onSelect: (artifact: Artifact) => void; detail: ReactNode;
}) {
  return <>
    <FilterBar>
      {([ ["", "全部"], ["current", "当前证据"], ["history", "历史与不完整"], ["deliverables", "业务交付物"] ] as Array<[ArtifactView, string]>).map(([key, label]) => (
        <Button key={key || "all"} size="sm" variant={view === key ? "primary" : "default"} onClick={() => onView(key)}>{label}</Button>
      ))}
      {producerId && <span className="status-pill">任务 {shortId(producerId)} <button type="button" className="link-button" onClick={onClearProducer}>清除</button></span>}
      <div className="spacer" />
      {governance && <><Badge kind="ok">证据流 {governance.evidence_streams || 0}</Badge><Badge kind="muted">交付物 {governance.deliverables || 0}</Badge></>}
    </FilterBar>
    <div className="split-shell data-split">
      <aside className="data-list" aria-label="制品列表">
        {!artifacts.length && <EmptyState text="暂无制品" hint="分析、报告或 Agent 任务的产出会显示在这里" />}
        {artifacts.map((artifact) => <button key={artifact.artifact_id} type="button" className={`data-row ${selected?.artifact_id === artifact.artifact_id ? "selected" : ""}`} onClick={() => onSelect(artifact)}>
          <span className="data-row-main"><b>{artifact.title || artifact.artifact_id}</b><small>{artifact.artifact_type} · {sourceLabel(artifact.source)}</small></span>
          <span className="data-row-badges"><AuthorityBadge artifact={artifact} /></span>
          <span className="data-row-meta">{formatFileSize(artifact.size_bytes)} · {formatDate(artifact.created_at, "short")}</span>
        </button>)}
      </aside>
      {detail}
    </div>
  </>;
}

function ArtifactDetail({ artifact, content, note, onDelete }: { artifact: Artifact | null; content: string; note: string; onDelete: (artifact: Artifact) => void }) {
  if (!artifact) return <DetailPanel empty={{ text: "选择一件制品", hint: "查看证据地位、来源和内容" }} />;
  return <DetailPanel title={artifact.title || artifact.artifact_id} subtitle={`${artifact.artifact_type} · ${formatFileSize(artifact.size_bytes)}`} actions={<Button size="sm" variant="danger-ghost" onClick={() => onDelete(artifact)}>删除</Button>}>
    <div className="info-grid-3 data-info-grid">
      <Info label="证据地位" value={authorityLabel(artifact)} />
      <Info label="来源" value={sourceLabel(artifact.source)} />
      <Info label="关联任务" value={artifact.run_id ? shortId(artifact.run_id) : "无"} />
      <Info label="文件 ID" value={artifact.file_id || "无"} mono />
      <Info label="敏感级别" value={artifact.sensitivity} />
      <Info label="生命周期" value={artifact.lifecycle} />
    </div>
    {artifact.governance?.authority_reason && <div className="callout info">{artifact.governance.authority_reason}</div>}
    <section className="data-detail-section"><h4>内容预览</h4>{note && <p className="dim text-sm">{note}</p>}{content && <CodeBlock>{content}</CodeBlock>}</section>
    <details className="collapse"><summary>元数据</summary><CodeBlock language="json">{JSON.stringify(artifact.metadata || {}, null, 2)}</CodeBlock></details>
  </DetailPanel>;
}

function RelationsView({ files, search, onSearch, onSelect }: { files: ManagedFile[]; search: string; onSearch: (value: string) => void; onSelect: (file: ManagedFile) => void }) {
  return <>
    <FilterBar className="data-relation-filters"><Input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="搜索关系中的文件或任务" aria-label="搜索数据关系" /><span className="status-pill">{files.length} 个文件节点</span></FilterBar>
    <div className="data-relations-grid">
      {files.map((file) => <button type="button" className="card data-relation-card" key={file.file_id} onClick={() => onSelect(file)}>
        <span className="data-relation-file"><b>{file.original_name || file.file_id}</b><small>{typeLabel(file.logical_type)}</small></span>
        <span className="data-relation-flow">文件</span><span className="data-relation-arrow">→</span>
        <span className="data-relation-flow">{file.reference_count} 个引用</span><span className="data-relation-arrow">→</span>
        <span className="data-relation-flow">{file.artifacts.length} 个制品</span>
        <span className="data-relation-types">{file.reference_types.length ? file.reference_types.map(referenceTypeLabel).join("、") : "暂无业务引用"}</span>
      </button>)}
      {!files.length && <EmptyState text="暂无关系数据" />}
    </div>
  </>;
}

function LifecycleView({ retention, archive, archivedItems, busy, onApply, onRestore }: {
  retention: LifecyclePreview | null; archive: LifecyclePreview | null; archivedItems: ArchivedDataItem[]; busy: boolean;
  onApply: (kind: "retention" | "archive") => void; onRestore: (item: ArchivedDataItem) => void;
}) {
  const retentionCount = sumCounts(retention?.candidate_counts);
  const archiveCount = sumCounts(archive?.candidate_counts);
  return <div className="data-lifecycle-grid">
    <section className="card data-lifecycle-card">
      <div className="data-lifecycle-head"><div><h3>到期清理</h3><p>永久清理超过保留期限且没有活跃引用的数据。</p></div><Badge kind={retentionCount ? "warn" : "ok"}>{retentionCount} 项候选</Badge></div>
      <CandidateCounts counts={retention?.candidate_counts} />
      {retention?.blocked_items?.length ? <p className="text-sm dim">已自动保护 {retention.blocked_items.length} 项仍在使用的数据。</p> : null}
      <Button variant="danger" size="sm" disabled={!retentionCount || busy} onClick={() => onApply("retention")}>清理到期数据</Button>
    </section>
    <section className="card data-lifecycle-card">
      <div className="data-lifecycle-head"><div><h3>历史归档</h3><p>把历史运行、追踪和作业移入可恢复归档区。</p></div><Badge kind={archiveCount ? "info" : "ok"}>{archiveCount} 项候选</Badge></div>
      <CandidateCounts counts={archive?.candidate_counts} />
      {archive?.blocked_items?.length ? <p className="text-sm dim">已自动保护 {archive.blocked_items.length} 项仍在使用的数据。</p> : null}
      <Button variant="primary" size="sm" disabled={!archiveCount || busy} onClick={() => onApply("archive")}>归档历史数据</Button>
    </section>
    <section className="card data-lifecycle-card data-archive-list">
      <div className="data-lifecycle-head"><div><h3>归档区</h3><p>归档内容可恢复到原来的运行位置。</p></div><Badge kind="muted">{archivedItems.length} 项</Badge></div>
      {archivedItems.map((item) => <div className="data-archive-row" key={`${item.month}-${item.kind}-${item.name}`}>
        <span><b>{item.name}</b><small>{archiveKindLabel(item.kind)} · {item.month} · {formatFileSize(item.size_bytes)}</small></span>
        <Button size="sm" onClick={() => onRestore(item)}>恢复</Button>
      </div>)}
      {!archivedItems.length && <EmptyState text="归档区为空" />}
    </section>
  </div>;
}

function CandidateCounts({ counts }: { counts?: Record<string, number> }) {
  const entries = Object.entries(counts || {}).filter(([, count]) => count > 0);
  return <div className="data-candidate-counts">{entries.length ? entries.map(([key, count]) => <span key={key}><b>{count}</b>{candidateLabel(key)}</span>) : <p className="dim text-sm">当前没有需要处理的数据。</p>}</div>;
}

function Info({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="data-info-cell"><div className="stat-card-box-label">{label}</div><div className={`stat-card-box-value ${mono ? "mono" : ""}`}>{value}</div></div>;
}

function AuthorityBadge({ artifact }: { artifact: Artifact }) {
  const status = artifact.governance?.authority_status;
  if (status === "authoritative") return <Badge kind="ok">最新完整</Badge>;
  if (status === "provisional") return <Badge kind="warn">临时证据</Badge>;
  if (status === "incomplete") return <Badge kind="err">不完整</Badge>;
  if (status === "historical") return <Badge kind="muted">历史版本</Badge>;
  if (status === "contextual") return <Badge kind="info">上下文证据</Badge>;
  return <Badge kind="muted">业务制品</Badge>;
}

function authorityLabel(artifact: Artifact): string {
  const status = artifact.governance?.authority_status;
  return status === "authoritative" ? "最新完整证据" : status === "provisional" ? "临时证据" : status === "incomplete" ? "不完整证据" : status === "historical" ? "历史版本" : status === "contextual" ? "上下文证据" : "业务交付物";
}

function typeLabel(type: string): string { return TYPE_LABELS[type] || type || "未知类型"; }
function sourceLabel(source: string): string { return SOURCE_LABELS[source] || source || "系统"; }
function sumCounts(counts?: Record<string, number>): number { return Object.values(counts || {}).reduce((sum, value) => sum + Number(value || 0), 0); }
function archiveKindLabel(kind: string): string { return ({ runs: "运行记录", traces: "追踪记录", jobs: "作业", tmp: "临时文件" } as Record<string, string>)[kind] || kind; }
function candidateLabel(kind: string): string { return ({ runs: "运行", traces: "追踪", jobs: "作业", artifacts: "临时文件", sessions: "会话", memories: "记忆", temp: "临时文件" } as Record<string, string>)[kind] || kind; }
function referenceTypeLabel(type: string): string { return ({ run: "运行任务", session: "会话", artifact: "证据制品", knowledge_source: "知识来源", job: "作业" } as Record<string, string>)[type] || type || "业务对象"; }
function relationLabel(relation: string): string { return ({ source: "源文件", output: "任务产出", attachment: "附件", normalized: "规范化内容" } as Record<string, string>)[relation] || relation || "关联"; }
function sensitivityLabel(value: string): string { return ({ public: "公开", internal: "内部", sensitive: "敏感", secret: "机密" } as Record<string, string>)[value] || value; }
function lifecycleLabel(value: string): string { return ({ active: "使用中", archived: "已归档", soft_deleted: "待清理", purged: "已清理" } as Record<string, string>)[value] || value; }
function errorMessage(reason: unknown): string {
  if (isApiError(reason)) return reason.message;
  if (reason instanceof Error) return reason.message;
  return String(reason);
}
