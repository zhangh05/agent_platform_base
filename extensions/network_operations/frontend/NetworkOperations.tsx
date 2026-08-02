import { useCallback, useEffect, useMemo, useState } from "react";
import { apiRequest } from "../../../frontend/src/api/client";
import { useSessionStore } from "../../../frontend/src/stores/session";
import "./NetworkOperations.css";

type Asset = {
  asset_id: string; name: string; host: string; port: number; username: string;
  vendor: string; region: string; auth_method?: string; credential_configured: boolean; host_key_trusted?: boolean;
};
type Inspection = {
  task_id: string; status: string; total: number; completed: number;
  succeeded: number; failed: number; created_at: string; artifact_id?: string;
};
type Baseline = {
  baseline_id: string; task_id: string; confirmed: boolean; current: boolean; created_at: string;
};
type Tab = "assets" | "inspections" | "baselines";

const base = "/extensions/network.operations";

export default function NetworkOperations() {
  const workspaceId = useSessionStore((state) => state.currentWorkspaceId);
  const [tab, setTab] = useState<Tab>("assets");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [inspections, setInspections] = useState<Inspection[]>([]);
  const [baselines, setBaselines] = useState<Baseline[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [probeStatus, setProbeStatus] = useState<Record<string, string>>({});
  const [form, setForm] = useState({ name: "", host: "", port: "22", username: "", password: "", private_key: "", key_passphrase: "", auth_method: "password", vendor: "h3c", region: "" });

  const load = useCallback(async () => {
    const params = { workspace_id: workspaceId };
    const [assetRes, inspectionRes, baselineRes] = await Promise.all([
      apiRequest<{ assets: Asset[] }>({ method: "GET", url: `${base}/assets`, params }),
      apiRequest<{ inspections: Inspection[] }>({ method: "GET", url: `${base}/inspections`, params }),
      apiRequest<{ baselines: Baseline[] }>({ method: "GET", url: `${base}/baselines`, params }),
    ]);
    setAssets(assetRes.assets || []);
    setInspections(inspectionRes.inspections || []);
    setBaselines(baselineRes.baselines || []);
  }, [workspaceId]);

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
    setBusy(true); setError("");
    try {
      await apiRequest({ method: "POST", url: `${base}/inspections`, data: { workspace_id: workspaceId, asset_ids: selected } });
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

  async function createBaseline() {
    if (!latestCompleted) return;
    setBusy(true);
    try {
      await apiRequest({ method: "POST", url: `${base}/baselines`, data: { workspace_id: workspaceId, task_id: latestCompleted.task_id, confirm: true } });
      setTab("baselines");
      await load();
    } finally { setBusy(false); }
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
          <button className="btn primary" onClick={runInspection} disabled={busy || assets.length === 0}>发起巡检</button>
        </div>
      </header>

      <div className="page-body network-ops-body">
        {error ? <div className="network-ops-error" role="alert">{error}</div> : null}
        <section className="network-ops-summary">
          <div><strong>{assets.length}</strong><span>设备资产</span></div>
          <div><strong>{inspections.length}</strong><span>巡检任务</span></div>
          <div><strong>{currentBaseline ? "已确认" : "未建立"}</strong><span>当前基线</span></div>
          <div><strong>{inspections[0]?.status || "暂无"}</strong><span>最近状态</span></div>
        </section>

        <nav className="network-ops-tabs" aria-label="网络巡检视图">
          <button className={tab === "assets" ? "active" : ""} onClick={() => setTab("assets")}>设备资产</button>
          <button className={tab === "inspections" ? "active" : ""} onClick={() => setTab("inspections")}>巡检记录</button>
          <button className={tab === "baselines" ? "active" : ""} onClick={() => setTab("baselines")}>状态基线</button>
        </nav>

        {tab === "assets" ? (
          <div className="network-assets-layout">
            <form className="network-asset-form" onSubmit={saveAsset}>
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

        {tab === "inspections" ? (
          <section className="network-list-panel full">
            <div className="network-section-head"><h2>巡检记录</h2><p>最多并发 5 台，任务可取消</p></div>
            {inspections.length === 0 ? <div className="network-empty">暂无巡检记录</div> : inspections.map((task) => (
              <article className="network-task-row" key={task.task_id}>
                <div><strong>{task.task_id}</strong><span>{new Date(task.created_at).toLocaleString()}</span></div>
                <div className="network-progress"><span style={{ width: `${task.total ? Math.round(task.completed / task.total * 100) : 0}%` }} /></div>
                <div className="network-counts"><span>完成 {task.completed}/{task.total}</span><span className="ok">成功 {task.succeeded}</span><span className={task.failed ? "danger" : ""}>失败 {task.failed}</span></div>
                <span className={`network-status ${task.status}`}>{task.status}</span>
              </article>
            ))}
            <div className="network-panel-actions"><button className="btn primary" onClick={createBaseline} disabled={!latestCompleted || busy}>将最近结果确认为基线</button></div>
          </section>
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
