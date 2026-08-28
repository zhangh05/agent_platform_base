import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { apiRequest } from "../../../frontend/src/api/client";
import { confirm } from "../../../frontend/src/components/ConfirmDialog";
import { IconAlert, IconCheck, IconClock, IconHistory, IconPlus, IconProbe, IconRefresh, IconServer, IconShield, IconTrash } from "../../../frontend/src/components/Icon";
import { Button, ModalShell, PageHeader } from "../../../frontend/src/components/ui";
import { useSessionStore } from "../../../frontend/src/stores/session";
import "./NetworkOperations.css";

type Asset = { asset_id: string; name: string; host: string; port: number; vendor: string; region: string; credential_configured: boolean; host_key_trusted?: boolean };
type FindingStatus = "open" | "acknowledged" | "resolved" | "suppressed";
type Finding = { finding_id: string; asset_name: string; asset_host: string; title: string; description: string; severity: "low" | "medium" | "high" | "critical"; status: FindingStatus; last_seen_at: string; last_seen_task_id: string; occurrences: number };
type Result = { status: string; name?: string; host?: string; commands?: string[]; error?: string };
type Inspection = { task_id: string; status: string; script?: { name?: string }; total: number; succeeded: number; failed: number; created_at: string; finished_at?: string; artifact_id?: string; results?: Record<string, Result> };
type Script = { script_id: string; name: string; description: string; vendors: string[]; commands: string[]; checks?: Array<{ check_id: string; name: string; severity: string }> };
type Baseline = { baseline_id: string; task_id: string; confirmed: boolean; current: boolean; created_at: string };
type Overview = { health: { registered_assets: number; assets_with_credentials: number; active_findings: number; latest_inspection_status: string; latest_inspection_at: string }; current_baseline: Baseline | null; latest_inspection: Inspection | null };
type View = "home" | "runs" | "findings" | "settings";
type Notice = { kind: "error" | "success"; text: string } | null;

const base = "/extensions/network.operations";
const words: Record<string, string> = { queued: "排队中", running: "巡检中", succeeded: "已完成", partial: "已完成（有异常）", failed: "未完成", cancelled: "已取消", not_started: "未开始", open: "待处理", acknowledged: "处理中", resolved: "已关闭", suppressed: "已忽略", critical: "严重", high: "高风险", medium: "需关注", low: "提示" };
const assetBlank = { name: "", host: "", port: "22", username: "", password: "", vendor: "h3c", region: "" };
const scriptBlank = { name: "", description: "", vendors: "h3c", commands: "", ruleName: "", rulePattern: "", ruleSeverity: "medium" };
const cls = (...items: Array<string | false | null | undefined>) => items.filter(Boolean).join(" ");
const stamp = (value?: string) => {
  if (!value) return "暂无记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
};
const vendor = (value: string) => ({ h3c: "H3C", huawei: "华为", cisco: "Cisco", generic: "通用设备" } as Record<string, string>)[value] || value;

export default function NetworkOperations() {
  const workspaceId = useSessionStore((state) => state.currentWorkspaceId);
  const [view, setView] = useState<View>("home");
  const [assetOpen, setAssetOpen] = useState(false);
  const [scriptOpen, setScriptOpen] = useState(false);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [scripts, setScripts] = useState<Script[]>([]);
  const [runs, setRuns] = useState<Inspection[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [baselines, setBaselines] = useState<Baseline[]>([]);
  const [activeRun, setActiveRun] = useState<Inspection | null>(null);
  const [scriptId, setScriptId] = useState("");
  const [selectedAssets, setSelectedAssets] = useState<string[]>([]);
  const [filter, setFilter] = useState<"active" | FindingStatus | "all">("active");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [assetForm, setAssetForm] = useState(assetBlank);
  const [scriptForm, setScriptForm] = useState(scriptBlank);
  const load = useCallback(async () => {
    const params = { workspace_id: workspaceId };
    const results = await Promise.all([
      apiRequest<Overview>({ method: "GET", url: base + "/overview", params }),
      apiRequest<{ assets: Asset[] }>({ method: "GET", url: base + "/assets", params }),
      apiRequest<{ inspections: Inspection[] }>({ method: "GET", url: base + "/inspections", params }),
      apiRequest<{ findings: Finding[] }>({ method: "GET", url: base + "/findings", params }),
      apiRequest<{ scripts: Script[] }>({ method: "GET", url: base + "/scripts", params }),
      apiRequest<{ baselines: Baseline[] }>({ method: "GET", url: base + "/baselines", params }),
    ]);
    setOverview(results[0]); setAssets(results[1].assets || []); setRuns(results[2].inspections || []); setFindings(results[3].findings || []); setScripts(results[4].scripts || []); setBaselines(results[5].baselines || []);
    setScriptId((current) => current || results[4].scripts?.[0]?.script_id || "");
  }, [workspaceId]);
  useEffect(() => { void load().catch(() => setNotice({ kind: "error", text: "暂时无法读取网络运行数据，请稍后重试。" })); }, [load]);
  useEffect(() => {
    if (!runs.some((run) => run.status === "queued" || run.status === "running")) return;
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
  }, [runs, load]);
  const active = useMemo(() => findings.filter((item) => item.status === "open" || item.status === "acknowledged"), [findings]);
  const visible = useMemo(() => findings.filter((item) => filter === "all" || (filter === "active" ? item.status === "open" || item.status === "acknowledged" : item.status === filter)), [findings, filter]);
  const selectedScript = scripts.find((item) => item.script_id === scriptId);
  const scope = selectedAssets.length ? "已选择 " + selectedAssets.length + " 台设备" : assets.length ? "全部 " + assets.length + " 台设备" : "尚未登记设备";
  const refresh = async () => { setBusy(true); setNotice(null); try { await load(); } catch { setNotice({ kind: "error", text: "刷新失败，请检查服务连接。" }); } finally { setBusy(false); } };
  const start = async () => {
    if (!scriptId) { setView("settings"); setNotice({ kind: "error", text: "先选择或新建一份巡检策略。" }); return; }
    const assetIds = selectedAssets.length ? selectedAssets : assets.map((asset) => asset.asset_id);
    if (!assetIds.length) { setView("settings"); setNotice({ kind: "error", text: "先登记至少一台设备。" }); return; }
    setBusy(true); setNotice(null);
    try {
      const response = await apiRequest<{ task: Inspection }>({ method: "POST", url: base + "/inspections", data: { workspace_id: workspaceId, asset_ids: assetIds, script_id: scriptId } });
      setActiveRun(response.task); setView("runs"); setNotice({ kind: "success", text: "健康巡检已创建，将在执行过程中自动更新。" }); await load();
    } catch (cause) { setNotice({ kind: "error", text: String((cause as { message?: string }).message || "巡检任务创建失败") }); } finally { setBusy(false); }
  };
  const openRun = async (run: Inspection) => {
    setBusy(true); setNotice(null);
    try { const response = await apiRequest<{ task: Inspection }>({ method: "GET", url: base + "/inspections/" + run.task_id, params: { workspace_id: workspaceId } }); setActiveRun(response.task); }
    catch (cause) { setNotice({ kind: "error", text: String((cause as { message?: string }).message || "无法打开巡检详情") }); } finally { setBusy(false); }
  };
  const updateFinding = async (item: Finding, action: "acknowledge" | "resolve" | "suppress" | "reopen") => {
    const actionName = { acknowledge: "确认跟进", resolve: "关闭", suppress: "忽略", reopen: "重新打开" }[action];
    if (!await confirm({ title: actionName + "发现项", body: "“" + item.title + "”会保留完整证据与处置记录。", confirmLabel: actionName, destructive: action === "suppress" })) return;
    setBusy(true); setNotice(null);
    try { await apiRequest({ method: "POST", url: base + "/findings/" + item.finding_id + "/state", data: { workspace_id: workspaceId, action } }); await load(); }
    catch (cause) { setNotice({ kind: "error", text: String((cause as { message?: string }).message || "发现项更新失败") }); } finally { setBusy(false); }
  };
  const saveAsset = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setNotice(null);
    try { await apiRequest({ method: "POST", url: base + "/assets", data: { ...assetForm, workspace_id: workspaceId, port: Number(assetForm.port) } }); setAssetForm(assetBlank); setAssetOpen(false); setNotice({ kind: "success", text: "设备已登记。下一步可测试连接或加入巡检范围。" }); await load(); }
    catch (cause) { setNotice({ kind: "error", text: String((cause as { message?: string }).message || "设备保存失败") }); } finally { setBusy(false); }
  };
  const probe = async (asset: Asset) => {
    setBusy(true); setNotice(null);
    try {
      const response = await apiRequest<{ ok: boolean; requires_host_key_acceptance?: boolean; fingerprint?: string; error?: string }>({ method: "POST", url: base + "/assets/" + asset.asset_id + "/probe", data: { workspace_id: workspaceId } });
      setNotice(response.ok ? { kind: "success", text: asset.name + " 的连接验证成功。" } : { kind: "error", text: response.requires_host_key_acceptance ? "发现新的主机指纹 " + (response.fingerprint || "") + "，需核验后再信任。" : response.error || "设备连接失败。" });
    } catch (cause) { setNotice({ kind: "error", text: String((cause as { message?: string }).message || "连接验证失败") }); } finally { setBusy(false); }
  };
  const remove = async (asset: Asset) => {
    if (!await confirm({ title: "硬删除设备", body: "确定从资产清单中硬删除“" + asset.name + "”吗？历史巡检证据不会被改写。", confirmLabel: "硬删除", destructive: true })) return;
    setBusy(true); setNotice(null);
    try { await apiRequest({ method: "DELETE", url: base + "/assets/" + asset.asset_id, params: { workspace_id: workspaceId } }); setSelectedAssets((items) => items.filter((id) => id !== asset.asset_id)); setNotice({ kind: "success", text: "设备已硬删除，关联历史证据仍可追溯。" }); await load(); }
    catch (cause) { setNotice({ kind: "error", text: String((cause as { message?: string }).message || "设备删除失败") }); } finally { setBusy(false); }
  };
  const saveScript = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setNotice(null);
    try {
      const checks = scriptForm.rulePattern ? [{ check_id: "custom-rule", name: scriptForm.ruleName || "自定义输出规则", description: "命令输出命中管理员定义的规则。", severity: scriptForm.ruleSeverity, kind: "output_matches", pattern: scriptForm.rulePattern }] : [];
      const response = await apiRequest<{ script: Script }>({ method: "POST", url: base + "/scripts", data: { workspace_id: workspaceId, name: scriptForm.name, description: scriptForm.description, vendors: scriptForm.vendors.split(",").map((value) => value.trim()).filter(Boolean), commands: scriptForm.commands.split("\\n").map((value) => value.trim()).filter(Boolean), checks } });
      setScriptId(response.script.script_id); setScriptForm(scriptBlank); setScriptOpen(false); setNotice({ kind: "success", text: "巡检策略已保存，可立即用于健康巡检。" }); await load();
    } catch (cause) { setNotice({ kind: "error", text: String((cause as { message?: string }).message || "策略保存失败") }); } finally { setBusy(false); }
  };
  return <div className="page network-ops-page">
    <PageHeader title="网络运行保障" subtitle="只读巡检、证据留存和人工闭环。不会自动修改网络设备。" className="network-header"><Button size="sm" icon={<IconRefresh size={16} />} onClick={() => void refresh()} disabled={busy}>刷新</Button><Button variant="primary" size="sm" icon={<IconProbe size={16} />} onClick={() => void start()} disabled={busy}>开始巡检</Button></PageHeader>
    <main className="network-layout">
      {notice && <div className={cls("network-notice", notice.kind)} role="status">{notice.kind === "error" ? <IconAlert size={17} /> : <IconCheck size={17} />}<span>{notice.text}</span><button aria-label="关闭提示" onClick={() => setNotice(null)}>×</button></div>}
      <nav className="network-local-nav" aria-label="网络运行保障导航"><NavButton current={view} id="home" icon={<IconShield size={17} />} onClick={setView}>运行概览</NavButton><NavButton current={view} id="runs" icon={<IconHistory size={17} />} onClick={setView}>巡检记录</NavButton><NavButton current={view} id="findings" icon={<IconAlert size={17} />} badge={active.length} onClick={setView}>发现项</NavButton><NavButton current={view} id="settings" icon={<IconServer size={17} />} onClick={setView}>设备与策略</NavButton></nav>
      {view === "home" && <Home overview={overview} active={active} assets={assets} scripts={scripts} script={selectedScript} scriptId={scriptId} scope={scope} busy={busy} onScript={setScriptId} onStart={() => void start()} onFindings={() => setView("findings")} onSettings={() => setView("settings")} onAction={updateFinding} />}
      {view === "runs" && <Runs runs={runs} activeRun={activeRun} findings={findings} busy={busy} onOpen={(run) => void openRun(run)} onStart={() => void start()} />}
      {view === "findings" && <Findings activeCount={active.length} filter={filter} items={visible} busy={busy} onFilter={setFilter} onAction={updateFinding} />}
      {view === "settings" && <Settings assets={assets} scripts={scripts} baseline={overview?.current_baseline || null} baselines={baselines} selectedAssets={selectedAssets} scriptId={scriptId} busy={busy} onAsset={() => setAssetOpen(true)} onScript={() => setScriptOpen(true)} onSelect={(id, checked) => setSelectedAssets((items) => checked ? [...items, id] : items.filter((item) => item !== id))} onChooseScript={setScriptId} onProbe={(asset) => void probe(asset)} onRemove={(asset) => void remove(asset)} />}
    </main>
    <AssetSheet open={assetOpen} busy={busy} form={assetForm} onChange={setAssetForm} onClose={() => setAssetOpen(false)} onSubmit={(event) => void saveAsset(event)} />
    <ScriptSheet open={scriptOpen} busy={busy} form={scriptForm} onChange={setScriptForm} onClose={() => setScriptOpen(false)} onSubmit={(event) => void saveScript(event)} />
  </div>;
}

function NavButton({ current, id, icon, badge, onClick, children }: { current: View; id: View; icon: ReactNode; badge?: number; onClick: (id: View) => void; children: ReactNode }) { return <button className={current === id ? "active" : ""} onClick={() => onClick(id)}>{icon}<span>{children}</span>{id === "findings" && badge ? <b>{badge}</b> : null}</button>; }
function Home({ overview, active, assets, scripts, script, scriptId, scope, busy, onScript, onStart, onFindings, onSettings, onAction }: { overview: Overview | null; active: Finding[]; assets: Asset[]; scripts: Script[]; script?: Script; scriptId: string; scope: string; busy: boolean; onScript: (id: string) => void; onStart: () => void; onFindings: () => void; onSettings: () => void; onAction: (item: Finding, action: "acknowledge" | "resolve" | "suppress" | "reopen") => Promise<void> }) {
  const latest = overview?.latest_inspection;
  return <div className="network-home"><section className="network-focus-card"><div className="network-focus-heading"><div><span className="network-kicker">当前工作</span><h2>{active.length ? "有 " + active.length + " 个发现项需要处理" : "当前没有待处理发现项"}</h2><p>{active.length ? "发现项均来自巡检证据。先确认影响，再完成处置。" : "可发起一次只读巡检，持续建立设备状态证据。"}</p></div>{active.length ? <Button size="sm" onClick={onFindings}>查看全部</Button> : null}</div><div className="network-focus-list">{active.slice(0, 3).map((item) => <FindingRow key={item.finding_id} item={item} compact busy={busy} onAction={onAction} />)}{!active.length && <div className="network-quiet-state"><IconShield size={28} /><div><b>运行状态平稳</b><span>未发现需要人工跟进的异常。巡检失败不会被误判为业务风险。</span></div></div>}</div></section>
    <aside className="network-side-column"><section className="network-action-card"><div className="network-card-title"><IconProbe size={18} /><div><span className="network-kicker">下一步</span><h3>发起健康巡检</h3></div></div><label>巡检策略<select value={scriptId} onChange={(event) => onScript(event.target.value)}><option value="">选择巡检策略</option>{scripts.map((item) => <option key={item.script_id} value={item.script_id}>{item.name}</option>)}</select></label><div className="network-action-summary"><span>{scope}</span><small>{script ? script.commands.length + " 条只读命令 · " + (script.checks?.length || 0) + " 条发现规则" : "策略定义采集命令和发现规则。"}</small></div><Button variant="primary" onClick={onStart} disabled={busy || !assets.length || !scriptId}>开始只读巡检</Button><button className="network-text-button" onClick={onSettings}>调整范围和策略 <span>→</span></button></section>
      <section className="network-posture-card"><div className="network-posture-header"><span className="network-kicker">运行状态</span><span className={cls("network-status-dot", overview?.health.latest_inspection_status || "not_started")} /></div><strong>{words[overview?.health.latest_inspection_status || "not_started"]}</strong><p>{stamp(overview?.health.latest_inspection_at)}</p><div className="network-posture-lines"><span><b>{overview?.health.registered_assets ?? 0}</b> 已登记设备</span><span><b>{overview?.health.assets_with_credentials ?? 0}</b> 已配置凭据</span><span><b>{overview?.current_baseline ? "已确认" : "未建立"}</b> 状态基线</span></div>{latest && <small>最近批次：{latest.script?.name || "健康巡检"} · {latest.succeeded}/{latest.total} 成功</small>}</section></aside></div>;
}
function Findings({ activeCount, filter, items, busy, onFilter, onAction }: { activeCount: number; filter: "active" | FindingStatus | "all"; items: Finding[]; busy: boolean; onFilter: (filter: "active" | FindingStatus | "all") => void; onAction: (item: Finding, action: "acknowledge" | "resolve" | "suppress" | "reopen") => Promise<void> }) {
  const filters: Array<["active" | FindingStatus | "all", string]> = [["active", "待处理 " + activeCount], ["all", "全部"], ["acknowledged", "处理中"], ["resolved", "已关闭"], ["suppressed", "已忽略"]];
  return <section className="network-workspace-card"><div className="network-workspace-head"><div><span className="network-kicker">异常闭环</span><h2>发现项</h2><p>只展示由巡检证据产生的风险或状态变化，不把工具调用异常当作业务结论。</p></div><div className="network-filters">{filters.map(([id, name]) => <button key={id} className={filter === id ? "active" : ""} onClick={() => onFilter(id)}>{name}</button>)}</div></div>{items.map((item) => <FindingRow key={item.finding_id} item={item} busy={busy} onAction={onAction} />)}{!items.length && <Empty title="没有符合条件的发现项" text="完成巡检后，连接异常、规则命中和基线变化会在这里排队处理。" />}</section>;
}
function Runs({ runs, activeRun, findings, busy, onOpen, onStart }: { runs: Inspection[]; activeRun: Inspection | null; findings: Finding[]; busy: boolean; onOpen: (run: Inspection) => void; onStart: () => void }) {
  const runFindings = activeRun ? findings.filter((item) => item.last_seen_task_id === activeRun.task_id) : [];
  return <section className="network-workspace-card"><div className="network-workspace-head"><div><span className="network-kicker">巡检执行</span><h2>巡检记录</h2><p>查看执行范围、设备结果和本次发现；任务状态与业务结论分别呈现。</p></div><Button variant="primary" size="sm" icon={<IconPlus size={16} />} onClick={onStart} disabled={busy}>新建巡检</Button></div><div className="network-run-workspace"><div className="network-run-list">{runs.map((run) => <button key={run.task_id} className={cls("network-run-row", activeRun?.task_id === run.task_id && "selected")} onClick={() => onOpen(run)}><span className={cls("network-status-dot", run.status)} /><div><b>{run.script?.name || "健康巡检"}</b><small>{stamp(run.created_at)} · {run.total} 台设备</small></div><span className="network-run-result">{run.succeeded} 成功 · {run.failed} 异常</span><em>{words[run.status] || run.status}</em></button>)}{!runs.length && <Empty title="还没有巡检记录" text="登记设备并选择策略后，就可以发起第一轮只读健康巡检。" />}</div><RunDetail run={activeRun} findings={runFindings} /></div></section>;
}
function Settings({ assets, scripts, baseline, baselines, selectedAssets, scriptId, busy, onAsset, onScript, onSelect, onChooseScript, onProbe, onRemove }: { assets: Asset[]; scripts: Script[]; baseline: Baseline | null; baselines: Baseline[]; selectedAssets: string[]; scriptId: string; busy: boolean; onAsset: () => void; onScript: () => void; onSelect: (id: string, checked: boolean) => void; onChooseScript: (id: string) => void; onProbe: (asset: Asset) => void; onRemove: (asset: Asset) => void }) {
  return <div className="network-settings-grid"><section className="network-workspace-card"><div className="network-workspace-head compact"><div><span className="network-kicker">巡检对象</span><h2>设备资产</h2><p>凭据仅保存为加密引用；硬删除不会篡改历史证据。</p></div><Button size="sm" variant="primary" icon={<IconPlus size={16} />} onClick={onAsset}>登记设备</Button></div><div className="network-asset-table">{assets.map((asset) => <article key={asset.asset_id}><label aria-label={"选择 " + asset.name}><input type="checkbox" checked={selectedAssets.includes(asset.asset_id)} onChange={(event) => onSelect(asset.asset_id, event.target.checked)} /><span /></label><div className="network-asset-main"><b>{asset.name}</b><small>{asset.host}:{asset.port} · {vendor(asset.vendor)}{asset.region ? " · " + asset.region : ""}</small></div><div className="network-asset-tags"><span className={asset.credential_configured ? "ready" : "missing"}>{asset.credential_configured ? "凭据已配置" : "缺少凭据"}</span><span className={asset.host_key_trusted ? "ready" : "pending"}>{asset.host_key_trusted ? "指纹已核验" : "待核验指纹"}</span></div><div className="network-row-actions"><button onClick={() => onProbe(asset)} disabled={busy}><IconProbe size={15} />测试</button><button className="danger" onClick={() => onRemove(asset)} disabled={busy}><IconTrash size={15} />删除</button></div></article>)}{!assets.length && <Empty title="还没有设备资产" text="先登记设备，再设置巡检范围。" />}</div></section>
    <section className="network-workspace-card"><div className="network-workspace-head compact"><div><span className="network-kicker">巡检方法</span><h2>巡检策略</h2><p>策略只包含只读命令与确定性发现规则。</p></div><Button size="sm" icon={<IconPlus size={16} />} onClick={onScript}>新建策略</Button></div><div className="network-script-list">{scripts.map((script) => <button key={script.script_id} className={script.script_id === scriptId ? "selected" : ""} onClick={() => onChooseScript(script.script_id)}><span className={cls("network-radio", script.script_id === scriptId && "checked")} /><div><b>{script.name}</b><small>{script.vendors.map(vendor).join("、")} · {script.commands.length} 条命令 · {script.checks?.length || 0} 条规则</small><p>{script.description || "未填写策略说明"}</p></div><span>用于巡检</span></button>)}{!scripts.length && <Empty title="还没有巡检策略" text="可直接创建一份只读策略，再配置需要识别的异常规则。" />}</div><div className="network-baseline-box"><IconShield size={18} /><div><b>状态基线</b><span>{baseline ? "当前已确认 · " + stamp(baseline.created_at) : "尚未建立。完成可信巡检后由人工确认。"}</span></div><small>{baselines.length} 份历史基线</small></div></section></div>;
}
function FindingRow({ item, compact = false, busy, onAction }: { item: Finding; compact?: boolean; busy: boolean; onAction: (item: Finding, action: "acknowledge" | "resolve" | "suppress" | "reopen") => Promise<void> }) {
  const active = item.status === "open" || item.status === "acknowledged";
  return <article className={cls("network-finding-item", compact && "compact")}><div className={cls("network-severity", item.severity)}><span>{words[item.severity]}</span></div><div className="network-finding-copy"><div><b>{item.title}</b><span>{item.asset_name} · {item.asset_host}</span></div><p>{item.description}</p><small><IconClock size={13} />{stamp(item.last_seen_at)} · 已出现 {item.occurrences} 次</small></div><div className="network-finding-actions"><em className={cls("finding-status", item.status)}>{words[item.status]}</em>{item.status === "open" && <Button size="sm" onClick={() => void onAction(item, "acknowledge")} disabled={busy}>确认跟进</Button>}{active && <Button size="sm" variant="primary" onClick={() => void onAction(item, "resolve")} disabled={busy}>关闭</Button>}{active && !compact && <button className="network-text-button muted" onClick={() => void onAction(item, "suppress")} disabled={busy}>忽略</button>}{!active && <Button size="sm" onClick={() => void onAction(item, "reopen")} disabled={busy}>重新打开</Button>}</div></article>;
}
function RunDetail({ run, findings }: { run: Inspection | null; findings: Finding[] }) {
  if (!run) return <aside className="network-run-detail empty"><IconHistory size={26} /><b>选择一条巡检记录</b><span>可查看设备结果、证据工件和本次发现项。</span></aside>;
  return <aside className="network-run-detail"><div className="network-detail-heading"><span className="network-kicker">执行详情</span><h3>{run.script?.name || "健康巡检"}</h3><p>{stamp(run.finished_at || run.created_at)} · {words[run.status] || run.status}</p></div><div className="network-detail-stats"><span><b>{run.total}</b>巡检设备</span><span><b>{run.succeeded}</b>成功</span><span><b>{run.failed}</b>异常</span></div><section><h4>设备结果</h4>{Object.entries(run.results || {}).map(([id, result]) => <div className="network-device-result" key={id}><span className={cls("network-status-dot", result.status)} /><div><b>{result.name || id}</b><small>{result.host || "未记录地址"}</small></div><em>{result.status === "succeeded" ? (result.commands?.length || 0) + " 条命令" : result.error || "执行异常"}</em></div>)}{!Object.keys(run.results || {}).length && <p className="network-detail-muted">任务正在执行，设备结果会自动出现。</p>}</section><section><h4>本次发现</h4>{findings.map((item) => <div className="network-run-finding" key={item.finding_id}><span className={cls("network-severity", item.severity)}>{words[item.severity]}</span><b>{item.title}</b></div>)}{!findings.length && <p className="network-detail-muted">本次没有产生规则发现项。</p>}</section>{run.artifact_id && <p className="network-evidence">证据工件：{run.artifact_id}</p>}</aside>;
}
function AssetSheet({ open, busy, form, onChange, onClose, onSubmit }: { open: boolean; busy: boolean; form: typeof assetBlank; onChange: (form: typeof assetBlank) => void; onClose: () => void; onSubmit: (event: FormEvent) => void }) {
  return <ModalShell open={open} onClose={onClose} title="登记网络设备" subtitle="保存后先进行连接验证，再加入巡检范围。" size="sheet"><form className="network-sheet-form" onSubmit={onSubmit}><div className="network-form-grid"><Field label="设备名称"><input value={form.name} onChange={(event) => onChange({ ...form, name: event.target.value })} required autoFocus /></Field><Field label="管理地址"><input value={form.host} onChange={(event) => onChange({ ...form, host: event.target.value })} placeholder="例如 10.10.1.1" required /></Field><Field label="登录账户"><input value={form.username} onChange={(event) => onChange({ ...form, username: event.target.value })} required /></Field><Field label="登录密码"><input type="password" autoComplete="new-password" value={form.password} onChange={(event) => onChange({ ...form, password: event.target.value })} required /></Field><Field label="设备厂商"><select value={form.vendor} onChange={(event) => onChange({ ...form, vendor: event.target.value })}><option value="h3c">H3C</option><option value="huawei">华为</option><option value="cisco">Cisco</option><option value="generic">通用设备</option></select></Field><Field label="所属区域"><input value={form.region} onChange={(event) => onChange({ ...form, region: event.target.value })} placeholder="可选" /></Field></div><div className="network-sheet-footer"><Button onClick={onClose}>取消</Button><Button type="submit" variant="primary" disabled={busy}>保存设备</Button></div></form></ModalShell>;
}
function ScriptSheet({ open, busy, form, onChange, onClose, onSubmit }: { open: boolean; busy: boolean; form: typeof scriptBlank; onChange: (form: typeof scriptBlank) => void; onClose: () => void; onSubmit: (event: FormEvent) => void }) {
  return <ModalShell open={open} onClose={onClose} title="新建巡检策略" subtitle="每条命令必须是只读命令；规则命中会形成可追溯发现项。" size="sheet"><form className="network-sheet-form" onSubmit={onSubmit}><Field label="策略名称"><input value={form.name} onChange={(event) => onChange({ ...form, name: event.target.value })} required autoFocus /></Field><Field label="适用厂商"><select value={form.vendors} onChange={(event) => onChange({ ...form, vendors: event.target.value })}><option value="h3c">H3C</option><option value="huawei">华为</option><option value="cisco">Cisco</option><option value="generic">通用设备</option></select></Field><Field label="策略说明"><input value={form.description} onChange={(event) => onChange({ ...form, description: event.target.value })} placeholder="说明此策略检查什么" /></Field><Field label="只读命令（每行一条）"><textarea rows={7} value={form.commands} onChange={(event) => onChange({ ...form, commands: event.target.value })} placeholder="display logbuffer | include ERROR" required /></Field><div className="network-rule-box"><b>发现规则</b><span>可选。命令输出匹配规则后会自动生成发现项。</span><Field label="规则名称"><input value={form.ruleName} onChange={(event) => onChange({ ...form, ruleName: event.target.value })} placeholder="例如：严重日志" /></Field><Field label="匹配表达式"><input value={form.rulePattern} onChange={(event) => onChange({ ...form, rulePattern: event.target.value })} placeholder="ERROR|FATAL" /></Field><Field label="命中严重度"><select value={form.ruleSeverity} onChange={(event) => onChange({ ...form, ruleSeverity: event.target.value })}><option value="low">低</option><option value="medium">中</option><option value="high">高</option><option value="critical">严重</option></select></Field></div><div className="network-sheet-footer"><Button onClick={onClose}>取消</Button><Button type="submit" variant="primary" disabled={busy}>保存策略</Button></div></form></ModalShell>;
}
function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="network-field"><span>{label}</span>{children}</label>; }
function Empty({ title, text }: { title: string; text: string }) { return <div className="network-empty-state"><IconShield size={24} /><b>{title}</b><span>{text}</span></div>; }
