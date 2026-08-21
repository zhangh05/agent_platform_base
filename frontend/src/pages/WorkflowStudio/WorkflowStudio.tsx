import { useCallback, useEffect, useMemo, useState } from "react";
import { extensionsApi, toolsApi, workflowsApi, workflowTemplatesApi, type InstalledExtension, type WorkflowDefinition, type WorkflowRun, type WorkflowTemplate } from "../../api";
import type { ToolCatalogItem } from "../../types";
import { useSessionStore } from "../../stores/session";

type DraftNode = { node_id: string; name: string; tool_id: string; argumentsText: string; dependsText: string };

/** User-facing workflow studio: templates first, raw DAG editing second. */
export function WorkflowStudio() {
  const workspaceId = useSessionStore((state) => state.currentWorkspaceId);
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [tools, setTools] = useState<ToolCatalogItem[]>([]);
  const [apps, setApps] = useState<InstalledExtension[]>([]);
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState(""); const [description, setDescription] = useState("");
  const [nodes, setNodes] = useState<DraftNode[]>([]); const [inputText, setInputText] = useState("{}");
  const [lastRun, setLastRun] = useState<WorkflowRun | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [flowData, toolData, extensionData, templateData] = await Promise.all([workflowsApi.list(workspaceId), toolsApi.catalog(), extensionsApi.list(), workflowTemplatesApi.list()]);
    setWorkflows(flowData.workflows || []); setTools((toolData.tools || []).filter((item) => item.enabled)); setApps(extensionData.extensions || []); setTemplates(templateData.templates || []);
  }, [workspaceId]);
  useEffect(() => { load().catch(() => setError("应用编排数据读取失败")); }, [load]);

  function edit(workflow: WorkflowDefinition) {
    setSelected(workflow.workflow_id); setName(workflow.name); setDescription(workflow.description || "");
    setNodes(workflow.nodes.map((node) => ({ node_id: node.node_id, name: node.name, tool_id: node.tool_id, argumentsText: JSON.stringify(node.arguments || {}, null, 2), dependsText: (node.depends_on || []).join(", ") })));
    setLastRun(null); setError("");
  }
  function createNew() {
    const tool = tools[0]?.tool_id || ""; setSelected(""); setName("新流程"); setDescription("");
    setNodes([{ node_id: "step_1", name: "第一步", tool_id: tool, argumentsText: "{}", dependsText: "" }]); setInputText("{}"); setLastRun(null); setError("");
  }
  async function createFromTemplate(template: WorkflowTemplate) {
    if (!workspaceId) return;
    setBusy(true); setError("");
    try {
      const result = await workflowTemplatesApi.instantiate(workspaceId, template.template_id);
      await load(); edit(result.workflow); setInputText(JSON.stringify(template.input_example || {}, null, 2));
    } catch (err) { setError(String((err as { message?: string })?.message || "模板创建失败，请确认所需扩展已启用")); }
    finally { setBusy(false); }
  }
  function addNode() {
    const index = nodes.length + 1; setNodes([...nodes, { node_id: `step_${index}`, name: `第 ${index} 步`, tool_id: tools[0]?.tool_id || "", argumentsText: "{}", dependsText: nodes.length ? nodes[nodes.length - 1].node_id : "" }]);
  }
  async function save() {
    setBusy(true); setError("");
    try {
      const payload = { workflow_id: selected || `workflow_${Date.now()}`, name, description, version: 1, status: "active", failure_policy: "fail_fast" as const, nodes: nodes.map((node) => ({ node_id: node.node_id.trim(), name: node.name.trim(), tool_id: node.tool_id, arguments: JSON.parse(node.argumentsText || "{}"), depends_on: node.dependsText.split(",").map((item) => item.trim()).filter(Boolean) })) };
      const result = selected ? await workflowsApi.update(workspaceId, payload as WorkflowDefinition) : await workflowsApi.save(workspaceId, payload);
      await load(); edit(result.workflow);
    } catch (err) { setError(String((err as { message?: string })?.message || "流程保存失败，请检查步骤名称、依赖和输入参数")); }
    finally { setBusy(false); }
  }
  async function run() {
    if (!selected) { setError("请先保存流程再运行"); return; }
    setBusy(true); setError("");
    try { const result = await workflowsApi.run(workspaceId, selected, JSON.parse(inputText || "{}")); setLastRun(result.run); }
    catch (err) { setError(String((err as { message?: string })?.message || "流程运行失败；失败详情已自动进入人工复核收件箱")); }
    finally { setBusy(false); }
  }
  const activeAppCount = useMemo(() => apps.filter((item) => item.lifecycle?.enabled !== false).length, [apps]);

  return <div className="page workflow-studio" data-testid="page-workflows">
    <header className="page-header ui-page-header"><div><h1>流程自动化 <span>Workflow Studio</span></h1><p className="subtitle">从业务模板开始，把已启用的工具连接成可重复、可追踪的运维流程。</p></div><button className="btn primary" onClick={createNew}>专家模式新建</button></header>
    <div className="page-body">
      {error ? <div className="extension-center-error" role="alert">{error}</div> : null}
      <section className="workflow-summary"><div><b>{activeAppCount}</b><span>可用应用</span></div><div><b>{tools.length}</b><span>可编排工具</span></div><div><b>{workflows.length}</b><span>当前流程</span></div><p>{apps.slice(0, 5).map((item) => item.name).join(" · ") || "当前仅使用平台内置能力"}</p></section>
      <section className="workflow-templates" aria-label="业务流程模板"><div className="workflow-panel-title"><div><h2>从模板开始</h2><p className="text-sm muted">模板会创建工作区内独立流程；创建后可在下方试运行或按需进入专家编辑。</p></div></div><div className="workflow-template-grid">{templates.map((template) => <article key={template.template_id} className="workflow-template-card"><h3>{template.name}</h3><p>{template.description}</p><small><strong>执行结果：</strong>{template.expected_result}</small><button className="btn primary" type="button" onClick={() => void createFromTemplate(template)} disabled={busy} data-testid={`create-template-${template.template_id}`}>{busy ? "创建中…" : "创建并打开"}</button></article>)}</div></section>
      <div className="workflow-layout">
        <aside className="workflow-list"><div className="workflow-panel-title"><h2>我的流程</h2><span>{workspaceId}</span></div>{workflows.length ? workflows.map((item) => <button className={selected === item.workflow_id ? "selected" : ""} key={item.workflow_id} onClick={() => edit(item)}><b>{item.name}</b><small>{item.nodes.length} 个步骤 · v{item.version}</small></button>) : <div className="workflow-empty">尚未创建流程。选择上方模板即可开始。</div>}</aside>
        <main className="workflow-editor">
          {nodes.length ? <><div className="workflow-editor-head"><div><input className="input workflow-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="流程名称" /><input className="input" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="说明这个流程解决什么问题" /></div><div><button className="btn secondary" onClick={addNode} disabled={!name}>添加步骤</button><button className="btn primary" onClick={() => void save()} disabled={busy || !name || !nodes.length}>{busy ? "处理中…" : "保存流程"}</button></div></div><div className="workflow-node-list">{nodes.map((node, index) => <article className="workflow-node" key={`${node.node_id}-${index}`}><span className="workflow-step-number">{index + 1}</span><div className="workflow-node-fields"><div className="workflow-node-row"><input className="input" value={node.name} onChange={(event) => setNodes(nodes.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} placeholder="步骤名称" /><input className="input" value={node.node_id} onChange={(event) => setNodes(nodes.map((item, i) => i === index ? { ...item, node_id: event.target.value } : item))} placeholder="步骤标识" /><select className="input" value={node.tool_id} onChange={(event) => setNodes(nodes.map((item, i) => i === index ? { ...item, tool_id: event.target.value } : item))}>{tools.map((tool) => <option key={tool.tool_id} value={tool.tool_id}>{tool.display_name || tool.tool_id}</option>)}</select></div><label>前置步骤<input className="input" value={node.dependsText} onChange={(event) => setNodes(nodes.map((item, i) => i === index ? { ...item, dependsText: event.target.value } : item))} placeholder="多个标识用逗号分隔" /></label><label>输入参数（支持 ${"${input.字段}"} 和 ${"${nodes.步骤.output.字段}"}）<textarea className="input" rows={4} value={node.argumentsText} onChange={(event) => setNodes(nodes.map((item, i) => i === index ? { ...item, argumentsText: event.target.value } : item))} /></label></div><button className="workflow-node-remove" onClick={() => setNodes(nodes.filter((_, i) => i !== index))} aria-label={`删除${node.name}`}>×</button></article>)}</div></> : <div className="workflow-empty">选择模板或进入专家模式后，可在这里查看并编辑步骤。</div>}
          {selected ? <section className="workflow-run-box"><div><h3>试运行</h3><p>填写本次执行所需参数后运行。只读步骤可并行；高风险步骤仍会进入统一审批，不会绕过安全控制。</p></div><textarea className="input" rows={4} value={inputText} onChange={(event) => setInputText(event.target.value)} aria-label="流程运行输入" /><button className="btn primary" onClick={() => void run()} disabled={busy}>运行流程</button>{lastRun ? <div className={`workflow-run-result ${lastRun.status}`}><b>{lastRun.status === "succeeded" ? "运行成功" : "运行未完成"}</b>{lastRun.nodes.map((node) => <span key={node.node_id}>{node.node_id} · {node.status}{node.orchestration?.layer ? ` · 第 ${node.orchestration.layer} 组` : ""}{node.orchestration?.parallel ? " · 并行" : ""}</span>)}</div> : null}</section> : null}
        </main>
      </div>
    </div>
  </div>;
}
