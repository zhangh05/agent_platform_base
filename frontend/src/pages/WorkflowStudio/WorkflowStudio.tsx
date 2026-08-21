import { useCallback, useEffect, useMemo, useState } from "react";
import { workflowsApi, workflowTemplatesApi, type WorkflowDefinition, type WorkflowRun, type WorkflowTemplate } from "../../api";
import { apiRequest } from "../../api/client";
import { useSessionStore } from "../../stores/session";

type Asset = { asset_id: string; name: string; host: string; port: number; vendor?: string; credential_configured?: boolean };

function isInspection(workflow: WorkflowDefinition) {
  return workflow.nodes.some((node) => node.tool_id === "network.operations.inspection");
}
function workflowKind(workflow: WorkflowDefinition) {
  if (isInspection(workflow)) return "批量只读巡检";
  if (workflow.nodes.some((node) => node.tool_id === "network.operations.assets_read")) return "资产清单核对";
  return "自动化流程";
}
function statusText(status?: string) {
  return ({ succeeded: "已完成", failed: "执行失败", cancelled: "已取消", awaiting_approval: "等待审批", running: "执行中", queued: "排队中" } as Record<string, string>)[status || ""] || status || "未运行";
}

export function WorkflowStudio() {
  const workspaceId = useSessionStore((state) => state.currentWorkspaceId);
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [selectedAssetIds, setSelectedAssetIds] = useState<string[]>([]);
  const [lastRun, setLastRun] = useState<WorkflowRun | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [flowData, templateData, assetData] = await Promise.all([
        workflowsApi.list(workspaceId),
        workflowTemplatesApi.list(),
        apiRequest<{ assets: Asset[] }>({ method: "GET", url: "/extensions/network.operations/assets", params: { workspace_id: workspaceId } }),
      ]);
      setWorkflows(flowData.workflows || []);
      setTemplates(templateData.templates || []);
      setAssets(assetData.assets || []);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "页面数据读取失败"); }
  }, [workspaceId]);
  useEffect(() => { void load(); }, [load]);

  const selected = useMemo(() => workflows.find((item) => item.workflow_id === selectedId) || null, [workflows, selectedId]);

  async function create(template: WorkflowTemplate) {
    setBusy(true); setError("");
    try {
      const result = await workflowTemplatesApi.instantiate(workspaceId, template.template_id);
      await load();
      setSelectedId(result.workflow.workflow_id);
      setSelectedAssetIds([]);
      setLastRun(null);
      setShowNew(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "创建任务失败"); }
    finally { setBusy(false); }
  }

  async function run() {
    if (!selected) return;
    if (isInspection(selected) && !selectedAssetIds.length) {
      setError("请至少选择一台设备后再开始巡检。");
      return;
    }
    setBusy(true); setError("");
    try {
      const inputs = isInspection(selected) ? { asset_ids: selectedAssetIds } : {};
      const result = await workflowsApi.run(workspaceId, selected.workflow_id, inputs);
      setLastRun(result.run);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "任务启动失败"); }
    finally { setBusy(false); }
  }

  async function remove(workflow: WorkflowDefinition) {
    if (!window.confirm(`确定删除“${workflow.name}”吗？删除后不能恢复。`)) return;
    if (window.prompt("请输入 删除 继续：") !== "删除") return;
    setBusy(true); setError("");
    try {
      await workflowsApi.remove(workspaceId, workflow.workflow_id);
      setWorkflows((items) => items.filter((item) => item.workflow_id !== workflow.workflow_id));
      if (selectedId === workflow.workflow_id) { setSelectedId(""); setSelectedAssetIds([]); setLastRun(null); }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "删除失败" ); }
    finally { setBusy(false); }
  }

  return <div className="page workflow-studio workflow-task" data-testid="page-workflows">
    <div className="page-body workflow-task-body">
      <div className="workflow-task-toolbar"><h1>流程</h1><button className="btn primary" type="button" onClick={() => setShowNew((value) => !value)}>{showNew ? "关闭" : "新建流程"}</button></div>
      {error ? <div className="workflow-error" role="alert">{error}</div> : null}
      {showNew ? <section className="workflow-new-menu" aria-label="新建流程"><p>选择要完成的工作：</p>{templates.map((template) => <button type="button" key={template.template_id} disabled={busy} onClick={() => void create(template)}><b>{template.name}</b><span>{template.description}</span></button>)}</section> : null}
      <div className="workflow-task-layout">
        <aside className="workflow-task-list"><div>我的流程 <span>{workflows.length}</span></div>{workflows.length ? workflows.map((workflow) => <article className={selectedId === workflow.workflow_id ? "selected" : ""} key={workflow.workflow_id}><button type="button" onClick={() => { setSelectedId(workflow.workflow_id); setSelectedAssetIds([]); setLastRun(null); setError(""); }}><b>{workflow.name}</b><small>{workflowKind(workflow)}</small></button><button className="workflow-delete" type="button" disabled={busy} onClick={() => void remove(workflow)}>删除</button></article>) : <p>还没有流程。</p>}</aside>
        <main className="workflow-task-detail">
          {!selected ? <div className="workflow-task-empty"><h2>选择一个流程</h2><p>点击左侧流程，或点右上角“新建流程”。</p></div> : <>
            <header><div><h2>{selected.name}</h2><p>{workflowKind(selected)}</p></div><button className="btn primary" type="button" disabled={busy} onClick={() => void run()}>{busy ? "正在启动…" : "开始运行"}</button></header>
            {isInspection(selected) ? <section className="workflow-device-picker"><h3>选择要巡检的设备</h3><p>仅执行只读巡检，不会下发配置。</p>{assets.length ? <div>{assets.map((asset) => <label key={asset.asset_id}><input type="checkbox" checked={selectedAssetIds.includes(asset.asset_id)} onChange={(event) => setSelectedAssetIds((ids) => event.target.checked ? [...ids, asset.asset_id] : ids.filter((id) => id !== asset.asset_id))} /><span><b>{asset.name}</b><small>{asset.host}:{asset.port}{asset.vendor ? ` · ${asset.vendor}` : ""}{asset.credential_configured ? "" : " · 未配置凭据"}</small></span></label>)}</div> : <div className="workflow-no-assets">还没有可巡检设备。请先到“网络巡检”添加设备。</div>}</section> : <section className="workflow-task-summary"><h3>将执行</h3><p>{selected.nodes.map((node) => node.name || node.node_id).join("、")}</p><p>此流程不需要填写额外参数。</p></section>}
            {lastRun ? <section className={`workflow-task-result ${lastRun.status}`}><h3>{statusText(lastRun.status)}</h3><p>运行编号：{lastRun.run_id}</p>{lastRun.nodes.map((node) => <span key={node.node_id}>{node.node_id}：{statusText(node.status)}</span>)}</section> : null}
          </>}
        </main>
      </div>
    </div>
  </div>;
}
