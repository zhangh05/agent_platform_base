import { useCallback, useEffect, useState } from "react";
import { extensionsApi, type InstalledExtension } from "../../api";
import { useSessionStore } from "../../stores/session";

export function ExtensionCenter() {
  const workspaceId = useSessionStore((state) => state.currentWorkspaceId);
  const [items, setItems] = useState<InstalledExtension[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const result = await extensionsApi.list();
    setItems(result.extensions || []);
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

  return (
    <div className="page">
      <header className="page-header ui-page-header">
        <div><h1>扩展管理 <span>Extension Center</span></h1><p className="subtitle">统一查看扩展版本、权限、运行状态和工作区迁移。</p></div>
        <button className="btn secondary" onClick={() => void load()}>刷新</button>
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
                  <button className="btn secondary" onClick={() => void migrate(item)} disabled={!enabled || busy === item.extension_id}>迁移当前工作区</button>
                  <button className={`btn ${enabled ? "danger" : "primary"}`} onClick={() => void changeState(item)} disabled={busy === item.extension_id}>{enabled ? "停用" : "启用"}</button>
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </div>
  );
}
