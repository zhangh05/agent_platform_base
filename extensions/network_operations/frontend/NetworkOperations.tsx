import { useCallback, useEffect, useMemo, useState } from "react";
import { apiRequest } from "../../../frontend/src/api/client";
import { useSessionStore } from "../../../frontend/src/stores/session";
import "./NetworkOperations.css";

type Asset = {
  asset_id: string; name: string; host: string; port: number; username: string;
  vendor: string; region: string; auth_method?: string; credential_configured: boolean; host_key_trusted?: boolean;
};
type InspectionResult = {
  status: string; name?: string; host?: string; commands?: string[]; output_hash?: string; duration_ms?: number; error?: string;
};
type Inspection = {
  task_id: string; status: string; script?: { name?: string; script_id?: string }; total: number; completed: number;
  succeeded: number; failed: number; created_at: string; finished_at?: string; artifact_id?: string; results?: Record<string, InspectionResult>;
};
type InspectionDiff = { baseline_id: string; task_id: string; changed: boolean; changes: Array<{ asset_id: string; before: { status?: string; output_hash?: string } | null; after: { status?: string; output_hash?: string } | null }> };
type EvidenceSummary = { artifact_id: string; artifact_sensitivity: string; devices: Array<{ asset_id: string; name: string; host: string; status: string; command_count: number; output_hash: string; duration_ms: number; error: string }> };
type Baseline = {
  baseline_id: string; task_id: string; confirmed: boolean; current: boolean; created_at: string;
};
type InspectionScript = { script_id: string; name: string; description: string; vendors: string[]; commands: string[]; builtin?: boolean; version?: number };
type Schedule = { schedule_id: string; name: string; asset_ids: string[]; script_id: string; script_name: string; interval_minutes: number; enabled: boolean; next_run_at_epoch: number; last_task_id?: string; last_error?: string };
type Tab = "assets" | "scripts" | "inspections" | "baselines" | "schedules";

const base = "/extensions/network.operations";

export default function NetworkOperations() {
  const workspaceId = useSessionStore((state) => state.currentWorkspaceId);
  const [tab, setTab] = useState<Tab>("assets");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [inspections, setInspections] = useState<Inspection[]>([]);
  const [baselines, setBaselines] = useState<Baseline[]>([]);
  const [scripts, setScripts] = useState<InspectionScript[]>([]);
  const [scriptId, setScriptId] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [probeStatus, setProbeStatus] = useState<Record<string, string>>({});
  const [form, setForm] = useState({ name: "", host: "", port: "22", username: "", password: "", private_key: "", key_passphrase: "", auth_method: "password", vendor: "h3c", region: "" });
  const [scriptForm, setScriptForm] = useState({ name: "", description: "", vendors: "h3c", commands: "" });
  const [editingScriptId, setEditingScriptId] = useState("");
  const [activeTask, setActiveTask] = useState<Inspection | null>(null);
  const [taskDiff, setTaskDiff] = useState<InspectionDiff | null>(null);
  const [diffMessage, setDiffMessage] = useState("");
  const [evidence, setEvidence] = useState<EvidenceSummary | null>(null);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [scheduleForm, setScheduleForm] = useState({ name: "", interval_minutes: "60" });

  const load = useCallback(async () => {
    const params = { workspace_id: workspaceId };
    const [assetRes, inspectionRes, baselineRes, scriptRes, scheduleRes] = await Promise.all([
      apiRequest<{ assets: Asset[] }>({ method: "GET", url: `${base}/assets`, params }),
      apiRequest<{ inspections: Inspection[] }>({ method: "GET", url: `${base}/inspections`, params }),
      apiRequest<{ baselines: Baseline[] }>({ method: "GET", url: `${base}/baselines`, params }),
      apiRequest<{ scripts: InspectionScript[] }>({ method: "GET", url: `${base}/scripts`, params }),
      apiRequest<{ schedules: Schedule[] }>({ method: "GET", url: `${base}/schedules`, params }),
    ]);
    setAssets(assetRes.assets || []);
    setInspections(inspectionRes.inspections || []);
    setBaselines(baselineRes.baselines || []);
    setScripts(scriptRes.scripts || []);
    setSchedules(scheduleRes.schedules || []);
    if (!scriptId && scriptRes.scripts?.length) setScriptId(scriptRes.scripts[0].script_id);
  }, [workspaceId, scriptId]);

  useEffect(() => {
    load().catch(() => setError("网络巡检数据暂时无法读取"));
  }, [load]);

  useEffect(() => {
    if (!inspections.some((item) => ["queued", "running"].includes(item.status))) return;
    const timer = window.setInterval(() => void load(), 1500);
    return () => window.clearInterval(timer);
  }, [inspections, load]);

  const latestCompleted = useMemo(
    () => inspections.find((item) => ["succeeded", "partial"].includes(item.status)),
    [inspections],
  );
  const currentBaseline = baselines.find((item) => item.current && item.confirmed);

  async function saveAsset(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      await apiRequest({ method: "POST", url: `${base}/assets`, data: { ...form, port: Number(form.port), workspace_id: workspaceId } });
      setForm({ name: "", host: "", port: "22", username: "", password: "", private_key: "", key_passphrase: "", auth_method: "password", vendor: "h3c", region: "" });
      await load();
    } catch (err) {
      setError(String((err as { message?: string })?.message || "设备保存失败"));
    } finally { setBusy(false); }
  }

  async function removeAsset(assetId: string) {
    setBusy(true);
    try {
      await apiRequest({ method: "DELETE", url: `${base}/assets/${assetId}`, params: { workspace_id: workspaceId } });
      setSelected((value) => value.filter((id) => id !== assetId));
      await load();
    } finally { setBusy(false); }
  }

  async function runInspection() {
    if (!scriptId) { setError("请先选择巡检脚本。"); setTab("scripts"); return; }
    setBusy(true); setError("");
    try {
      await apiRequest({ method: "POST", url: `${base}/inspections`, data: { workspace_id: workspaceId, asset_ids: selected, script_id: scriptId } });
      setTab("inspections");
      await load();
    } catch (err) {
      setError(String((err as { message?: string })?.message || "巡检启动失败"));
    } finally { setBusy(false); }
  }

  async function probeAsset(asset: Asset, acceptHostKey = false) {
    setBusy(true); setError("");
    setProbeStatus((value) => ({ ...value, [asset.asset_id]: acceptHostKey ? "正在信任指纹并复测..." : "正在测试连接..." }));
    try {
      const result = await apiRequest<{ ok: boolean; status: string; error?: string; fingerprint?: string; requires_host_key_acceptance?: boolean; host_key_saved?: boolean }>({
        method: "POST",
        url: `${base}/assets/${asset.asset_id}/probe`,
        data: { workspace_id: workspaceId, accept_host_key: acceptHostKey },
      });
      if (result.requires_host_key_acceptance) {
        setProbeStatus((value) => ({ ...value, [asset.asset_id]: `发现新指纹 ${result.fingerprint || ""}，请确认信任` }));
      } else {
        setProbeStatus((value) => ({ ...value, [asset.asset_id]: result.ok ? (result.host_key_saved ? "连接成功，指纹已保存" : "连接成功") : `连接失败：${result.error || result.status}` }));
      }
      await load();
    } catch (err) {
      setProbeStatus((value) => ({ ...value, [asset.asset_id]: `连接失败：${String((err as { message?: string })?.message || err)}` }));
    } finally { setBusy(false); }
  }

  async function saveScript(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = await apiRequest<{ script: InspectionScript }>({ method: "POST", url: `${base}/scripts`, data: { workspace_id: workspaceId, script_id: editingScriptId || undefined, name: scriptForm.name, description: scriptForm.description, vendors: scriptForm.vendors.split(",").map((item) => item.trim()).filter(Boolean), commands: scriptForm.commands.split("\n").map((item) => item.trim()).filter(Boolean) } });
      setScriptId(result.script.script_id); setEditingScriptId(""); setScriptForm({ name: "", description: "", vendors: "h3c", commands: "" }); await load();
    } catch (err) { setError(String((err as { message?: string })?.message || "脚本保存失败")); } finally { setBusy(false); }
  }
  async function removeScript(script: InspectionScript) {
    if (!window.confirm(`确定删除巡检脚本“${script.name}”吗？`)) return;
    setBusy(true); setError("");
    try { await apiRequest({ method: "DELETE", url: `${base}/scripts/${script.script_id}`, params: { workspace_id: workspaceId } }); if (scriptId === script.script_id) setScriptId(""); await load(); }
    catch (err) { setError(String((err as { message?: string })?.message || "脚本删除失败")); } finally { setBusy(false); }
  }

  async function openTask(task: Inspection) {
    setBusy(true); setError(""); setDiffMessage(""); setTaskDiff(null); setEvidence(null);
    try {
      const detail = await apiRequest<{ task: Inspection }>({ method: "GET", url: `${base}/inspections/${task.task_id}`, params: { workspace_id: workspaceId } });
      setActiveTask(detail.task);
      if (currentBaseline && ["succeeded", "partial"].includes(detail.task.status)) {
        try {
          const diff = await apiRequest<InspectionDiff>({ method: "GET", url: `${base}/diff`, params: { workspace_id: workspaceId, task_id: detail.task.task_id } });
          setTaskDiff(diff);
        } catch (err) { setDiffMessage(String((err as { message?: string })?.message || "当前结果尚不能与基线比较")); }
      } else if (!currentBaseline) {
        setDiffMessage("尚未确认当前基线；确认一份可信结果后可查看设备变化。");
      }
      if (detail.task.artifact_id) {
        try {
          const summary = await apiRequest<EvidenceSummary>({ method: "GET", url: `${base}/inspections/${task.task_id}/evidence`, params: { workspace_id: workspaceId } });
          setEvidence(summary);
        } catch { setEvidence(null); }
      }
    } catch (err) { setError(String((err as { message?: string })?.message || "巡检结果读取失败")); }
    finally { setBusy(false); }
  }
  async function cancelTask(task: Inspection) {
    if (!window.confirm(`确定取消巡检任务 ${task.task_id} 吗？`)) return;
    setBusy(true); setError("");
    try {
      await apiRequest({ method: "POST", url: `${base}/inspections/${task.task_id}/cancel`, data: { workspace_id: workspaceId } });
      await load();
      if (activeTask?.task_id === task.task_id) await openTask(task);
    } catch (err) { setError(String((err as { message?: string })?.message || "巡检取消失败")); }
    finally { setBusy(false); }
  }
  async function saveSchedule(event: React.FormEvent) {
    event.preventDefault();
    if (!scriptId) { setError("请先选择巡检脚本后再创建计划。"); setTab("scripts"); return; }
    if (selected.length === 0) { setError("请先在设备资产页选择计划巡检的设备。"); setTab("assets"); return; }
    setBusy(true); setError("");
    try {
      await apiRequest({ method: "POST", url: `${base}/schedules`, data: { workspace_id: workspaceId, name: scheduleForm.name, interval_minutes: Number(scheduleForm.interval_minutes), asset_ids: selected, script_id: scriptId, enabled: true } });
      setScheduleForm({ name: "", interval_minutes: "60" });
      await load();
    } catch (err) { setError(String((err as { message?: string })?.message || "计划巡检保存失败")); }
    finally { setBusy(false); }
  }
  async function removeSchedule(schedule: Schedule) {
    if (!window.confirm(`确定删除计划巡检“${schedule.name}”吗？`)) return;
    setBusy(true); setError("");
    try { await apiRequest({ method: "DELETE", url: `${base}/schedules/${schedule.schedule_id}`, params: { workspace_id: workspaceId } }); await load(); }
    catch (err) { setError(String((err as { message?: string })?.message || "计划巡检删除失败")); }
    finally { setBusy(false); }
  }
  async function createBaseline() {
    const target = activeTask && ["succeeded", "partial"].includes(activeTask.status) ? activeTask : latestCompleted;
    if (!target) return;
    if (!window.confirm(`确认将任务 ${target.task_id} 设为当前状态基线吗？`)) return;
    setBusy(true); setError("");
    try {
      await apiRequest({ method: "POST", url: `${base}/baselines`, data: { workspace_id: workspaceId, task_id: target.task_id, confirm: true } });
      await load();
      await openTask(target);
      setTab("baselines");
    } catch (err) { setError(String((err as { message?: string })?.message || "基线确认失败")); }
    finally { setBusy(false); }
  }

  return (
    <div className="page network-ops-page">
      <header className="page-header ui-page-header">
        <div>
          <h1>网络巡检 <span>只读采集与状态基线</span></h1>
          <p className="subtitle">设备凭据加密保存；巡检命令经过只读校验，不执行配置变更。</p>
        </div>
        <div className="network-ops-actions">
          <button className="btn secondary" onClick={() => void load()} disabled={busy}>刷新</button>
          <button className="btn primary" onClick={runInspection} disabled={busy || assets.length === 0 || !scriptId}>发起巡检</button>
        </div>
      </header>

      <div className="page-body network-ops-body">
        {error ? <div className="network-ops-error" role="alert">{error}</div> : null}
        <section className="network-ops-summary">
          <div><strong>{assets.length}</strong><span>设备资产</span></div>
          <div><strong>{scripts.length}</strong><span>巡检脚本</span></div>
          <div><strong>{currentBaseline ? "已确认" : "未建立"}</strong><span>当前基线</span></div>
          <div><strong>{inspections[0]?.status || "暂无"}</strong><span>最近状态</span></div>
        </section>

        <nav className="network-ops-tabs" aria-label="网络巡检视图">
          <button className={tab === "assets" ? "active" : ""} onClick={() => setTab("assets")}>设备资产</button>
          <button className={tab === "scripts" ? "active" : ""} onClick={() => setTab("scripts")}>巡检脚本</button>
          <button className={tab === "inspections" ? "active" : ""} onClick={() => setTab("inspections")}>巡检记录</button>
          <button className={tab === "baselines" ? "active" : ""} onClick={() => setTab("baselines")}>状态基线</button>
          <button className={tab === "schedules" ? "active" : ""} onClick={() => setTab("schedules")}>计划巡检</button>
        </nav>

        {tab === "assets" ? (
          <div className="network-assets-layout">
            <form className="network-asset-form" onSubmit={saveAsset}>
              <div className="network-script-select"><b>本次巡检脚本</b><select value={scriptId} onChange={(event) => setScriptId(event.target.value)}><option value="">请选择脚本</option>{scripts.map((script) => <option key={script.script_id} value={script.script_id}>{script.name}</option>)}</select><button type="button" className="network-link" onClick={() => setTab("scripts")}>管理脚本</button></div>
              <div className="network-section-head"><h2>添加设备</h2><p>凭据只写入加密密钥库</p></div>
              <label><span>设备名称</span><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></label>
              <div className="network-form-row">
                <label><span>管理地址</span><input value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} required /></label>
                <label className="network-port"><span>端口</span><input value={form.port} onChange={(e) => setForm({ ...form, port: e.target.value })} /></label>
              </div>
              <div className="network-form-row">
                <label><span>登录账户</span><input autoComplete="username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required /></label>
                <label><span>厂商</span><select value={form.vendor} onChange={(e) => setForm({ ...form, vendor: e.target.value })}><option value="h3c">H3C</option><option value="huawei">华为</option><option value="cisco">Cisco</option><option value="generic">通用主机</option></select></label>
              </div>
              <label><span>认证方式</span><select value={form.auth_method} onChange={(e) => setForm({ ...form, auth_method: e.target.value })}><option value="password">密码</option><option value="private_key">私钥</option></select></label>
              {form.auth_method === "password" ? (
                <label><span>登录密码</span><input type="password" autoComplete="current-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required /></label>
              ) : (
                <>
                  <label><span>私钥</span><textarea value={form.private_key} onChange={(e) => setForm({ ...form, private_key: e.target.value })} required rows={5} spellCheck={false} /></label>
                  <label><span>私钥口令</span><input type="password" autoComplete="new-password" value={form.key_passphrase} onChange={(e) => setForm({ ...form, key_passphrase: e.target.value })} /></label>
                </>
              )}
              <label><span>区域</span><input value={form.region} onChange={(e) => setForm({ ...form, region: e.target.value })} /></label>
              <button className="btn primary" type="submit" disabled={busy}>保存设备</button>
            </form>

            <section className="network-list-panel">
              <div className="network-section-head"><h2>设备列表</h2><p>勾选需要巡检的设备；未勾选时默认巡检全部</p></div>
              {assets.length === 0 ? <div className="network-empty">尚未添加设备</div> : assets.map((asset) => (
                <article className="network-asset-row" key={asset.asset_id}>
                  <input type="checkbox" aria-label={`选择 ${asset.name}`} checked={selected.includes(asset.asset_id)} onChange={(e) => setSelected((value) => e.target.checked ? [...value, asset.asset_id] : value.filter((id) => id !== asset.asset_id))} />
                  <div className="network-asset-main"><strong>{asset.name}</strong><span>{asset.host}:{asset.port} · {asset.vendor || "generic"}</span></div>
                  <div className="network-asset-meta"><span className={asset.credential_configured ? "ok" : "warn"}>{asset.credential_configured ? `${asset.auth_method === "private_key" ? "私钥" : "密码"}已配置` : "缺少凭据"}</span><span className={asset.host_key_trusted ? "ok" : "warn"}>{asset.host_key_trusted ? "指纹已信任" : "指纹待信任"}</span><span>{asset.region || "未分区"}</span></div>
                  <div className="network-row-actions">
                    <button className="network-link" onClick={() => void probeAsset(asset)} disabled={busy}>测试连接</button>
                    {!asset.host_key_trusted && probeStatus[asset.asset_id]?.includes("发现新指纹") ? <button className="network-link" onClick={() => void probeAsset(asset, true)} disabled={busy}>信任并重试</button> : null}
                    <button className="network-delete" onClick={() => void removeAsset(asset.asset_id)} disabled={busy}>删除</button>
                  </div>
                  {probeStatus[asset.asset_id] ? <div className="network-probe-status">{probeStatus[asset.asset_id]}</div> : null}
                </article>
              ))}
            </section>
          </div>
        ) : null}

        {tab === "scripts" ? (
          <div className="network-scripts-layout">
            <form className="network-script-form" onSubmit={saveScript}>
              <div className="network-section-head"><h2>{editingScriptId ? "编辑只读巡检脚本" : "新建只读巡检脚本"}</h2><p>每行一条命令；系统会拒绝配置、重启、删除等高风险命令。</p></div>
              <label><span>脚本名称</span><input value={scriptForm.name} onChange={(event) => setScriptForm({ ...scriptForm, name: event.target.value })} placeholder="例如：核心交换机健康检查" required /></label>
              <label><span>适用厂商</span><select value={scriptForm.vendors} onChange={(event) => setScriptForm({ ...scriptForm, vendors: event.target.value })}><option value="h3c">H3C</option><option value="huawei">华为</option><option value="cisco">Cisco</option><option value="generic">通用主机</option></select></label>
              <label><span>说明（可选）</span><input value={scriptForm.description} onChange={(event) => setScriptForm({ ...scriptForm, description: event.target.value })} placeholder="说明这份巡检要检查什么" /></label>
              <label><span>只读命令</span><textarea value={scriptForm.commands} onChange={(event) => setScriptForm({ ...scriptForm, commands: event.target.value })} placeholder={"display version\ndisplay interface brief"} rows={7} required /></label>
              <div className="network-form-row"><button className="btn primary" type="submit" disabled={busy}>保存脚本</button>{editingScriptId ? <button className="btn secondary" type="button" onClick={() => { setEditingScriptId(""); setScriptForm({ name: "", description: "", vendors: "h3c", commands: "" }); }}>取消编辑</button> : null}</div>
            </form>
            <section className="network-list-panel">
              <div className="network-section-head"><h2>脚本库</h2><p>基础脚本是本工作区的初始模板，可直接修改或删除。</p></div>
              {scripts.length === 0 ? <div className="network-empty">暂无可用脚本</div> : scripts.map((script) => <article className="network-script-row" key={script.script_id}><div><strong>{script.name}</strong><span>{script.vendors.join("、")} · {script.commands.length} 条只读命令{script.builtin ? " · 内置" : ""}</span><p>{script.description || "未填写说明"}</p><code>{script.commands.join("\n")}</code></div><div className="network-row-actions"><button className="network-link" onClick={() => { setScriptId(script.script_id); setTab("assets"); }}>用于巡检</button><button className="network-link" onClick={() => { setEditingScriptId(script.script_id); setScriptForm({ name: script.name, description: script.description || "", vendors: script.vendors.join(","), commands: script.commands.join("\n") }); }}>编辑</button><button className="network-link" onClick={() => { setEditingScriptId(""); setScriptForm({ name: `${script.name} 副本`, description: script.description || "", vendors: script.vendors.join(","), commands: script.commands.join("\n") }); }}>复制</button><button className="network-delete" onClick={() => void removeScript(script)} disabled={busy}>删除</button></div></article>)}
            </section>
          </div>
        ) : null}
        {tab === "inspections" ? (
          <section className="network-list-panel full">
            <div className="network-section-head"><h2>巡检记录</h2><p>任务完成后查看每台设备结果、基线差异和受保护证据摘要。</p></div>
            {inspections.length === 0 ? <div className="network-empty">暂无巡检记录</div> : inspections.map((task) => (
              <article className={`network-task-row ${activeTask?.task_id === task.task_id ? "selected" : ""}`} key={task.task_id}>
                <div><strong>{task.script?.name || "未记录脚本"}</strong><span>{new Date(task.created_at).toLocaleString()} · {task.task_id}</span></div>
                <div className="network-progress"><span style={{ width: `${task.total ? Math.round(task.completed / task.total * 100) : 0}%` }} /></div>
                <div className="network-counts"><span>完成 {task.completed}/{task.total}</span><span className="ok">成功 {task.succeeded}</span><span className={task.failed ? "danger" : ""}>失败 {task.failed}</span></div>
                <span className={`network-status ${task.status}`}>{task.status}</span>
                <div className="network-row-actions"><button className="network-link" onClick={() => void openTask(task)} disabled={busy}>查看结果</button>{["queued", "running"].includes(task.status) ? <button className="network-delete" onClick={() => void cancelTask(task)} disabled={busy}>取消</button> : null}</div>
              </article>
            ))}
            {activeTask ? <section className="network-task-detail" aria-label="巡检结果详情">
              <div className="network-section-head"><div><h3>任务详情</h3><p>{activeTask.task_id} · {activeTask.finished_at ? new Date(activeTask.finished_at).toLocaleString() : "执行中"}</p></div><span className={`network-status ${activeTask.status}`}>{activeTask.status}</span></div>
              <div className="network-result-list">{Object.entries(activeTask.results || {}).map(([assetId, result]) => <article key={assetId} className={`network-result-row ${result.status}`}><div><strong>{result.name || assetId}</strong><span>{result.host || "未记录地址"} · {result.duration_ms || 0} ms</span></div><div><span>{result.commands?.length || 0} 条命令</span><code>{result.output_hash || "无输出哈希"}</code></div><div>{result.error ? <p className="danger">{result.error}</p> : <span className="ok">{result.status}</span>}</div></article>)}</div>
              <div className="network-evidence-panel"><strong>证据摘要</strong>{evidence ? <span>工件 {evidence.artifact_id} · {evidence.devices.length} 台设备 · 原始命令输出按敏感数据策略受保护，已保留输出哈希供复核。</span> : <span>{activeTask.artifact_id ? "证据索引读取中或不可用" : "任务尚未生成证据工件"}</span>}</div>
              <div className="network-diff-panel"><strong>基线差异</strong>{taskDiff ? <>{taskDiff.changed ? <div>{taskDiff.changes.map((change) => <p key={change.asset_id}>{change.asset_id}：{change.before?.status || "无"} → {change.after?.status || "无"}{change.before?.output_hash !== change.after?.output_hash ? "，输出发生变化" : ""}</p>)}</div> : <span className="ok">与当前基线一致</span>}</> : <span>{diffMessage || "选择已完成任务后加载差异"}</span>}</div>
            </section> : null}
            <div className="network-panel-actions"><button className="btn primary" onClick={createBaseline} disabled={!(activeTask || latestCompleted) || busy}>将选定结果确认为基线</button></div>
          </section>
        ) : null}

        {tab === "schedules" ? (
          <div className="network-schedules-layout">
            <form className="network-asset-form" onSubmit={saveSchedule}>
              <div className="network-section-head"><h2>新建计划巡检</h2><p>按选定设备和脚本周期创建持久任务；实际执行由后台 Worker 接管。</p></div>
              <label><span>计划名称</span><input value={scheduleForm.name} onChange={(event) => setScheduleForm({ ...scheduleForm, name: event.target.value })} placeholder="例如：核心设备每小时健康巡检" required /></label>
              <label><span>执行间隔（分钟）</span><input type="number" min="5" max="10080" value={scheduleForm.interval_minutes} onChange={(event) => setScheduleForm({ ...scheduleForm, interval_minutes: event.target.value })} required /></label>
              <p className="text-xs muted">当前将使用“设备资产”页已选的 {selected.length} 台设备和脚本“{scripts.find((item) => item.script_id === scriptId)?.name || "未选择"}”。</p>
              <button className="btn primary" type="submit" disabled={busy || !scriptId || selected.length === 0}>创建计划</button>
            </form>
            <section className="network-list-panel">
              <div className="network-section-head"><h2>已配置计划</h2><p>每次到期只入队一次；Worker 重启后计划配置和待执行任务仍会保留。</p></div>
              {schedules.length === 0 ? <div className="network-empty">暂无计划巡检</div> : schedules.map((schedule) => <article className="network-schedule-row" key={schedule.schedule_id}><div><strong>{schedule.name}</strong><span>{schedule.script_name} · {schedule.asset_ids.length} 台设备 · 每 {schedule.interval_minutes} 分钟</span><p>下次执行：{schedule.next_run_at_epoch ? new Date(schedule.next_run_at_epoch * 1000).toLocaleString() : "待调度"}{schedule.last_task_id ? ` · 最近任务 ${schedule.last_task_id}` : ""}</p>{schedule.last_error ? <p className="danger">{schedule.last_error}</p> : null}</div><button className="network-delete" onClick={() => void removeSchedule(schedule)} disabled={busy}>删除</button></article>)}
            </section>
          </div>
        ) : null}

        {tab === "baselines" ? (
          <section className="network-list-panel full">
            <div className="network-section-head"><h2>状态基线</h2><p>只有人工确认的巡检结果才能成为当前状态依据</p></div>
            {baselines.length === 0 ? <div className="network-empty">尚未建立状态基线</div> : baselines.map((baseline) => (
              <article className={`network-baseline-row ${baseline.current ? "current" : ""}`} key={baseline.baseline_id}>
                <div><strong>{baseline.baseline_id}</strong><span>来源任务 {baseline.task_id}</span></div>
                <div><span>{new Date(baseline.created_at).toLocaleString()}</span></div>
                <span className={baseline.current ? "network-current" : "network-history"}>{baseline.current ? "当前已确认" : baseline.confirmed ? "历史基线" : "待确认"}</span>
              </article>
            ))}
          </section>
        ) : null}
      </div>
    </div>
  );
}
