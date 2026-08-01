import { useCallback, useEffect, useState } from "react";
import { extensionsApi, type ExtensionPackageRecord, type InstalledExtension } from "../../api";
import { useSessionStore } from "../../stores/session";

export function ExtensionCenter() {
  const workspaceId = useSessionStore((state) => state.currentWorkspaceId);
  const [items, setItems] = useState<InstalledExtension[]>([]);
  const [packages, setPackages] = useState<ExtensionPackageRecord[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [installed, repository] = await Promise.all([extensionsApi.list(), extensionsApi.repository()]);
    setItems(installed.extensions || []);
    setPackages(repository.packages || []);
  }, []);

  useEffect(() => { load().catch(() => setError("扩展目录读取失败")); }, [load]);

  async function changeState(item: InstalledExtension) {
    setBusy(item.extension_id); setError("");
    try {
      if (item.lifecycle?.enabled === false) await extensionsApi.enable(item.extension_id);
      else await extensionsApi.disable(item.extension_id);
      await load();
    } catch (err) {
      setError(String((err as { message?: string })?.message || "扩展状态更新失败"));
    } finally { setBusy(""); }
  }

  async function migrate(item: InstalledExtension) {
    setBusy(item.extension_id); setError("");
    try { await extensionsApi.migrate(item.extension_id, workspaceId); await load(); }
    catch (err) { setError(String((err as { message?: string })?.message || "数据迁移失败")); }
    finally { setBusy(""); }
  }

  async function publish(file?: File) {
    if (!file) return;
    setBusy("publish"); setError("");
    try { await extensionsApi.publish(file); await load(); }
    catch (err) { setError(String((err as { message?: string })?.message || "扩展包发布失败")); }
    finally { setBusy(""); }
  }

  async function install(item: ExtensionPackageRecord) {
    const current = items.find((candidate) => candidate.extension_id === item.extension_id);
    setBusy(`${item.extension_id}@${item.version}`); setError("");
    try { await extensionsApi.install(item.extension_id, item.version, Boolean(current)); await load(); }
    catch (err) { setError(String((err as { message?: string })?.message || "扩展安装失败")); }
    finally { setBusy(""); }
  }

  async function uninstall(item: InstalledExtension) {
    if (!window.confirm(`确认卸载“${item.name}”？扩展会移入可恢复区，数据不会删除。`)) return;
    setBusy(item.extension_id); setError("");
    try { await extensionsApi.uninstall(item.extension_id); await load(); }
    catch (err) { setError(String((err as { message?: string })?.message || "扩展卸载失败")); }
    finally { setBusy(""); }
  }

  return (
    <div className="page">
      <header className="page-header ui-page-header">
        <div><h1>扩展管理 <span>Extension Center</span></h1><p className="subtitle">统一查看扩展版本、权限、运行状态、签名包和工作区迁移。</p></div>
        <div className="extension-header-actions"><label className={`btn primary ${busy === "publish" ? "disabled" : ""}`}>发布签名包<input type="file" accept=".apx" hidden disabled={busy === "publish"} onChange={(event) => { void publish(event.target.files?.[0]); event.target.value = ""; }} /></label><button className="btn secondary" onClick={() => void load()}>刷新</button></div>
      </header>
      <div className="page-body">
        {error ? <div className="extension-center-error" role="alert">{error}</div> : null}
        <div className="extension-center-grid">
          {items.map((item) => {
            const enabled = item.lifecycle?.enabled !== false;
            return (
              <article className="extension-card" key={item.extension_id}>
                <div className="extension-card-head">
                  <div><h2>{item.name}</h2><p>{item.extension_id} · v{item.version}</p></div>
                  <span className={`extension-state ${enabled ? item.lifecycle?.status || "ready" : "disabled"}`}>{enabled ? item.lifecycle?.status || "ready" : "disabled"}</span>
                </div>
                <p className="extension-description">{item.description}</p>
                <dl className="extension-contract">
                  <div><dt>工具</dt><dd>{item.tools.length}</dd></div>
                  <div><dt>页面</dt><dd>{item.frontend_routes.length}</dd></div>
                  <div><dt>写权限</dt><dd>{item.metadata?.minimum_write_role || "developer"}</dd></div>
                  <div><dt>故障次数</dt><dd>{item.lifecycle?.failure_count || 0}</dd></div>
                </dl>
                {item.lifecycle?.last_error ? <p className="extension-error">{item.lifecycle.last_error}</p> : null}
                <div className="extension-card-actions">
                  {item.source === "installed" ? <button className="btn danger" onClick={() => void uninstall(item)} disabled={busy === item.extension_id}>卸载</button> : null}
                  <button className="btn secondary" onClick={() => void migrate(item)} disabled={!enabled || busy === item.extension_id}>迁移当前工作区</button>
                  <button className={`btn ${enabled ? "danger" : "primary"}`} onClick={() => void changeState(item)} disabled={busy === item.extension_id}>{enabled ? "停用" : "启用"}</button>
                </div>
              </article>
            );
          })}
        </div>
        <section className="extension-repository">
          <div className="extension-section-head"><div><h2>私有扩展仓库</h2><p>仅展示已通过 Ed25519 签名校验的扩展包；安装或升级后需重启服务。</p></div><span>{packages.length} 个版本</span></div>
          {packages.length ? <div className="extension-package-list">{packages.map((item) => {
            const current = items.find((candidate) => candidate.extension_id === item.extension_id);
            const key = `${item.extension_id}@${item.version}`;
            return <div className="extension-package-row" key={key}><div><b>{item.extension_id}</b><small>v{item.version} · 密钥 {item.key_id}</small></div><span>{item.algorithm}</span><button className="btn secondary" disabled={busy === key || current?.version === item.version} onClick={() => void install(item)}>{current ? current.version === item.version ? "已安装" : "升级" : "安装"}</button></div>;
          })}</div> : <div className="extension-repository-empty">仓库中暂无扩展包</div>}
        </section>
      </div>
    </div>
  );
}
