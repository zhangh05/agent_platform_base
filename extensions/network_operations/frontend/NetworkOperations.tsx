import { useCallback, useEffect, useMemo, useState } from "react";
import { apiRequest } from "../../../frontend/src/api/client";
import { useSessionStore } from "../../../frontend/src/stores/session";
import "./NetworkOperations.css";

type Asset = { asset_id: string; name: string; host: string; port: number; username: string; vendor: string; region: string; auth_method?: string; credential_configured: boolean; host_key_trusted?: boolean };
type InspectionResult = { status: string; name?: string; host?: string; commands?: string[]; output_hash?: string; duration_ms?: number; error?: string };
type Finding = { finding_id: string; asset_id: string; asset_name: string; asset_host: string; category: string; title: string; description: string; severity: "low" | "medium" | "high" | "critical"; status: "open" | "acknowledged" | "resolved" | "suppressed"; last_seen_at: string; last_seen_task_id: string; occurrences: number; evidence?: { artifact_id?: string; output_hash?: string; baseline_id?: string } };
type Inspection = { task_id: string; status: string; script?: { name?: string; script_id?: string }; total: number; completed: number; succeeded: number; failed: number; created_at: string; finished_at?: string; artifact_id?: string; results?: Record<string, InspectionResult>; findings?: Finding[]; finding_count?: number };
type Script = { script_id: string; name: string; description: string; vendors: string[]; commands: string[]; checks?: Array<{ check_id: string; name: string; severity: string }>; version?: number };
type Baseline = { baseline_id: string; task_id: string; confirmed: boolean; current: boolean; created_at: string };
type Health = { registered_assets: number; assets_with_credentials: number; active_findings: number; findings_by_severity: Record<string, number>; latest_inspection_status: string; latest_inspection_at: string };
type Overview = { health: Health; current_baseline: Baseline | null; latest_inspection: Inspection | null };
type View = "overview" | "runs" | "findings" | "manage";

const base = "/extensions/network.operations";
const displayStatus: Record<string, string> = { queued: "等待执行", running: "执行中", succeeded: "已完成", partial: "部分完成", failed: "执行失败", cancelled: "已取消", not_started: "尚未巡检", open: "待处理", acknowledged: "跟进中", resolved: "已关闭", suppressed: "已忽略" };
const severityLabel: Record<string, string> = { critical: "严重", high: "高", medium: "中", low: "低" };

function formatTime(value?: string) {
  if (!value) return "暂无";
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString();
}

export default function NetworkOperations() {
  const workspaceId = useSessionStore((state) => state.currentWorkspaceId);
  const [view, setView] = useState<View>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [runs, setRuns] = useState<Inspection[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [scripts, setScripts] = useState<Script[]>([]);
  const [baselines, setBaselines] = useState<Baseline[]>([]);
  const [selectedAssetIds, setSelectedAssetIds] = useState<string[]>([]);
  const [scriptId, setScriptId] = useState("");
  const [activeRun, setActiveRun] = useState<Inspection | null>(null);
  const [findingFilter, setFindingFilter] = useState("active");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [assetForm, setAssetForm] = useState({ name: "", host: "", port: "22", username: "", password: "", vendor: "h3c", region: "" });
  const [scriptForm, setScriptForm] = useState({ name: "", description: "", vendors: "h3c", commands: "", ruleName: "", rulePattern: "", ruleSeverity: "medium" });

  const load = useCallback(async () => {
    const params = { workspace_id: workspaceId };
    const [overviewRes, assetsRes, runsRes, findingsRes, scriptsRes, baselinesRes] = await Promise.all([
      apiRequest<Overview>({ method: "GET", url: `${base}/overview`, params }),
      apiRequest<{ assets: Asset[] }>({ method: "GET", url: `${base}/assets`, params }),
      apiRequest<{ inspections: Inspection[] }>({ method: "GET", url: `${base}/inspections`, params }),
      apiRequest<{ findings: Finding[] }>({ method: "GET", url: `${base}/findings`, params }),
      apiRequest<{ scripts: Script[] }>({ method: "GET", url: `${base}/scripts`, params }),
      apiRequest<{ baselines: Baseline[] }>({ method: "GET", url: `${base}/baselines`, params }),
    ]);
    setOverview(overviewRes); setAssets(assetsRes.assets || []); setRuns(runsRes.inspections || []); setFindings(findingsRes.findings || []); setScripts(scriptsRes.scripts || []); setBaselines(baselinesRes.baselines || []);
    if (!scriptId && scriptsRes.scripts?.length) setScriptId(scriptsRes.scripts[0].script_id);
  }, [workspaceId, scriptId]);

  useEffect(() => { void load().catch(() => setError("网络运行保障数据暂时无法读取")); }, [load]);
  useEffect(() => {
    if (!runs.some((item) => item.status === "queued" || item.status === "running")) return;
    const timer = window.setInterval(() => void load(), 1800);
    return () => window.clearInterval(timer);
  }, [runs, load]);

  const activeFindings = useMemo(() => findings.filter((item) => findingFilter === "all" || (findingFilter === "active" ? ["open", "acknowledged"].includes(item.status) : item.status === findingFilter)), [findings, findingFilter]);
  const selectedScript = scripts.find((item) => item.script_id === scriptId);

  async function startInspection() {
    if (!scriptId) { setError("请先选择一份巡检策略。"); setView("manage"); return; }
    const assetIds = selectedAssetIds.length ? selectedAssetIds : assets.map((item) => item.asset_id);
    if (!assetIds.length) { setError("请先登记至少一台设备。"); setView("manage"); return; }
    setBusy(true); setError("");
    try { const result = await apiRequest<{ task: Inspection }>({ method: "POST", url: `${base}/inspections`, data: { workspace_id: workspaceId, asset_ids: assetIds, script_id: scriptId } }); setActiveRun(result.task); setView("runs"); await load(); }
    catch (cause) { setError(String((cause as { message?: string })?.message || "巡检任务创建失败")); }
    finally { setBusy(false); }
  }

  async function openRun(run: Inspection) {
    setBusy(true); setError("");
    try { const result = await apiRequest<{ task: Inspection }>({ method: "GET", url: `${base}/inspections/${run.task_id}`, params: { workspace_id: workspaceId } }); setActiveRun(result.task); }
    catch (cause) { setError(String((cause as { message?: string })?.message || "巡检详情读取失败")); }
    finally { setBusy(false); }
  }

  async function setFindingState(finding: Finding, action: "acknowledge" | "resolve" | "suppress" | "reopen") {
    const label = action === "acknowledge" ? "确认跟进" : action === "resolve" ? "关闭" : action === "suppress" ? "忽略" : "重新打开";
    if (!window.confirm(`确定${label}“${finding.title}”吗？`)) return;
    setBusy(true); setError("");
    try { await apiRequest({ method: "POST", url: `${base}/findings/${finding.finding_id}/state`, data: { workspace_id: workspaceId, action } }); await load(); }
    catch (cause) { setError(String((cause as { message?: string })?.message || "发现项状态更新失败")); }
    finally { setBusy(false); }
  }

  async function saveAsset(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { await apiRequest({ method: "POST", url: `${base}/assets`, data: { ...assetForm, workspace_id: workspaceId, port: Number(assetForm.port) } }); setAssetForm({ name: "", host: "", port: "22", username: "", password: "", vendor: "h3c", region: "" }); await load(); }
    catch (cause) { setError(String((cause as { message?: string })?.message || "设备保存失败")); }
    finally { setBusy(false); }
  }

  async function removeAsset(asset: Asset) {
    if (!window.confirm(`确定硬删除设备“${asset.name}”吗？关联的历史巡检记录不会被改写。`)) return;
    setBusy(true); setError("");
    try { await apiRequest({ method: "DELETE", url: `${base}/assets/${asset.asset_id}`, params: { workspace_id: workspaceId } }); setSelectedAssetIds((value) => value.filter((id) => id !== asset.asset_id)); await load(); }
    catch (cause) { setError(String((cause as { message?: string })?.message || "设备删除失败")); }
    finally { setBusy(false); }
  }

  async function probeAsset(asset: Asset) {
    setBusy(true); setError("");
    try {
      const result = await apiRequest<{ ok: boolean; requires_host_key_acceptance?: boolean; fingerprint?: string; error?: string }>({ method: "POST", url: `${base}/assets/${asset.asset_id}/probe`, data: { workspace_id: workspaceId } });
      if (result.requires_host_key_acceptance) setError(`设备返回新主机指纹 ${result.fingerprint || ""}，请在受控流程中核验后信任。`);
      else if (!result.ok) setError(result.error || "设备连接失败"); else setError(`${asset.name} 连接验证成功。`);
    } catch (cause) { setError(String((cause as { message?: string })?.message || "连接验证失败")); }
    finally { setBusy(false); }
  }

  async function saveScript(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { const result = await apiRequest<{ script: Script }>({ method: "POST", url: `${base}/scripts`, data: { workspace_id: workspaceId, name: scriptForm.name, description: scriptForm.description, vendors: scriptForm.vendors.split(",").map((item) => item.trim()).filter(Boolean), commands: scriptForm.commands.split("\n").map((item) => item.trim()).filter(Boolean), checks: scriptForm.rulePattern ? [{ check_id: "custom-rule", name: scriptForm.ruleName || "自定义输出匹配", description: "命令输出匹配了管理员配置的规则。", severity: scriptForm.ruleSeverity, kind: "output_matches", pattern: scriptForm.rulePattern }] : [] } }); setScriptId(result.script.script_id); setScriptForm({ name: "", description: "", vendors: "h3c", commands: "", ruleName: "", rulePattern: "", ruleSeverity: "medium" }); await load(); }
    catch (cause) { setError(String((cause as { message?: string })?.message || "巡检策略保存失败")); }
    finally { setBusy(false); }
  }

  return <div className="page network-ops-page">
    <header className="page-header ui-page-header network-hero"><div><span className="network-eyebrow">网络运行保障</span><h1>网络健康巡检 <span>从证据到人工闭环</span></h1><p className="subtitle">只读采集、规则化发现与人工处置；不会自动下发设备配置。</p></div><div className="network-hero-actions"><button className="btn secondary" onClick={() => void load()} disabled={busy}>刷新数据</button><button className="btn primary" onClick={() => void startInspection()} disabled={busy || assets.length === 0}>发起健康巡检</button></div></header>
    <div className="page-body network-ops-body">
      {error ? <div className="network-ops-error" role="alert">{error}</div> : null}
      <nav className="network-ops-tabs" aria-label="网络运行保障视图"><button className={view === "overview" ? "active" : ""} onClick={() => setView("overview")}>健康概览</button><button className={view === "runs" ? "active" : ""} onClick={() => setView("runs")}>巡检执行</button><button className={view === "findings" ? "active" : ""} onClick={() => setView("findings")}>异常闭环 <span>{overview?.health.active_findings || 0}</span></button><button className={view === "manage" ? "active" : ""} onClick={() => setView("manage")}>设备与策略</button></nav>
      {view === "overview" ? <OverviewPanel overview={overview} activeFindings={activeFindings} scripts={scripts} assets={assets} selectedScript={selectedScript} scriptId={scriptId} selectedAssetIds={selectedAssetIds} busy={busy} onScript={setScriptId} onOpenFindings={() => setView("findings")} onOpenManage={() => setView("manage")} onRun={startInspection} onFinding={setFindingState} /> : null}
      {view === "findings" ? <section className="network-panel full"><div className="network-panel-head"><div><span>异常闭环</span><h2>证据化发现项</h2><p>发现项来自真实巡检证据；关闭或忽略均保留人工处置记录。</p></div><div className="network-filter">{[["active", "待处理"], ["all", "全部"], ["acknowledged", "跟进中"], ["resolved", "已关闭"], ["suppressed", "已忽略"]].map(([value, label]) => <button key={value} className={findingFilter === value ? "active" : ""} onClick={() => setFindingFilter(value)}>{label}</button>)}</div></div><div className="network-finding-list">{activeFindings.map((finding) => <FindingRow key={finding.finding_id} finding={finding} busy={busy} onAction={setFindingState} expanded />)}{!activeFindings.length ? <Empty title="没有符合条件的发现项" text="这里不会把单次工具失败误写成业务结论；只有巡检证据和规则产生的发现才会出现。" /> : null}</div></section> : null}
      {view === "runs" ? <RunsPanel runs={runs} activeRun={activeRun} findings={findings} busy={busy} onOpen={openRun} onRun={startInspection} /> : null}
      {view === "manage" ? <ManagementPanel assets={assets} scripts={scripts} baselines={baselines} currentBaseline={overview?.current_baseline || null} selectedAssetIds={selectedAssetIds} scriptId={scriptId} assetForm={assetForm} scriptForm={scriptForm} busy={busy} onAssetForm={setAssetForm} onScriptForm={setScriptForm} onSaveAsset={saveAsset} onSaveScript={saveScript} onSelectAssets={setSelectedAssetIds} onScript={setScriptId} onProbe={probeAsset} onRemove={removeAsset} /> : null}
    </div>
  </div>;
}

function OverviewPanel({ overview, activeFindings, scripts, assets, selectedScript, scriptId, selectedAssetIds, busy, onScript, onOpenFindings, onOpenManage, onRun, onFinding }: { overview: Overview | null; activeFindings: Finding[]; scripts: Script[]; assets: Asset[]; selectedScript?: Script; scriptId: string; selectedAssetIds: string[]; busy: boolean; onScript: (value: string) => void; onOpenFindings: () => void; onOpenManage: () => void; onRun: () => Promise<void>; onFinding: (finding: Finding, action: "acknowledge" | "resolve" | "suppress" | "reopen") => Promise<void> }) {
  return <><section className="network-stat-grid"><article><span>已登记设备</span><strong>{overview?.health.registered_assets ?? "—"}</strong><small>{overview?.health.assets_with_credentials ?? 0} 台已配置凭据</small></article><article><span>待处置异常</span><strong className={(overview?.health.active_findings || 0) > 0 ? "attention" : "ok"}>{overview?.health.active_findings ?? "—"}</strong><small>严重 {overview?.health.findings_by_severity?.critical || 0} · 高 {overview?.health.findings_by_severity?.high || 0}</small></article><article><span>最近巡检</span><strong className={`status-${overview?.health.latest_inspection_status || "not_started"}`}>{displayStatus[overview?.health.latest_inspection_status || "not_started"]}</strong><small>{formatTime(overview?.health.latest_inspection_at)}</small></article><article><span>当前状态基线</span><strong className={overview?.current_baseline ? "ok" : "muted"}>{overview?.current_baseline ? "已确认" : "未建立"}</strong><small>{overview?.current_baseline ? `来源 ${overview.current_baseline.task_id}` : "完成可信巡检后人工确认"}</small></article></section><section className="network-business-grid"><article className="network-panel"><div className="network-panel-head"><div><span>优先处理</span><h2>待处理异常</h2></div><button className="network-link" onClick={onOpenFindings}>查看全部</button></div>{activeFindings.slice(0, 5).map((finding) => <FindingRow key={finding.finding_id} finding={finding} busy={busy} onAction={onFinding} />)}{!activeFindings.length ? <Empty title="暂无待处理异常" text="完成巡检后，连接失败、规则命中和基线变化会在这里形成可追溯发现项。" /> : null}</article><aside className="network-panel network-run-panel"><div className="network-panel-head"><div><span>下一步</span><h2>发起健康巡检</h2></div></div><label>巡检策略<select value={scriptId} onChange={(event) => onScript(event.target.value)}><option value="">请选择策略</option>{scripts.map((script) => <option key={script.script_id} value={script.script_id}>{script.name}</option>)}</select></label><p>{selectedScript ? `${selectedScript.vendors.join("、")} · ${selectedScript.commands.length} 条只读命令 · ${selectedScript.checks?.length || 0} 条发现规则` : "策略定义采集命令及可审计的发现规则。"}</p><div className="network-scope"><b>巡检范围</b><span>{selectedAssetIds.length ? `已选 ${selectedAssetIds.length} 台设备` : assets.length ? `全部 ${assets.length} 台设备` : "尚未登记设备"}</span></div><button className="btn primary" onClick={() => void onRun()} disabled={busy || !assets.length || !scriptId}>开始只读巡检</button><button className="network-link align-left" onClick={onOpenManage}>调整设备或策略</button></aside></section></>;
}

function RunsPanel({ runs, activeRun, findings, busy, onOpen, onRun }: { runs: Inspection[]; activeRun: Inspection | null; findings: Finding[]; busy: boolean; onOpen: (run: Inspection) => Promise<void>; onRun: () => Promise<void> }) { return <section className="network-panel full"><div className="network-panel-head"><div><span>巡检执行</span><h2>巡检批次与证据</h2><p>任务是采集过程；发现项和人工处置才是业务闭环。</p></div><button className="btn primary" onClick={() => void onRun()} disabled={busy}>新建巡检</button></div><div className="network-run-list">{runs.map((run) => <button type="button" className={`network-run-row ${activeRun?.task_id === run.task_id ? "selected" : ""}`} key={run.task_id} onClick={() => void onOpen(run)}><div><strong>{run.script?.name || "健康巡检"}</strong><span>{formatTime(run.created_at)} · {run.total} 台设备</span></div><div className="network-progress"><i style={{ width: `${run.total ? Math.round(run.completed / run.total * 100) : 0}%` }} /></div><span>{run.succeeded} 成功 / {run.failed} 失败</span><b className={`network-state ${run.status}`}>{displayStatus[run.status] || run.status}</b></button>)}{!runs.length ? <Empty title="尚无巡检批次" text="从健康概览选择策略并开始一次只读巡检。" /> : null}</div>{activeRun ? <RunDetail run={activeRun} findings={findings.filter((item) => item.last_seen_task_id === activeRun.task_id)} /> : null}</section>; }

function ManagementPanel({ assets, scripts, baselines, currentBaseline, selectedAssetIds, scriptId, assetForm, scriptForm, busy, onAssetForm, onScriptForm, onSaveAsset, onSaveScript, onSelectAssets, onScript, onProbe, onRemove }: { assets: Asset[]; scripts: Script[]; baselines: Baseline[]; currentBaseline: Baseline | null; selectedAssetIds: string[]; scriptId: string; assetForm: { name: string; host: string; port: string; username: string; password: string; vendor: string; region: string }; scriptForm: { name: string; description: string; vendors: string; commands: string; ruleName: string; rulePattern: string; ruleSeverity: string }; busy: boolean; onAssetForm: (value: typeof assetForm) => void; onScriptForm: (value: typeof scriptForm) => void; onSaveAsset: (event: React.FormEvent) => Promise<void>; onSaveScript: (event: React.FormEvent) => Promise<void>; onSelectAssets: (ids: string[]) => void; onScript: (value: string) => void; onProbe: (asset: Asset) => Promise<void>; onRemove: (asset: Asset) => Promise<void> }) { return <div className="network-management-grid"><section className="network-panel"><div className="network-panel-head"><div><span>资产范围</span><h2>设备资产</h2><p>凭据只保存为加密引用；删除为硬删除，不改写历史巡检证据。</p></div></div><form className="network-form" onSubmit={(event) => void onSaveAsset(event)}><div className="network-form-grid"><label>设备名称<input value={assetForm.name} onChange={(event) => onAssetForm({ ...assetForm, name: event.target.value })} required /></label><label>管理地址<input value={assetForm.host} onChange={(event) => onAssetForm({ ...assetForm, host: event.target.value })} required /></label><label>登录账户<input value={assetForm.username} onChange={(event) => onAssetForm({ ...assetForm, username: event.target.value })} required /></label><label>密码<input type="password" autoComplete="new-password" value={assetForm.password} onChange={(event) => onAssetForm({ ...assetForm, password: event.target.value })} required /></label><label>厂商<select value={assetForm.vendor} onChange={(event) => onAssetForm({ ...assetForm, vendor: event.target.value })}><option value="h3c">H3C</option><option value="huawei">华为</option><option value="cisco">Cisco</option><option value="generic">通用主机</option></select></label><label>区域<input value={assetForm.region} onChange={(event) => onAssetForm({ ...assetForm, region: event.target.value })} /></label></div><button className="btn secondary" disabled={busy}>登记设备</button></form><div className="network-asset-list">{assets.map((asset) => <article key={asset.asset_id}><label className="network-select"><input type="checkbox" checked={selectedAssetIds.includes(asset.asset_id)} onChange={(event) => onSelectAssets(event.target.checked ? [...selectedAssetIds, asset.asset_id] : selectedAssetIds.filter((id) => id !== asset.asset_id))} /><span /></label><div><strong>{asset.name}</strong><small>{asset.host}:{asset.port} · {asset.vendor} · {asset.region || "未分区"}</small></div><div className="network-asset-state"><span className={asset.credential_configured ? "ok" : "attention"}>{asset.credential_configured ? "凭据已配置" : "缺少凭据"}</span><span className={asset.host_key_trusted ? "ok" : "muted"}>{asset.host_key_trusted ? "主机指纹已确认" : "待确认指纹"}</span></div><div><button className="network-link" onClick={() => void onProbe(asset)} disabled={busy}>测试连接</button><button className="network-delete" onClick={() => void onRemove(asset)} disabled={busy}>删除</button></div></article>)}{!assets.length ? <Empty title="尚未登记设备" text="先登记一台设备，再选择适用的巡检策略。" /> : null}</div></section><section className="network-panel"><div className="network-panel-head"><div><span>巡检策略</span><h2>检查包与基线</h2><p>策略控制只读命令和确定性规则；规则命中产生发现项，不交给模型猜测。</p></div></div><div className="network-strategy-list">{scripts.map((script) => <article key={script.script_id} className={script.script_id === scriptId ? "selected" : ""}><div><strong>{script.name}</strong><small>{script.vendors.join("、")} · {script.commands.length} 条命令 · {script.checks?.length || 0} 条发现规则</small><p>{script.description || "未填写说明"}</p></div><button className="network-link" onClick={() => onScript(script.script_id)}>用于巡检</button></article>)}</div><form className="network-form compact" onSubmit={(event) => void onSaveScript(event)}><h3>新增自定义策略</h3><label>策略名称<input value={scriptForm.name} onChange={(event) => onScriptForm({ ...scriptForm, name: event.target.value })} required /></label><label>适用厂商<select value={scriptForm.vendors} onChange={(event) => onScriptForm({ ...scriptForm, vendors: event.target.value })}><option value="h3c">H3C</option><option value="huawei">华为</option><option value="cisco">Cisco</option><option value="generic">通用主机</option></select></label><label>说明<input value={scriptForm.description} onChange={(event) => onScriptForm({ ...scriptForm, description: event.target.value })} /></label><label>只读命令（每行一条）<textarea rows={5} value={scriptForm.commands} onChange={(event) => onScriptForm({ ...scriptForm, commands: event.target.value })} required /></label><label>规则名称（可选）<input value={scriptForm.ruleName} onChange={(event) => onScriptForm({ ...scriptForm, ruleName: event.target.value })} placeholder="例如：日志告警关键字" /></label><label>输出匹配规则（可选，正则表达式）<input value={scriptForm.rulePattern} onChange={(event) => onScriptForm({ ...scriptForm, rulePattern: event.target.value })} placeholder="例如：ERROR|FATAL" /></label><label>命中严重度<select value={scriptForm.ruleSeverity} onChange={(event) => onScriptForm({ ...scriptForm, ruleSeverity: event.target.value })}><option value="low">低</option><option value="medium">中</option><option value="high">高</option><option value="critical">严重</option></select></label><button className="btn secondary" disabled={busy}>保存策略</button></form><div className="network-baseline-summary"><b>当前状态基线</b><span>{currentBaseline ? `已确认 · ${currentBaseline.task_id}` : "未建立；完成可信巡检后需人工确认。"}</span><small>历史基线：{baselines.length} 份</small></div></section></div>; }

function FindingRow({ finding, busy, onAction, expanded = false }: { finding: Finding; busy: boolean; expanded?: boolean; onAction: (finding: Finding, action: "acknowledge" | "resolve" | "suppress" | "reopen") => Promise<void> }) { return <article className={`network-finding severity-${finding.severity} ${expanded ? "expanded" : ""}`}><div className="network-finding-mark">{severityLabel[finding.severity]}</div><div className="network-finding-content"><div><b>{finding.title}</b><span>{finding.asset_name} · {finding.asset_host}</span></div><p>{finding.description}</p><small>最近证据：{formatTime(finding.last_seen_at)} · 已出现 {finding.occurrences} 次 · 巡检 {finding.last_seen_task_id}</small></div><div className="network-finding-actions"><span className={`network-state ${finding.status}`}>{displayStatus[finding.status]}</span>{finding.status === "open" ? <button className="network-link" onClick={() => void onAction(finding, "acknowledge")} disabled={busy}>确认跟进</button> : null}{["open", "acknowledged"].includes(finding.status) ? <button className="network-link" onClick={() => void onAction(finding, "resolve")} disabled={busy}>关闭</button> : null}{["open", "acknowledged"].includes(finding.status) ? <button className="network-link muted" onClick={() => void onAction(finding, "suppress")} disabled={busy}>忽略</button> : null}{["resolved", "suppressed"].includes(finding.status) ? <button className="network-link" onClick={() => void onAction(finding, "reopen")} disabled={busy}>重新打开</button> : null}</div></article>; }

function RunDetail({ run, findings }: { run: Inspection; findings: Finding[] }) { return <section className="network-run-detail"><div><span>执行详情</span><h3>{run.script?.name || "健康巡检"}</h3><p>{formatTime(run.finished_at || run.created_at)} · 证据工件 {run.artifact_id || "生成中"}</p></div><div className="network-result-grid">{Object.entries(run.results || {}).map(([assetId, result]) => <article key={assetId}><b>{result.name || assetId}</b><span>{result.host || "未记录地址"}</span><small>{result.status === "succeeded" ? `${result.commands?.length || 0} 条命令 · ${result.duration_ms || 0} ms` : result.error || "执行失败"}</small></article>)}</div><div className="network-run-findings"><b>本次发现项</b>{findings.length ? findings.map((item) => <span key={item.finding_id} className={`severity-${item.severity}`}>{severityLabel[item.severity]} · {item.title} · {item.asset_name}</span>) : <span className="ok">未产生规则发现项；不代表设备绝对无风险。</span>}</div></section>; }
function Empty({ title, text }: { title: string; text: string }) { return <div className="network-empty"><b>{title}</b><span>{text}</span></div>; }
