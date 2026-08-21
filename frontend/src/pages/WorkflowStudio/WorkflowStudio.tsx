import { useCallback, useEffect, useState } from "react";
import { extensionsApi, toolsApi, workflowsApi, workflowTemplatesApi, type WorkflowDefinition, type WorkflowRun, type WorkflowTemplate } from "../../api";
import { useSessionStore } from "../../stores/session";

type DraftNode = { node_id: string; name: string; tool_id: string; dependsText: string; argumentsText: string };

function toDraftNodes(workflow: WorkflowDefinition): DraftNode[] {
  return (workflow.nodes || []).map((node) => ({
    node_id: node.node_id,
    name: node.name || node.node_id,
    tool_id: node.tool_id,
    dependsText: (node.depends_on || []).join(", "),
    argumentsText: JSON.stringify(node.arguments || {}, null, 2),
  }));
}

function runStatus(status?: string) {
  return ({ succeeded: "已完成", failed: "失败", cancelled: "已取消", awaiting_approval: "等待审批", running: "执行中", queued: "排队中" } as Record<string, string>)[status || ""] || status || "未运行";
}

export function WorkflowStudio() {
  const workspaceId = useSessionStore((state) => state.currentWorkspaceId);
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [tools, setTools] = useState<{ tool_id: string; display_name?: string }[]>([]);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [nodes, setNodes] = useState<DraftNode[]>([]);
  const [inputText, setInputText] = useState("{}");
  const [lastRun, setLastRun] = useState<WorkflowRun | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [flowData, toolData, templateData] = await Promise.all([
        workflowsApi.list(workspaceId), toolsApi.catalog(), workflowTemplatesApi.list(), extensionsApi.list(),
      ]);
      setWorkflows(flowData.workflows || []);
      setTools((toolData.tools || []).map((tool) => ({ tool_id: tool.tool_id, display_name: tool.display_name })));
      setTemplates(templateData.templates || []);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取流程"); }
  }, [workspaceId]);

  useEffect(() => { void load(); }, [load]);

  function open(workflow: WorkflowDefinition, edit = false) {
    setSelected(workflow.workflow_id); setName(workflow.name || ""); setDescription(workflow.description || "");
    setNodes(toDraftNodes(workflow)); setInputText("{}"); setLastRun(null); setEditing(edit); setShowCreate(false); setError("");
  }

  function createBlank() {
    const toolId = tools[0]?.tool_id || "";
    setSelected(""); setName("未命名流程"); setDescription("");
    setNodes(toolId ? [{ node_id: "step_1", name: "执行步骤", tool_id: toolId, dependsText: "", argumentsText: "{}" }] : []);
    setEditing(true); setShowCreate(false); setLastRun(null);
    if (!toolId) setError("没有可用工具，无法创建空白流程。");
  }

  async function createFromTemplate(template: WorkflowTemplate) {
    setBusy(true); setError("");
    try { const result = await workflowTemplatesApi.instantiate(workspaceId, template.template_id); await load(); open(result.workflow, false); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "创建流程失败"); }
    finally { setBusy(false); }
  }

  function addNode() {
    const toolId = tools[0]?.tool_id;
    if (!toolId) { setError("没有可用工具，无法添加步骤。"); return; }
    setNodes((current) => [...current, { node_id: `step_${current.length + 1}`, name: "新增步骤", tool_id: toolId, dependsText: "", argumentsText: "{}" }]);
  }

  async function save() {
    if (!name.trim() || !nodes.length) return;
    setBusy(true); setError("");
    try {
      const definition = {
        workflow_id: selected || `workflow-${Date.now()}`,
        name: name.trim(), description: description.trim(),
        nodes: nodes.map((node) => ({ node_id: node.node_id.trim(), name: node.name.trim(), tool_id: node.tool_id, depends_on: node.dependsText.split(",").map((item) => item.trim()).filter(Boolean), arguments: JSON.parse(node.argumentsText || "{}") })),
      } as WorkflowDefinition;
      const result = selected ? await workflowsApi.update(workspaceId, definition) : await workflowsApi.save(workspaceId, definition);
      await load(); open(result.workflow, false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败，请检查步骤标识、依赖和参数格式。"); }
    finally { setBusy(false); }
  }

  async function run() {
    if (!selected) return;
    setBusy(true); setError("");
    try { const result = await workflowsApi.run(workspaceId, selected, JSON.parse(inputText || "{}")); setLastRun(result.run); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "运行失败，请检查输入参数。"); }
    finally { setBusy(false); }
  }

  async function remove(workflow: WorkflowDefinition) {
    if (!window.confirm(`确定永久删除“${workflow.name}”吗？该流程及其历史运行记录都将被删除，无法恢复。`)) return;
    if (window.prompt("这是永久删除。请输入 删除 继续：") !== "删除") return;
    setBusy(true); setError("");
    try {
      await workflowsApi.remove(workspaceId, workflow.workflow_id);
      const remaining = workflows.filter((item) => item.workflow_id !== workflow.workflow_id);
      setWorkflows(remaining);
      if (selected === workflow.workflow_id) { setSelected(""); setName(""); setDescription(""); setNodes([]); setLastRun(null); setEditing(false); }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "删除失败"); }
    finally { setBusy(false); }
  }

  return <div className="page workflow-studio workflow-plain" data-testid="page-workflows">
    <div className="page-body workflow-plain-body">
      <div className="workflow-toolbar"><div><h1>流程</h1><span>{workflows.length} 个流程 · {workspaceId}</span></div><button className="btn primary" type="button" onClick={() => setShowCreate((value) => !value)}>{showCreate ? "关闭" : "新建流程"}</button></div>
      {error ? <div className="workflow-error" role="alert">{error}</div> : null}
      {showCreate ? <section className="workflow-create" aria-label="新建流程"><div className="workflow-create-title"><b>从模板新建</b><span>选择一个任务即可创建；也可以新建空白流程。</span></div><div className="workflow-create-options">{templates.map((template) => <button type="button" key={template.template_id} disabled={busy} onClick={() => void createFromTemplate(template)}><b>{template.name}</b><span>{template.description}</span></button>)}<button type="button" className="workflow-create-blank" onClick={createBlank}><b>空白流程</b><span>自行添加步骤和工具。</span></button></div></section> : null}
      <div className="workflow-main">
        <aside className="workflow-sidebar"><div className="workflow-sidebar-title">我的流程 <span>{workflows.length}</span></div>{workflows.length ? <div className="workflow-list-plain">{workflows.map((workflow) => <div className={`workflow-row ${selected === workflow.workflow_id ? "selected" : ""}`} key={workflow.workflow_id}><button type="button" onClick={() => open(workflow)}><b>{workflow.name}</b><small>{workflow.nodes.length} 个步骤</small></button><button className="workflow-delete" type="button" disabled={busy} onClick={() => void remove(workflow)} aria-label={`删除${workflow.name}`}>删除</button></div>)}</div> : <p className="workflow-empty">暂无流程</p>}</aside>
        <main className="workflow-content">
          {!nodes.length ? <div className="workflow-empty-state"><h2>选择或新建一个流程</h2><p>点击左侧已有流程，或使用右上角“新建流程”。</p></div> : <>
            <div className="workflow-content-head"><div><h2>{name}</h2><p>{description || "未填写说明"}</p></div><div>{editing ? <><button className="btn secondary" type="button" onClick={() => setEditing(false)}>取消</button><button className="btn primary" type="button" onClick={() => void save()} disabled={busy}>保存</button></> : <><button className="btn secondary" type="button" onClick={() => setEditing(true)}>编辑</button><button className="btn primary" type="button" onClick={() => void run()} disabled={busy || !selected}>运行</button></>}</div></div>
            {!editing ? <><ol className="workflow-steps-plain">{nodes.map((node, index) => <li key={`${node.node_id}-${index}`}><span>{index + 1}</span><div><b>{node.name}</b><small>{node.dependsText ? `依赖：${node.dependsText}` : "无前置步骤"}</small></div></li>)}</ol>{selected ? <section className="workflow-run-plain"><label>运行输入（可选）<textarea className="input" rows={3} value={inputText} onChange={(event) => setInputText(event.target.value)} aria-label="流程运行输入" /></label><button className="btn primary" type="button" onClick={() => void run()} disabled={busy}>运行</button>{lastRun ? <p className={`workflow-run-status ${lastRun.status}`}>{runStatus(lastRun.status)} · {lastRun.run_id}</p> : null}</section> : null}</> : <section className="workflow-edit-plain"><div className="workflow-fields"><label>名称<input className="input" value={name} onChange={(event) => setName(event.target.value)} /></label><label>说明<input className="input" value={description} onChange={(event) => setDescription(event.target.value)} /></label></div><div className="workflow-edit-title"><b>步骤</b><button className="btn secondary" type="button" onClick={addNode}>添加步骤</button></div>{nodes.map((node, index) => <article className="workflow-node-plain" key={`${node.node_id}-${index}`}><span>{index + 1}</span><div><input className="input" value={node.name} onChange={(event) => setNodes(nodes.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} placeholder="步骤名称" /><select className="input" value={node.tool_id} onChange={(event) => setNodes(nodes.map((item, i) => i === index ? { ...item, tool_id: event.target.value } : item))}>{tools.map((tool) => <option key={tool.tool_id} value={tool.tool_id}>{tool.display_name || tool.tool_id}</option>)}</select><input className="input" value={node.dependsText} onChange={(event) => setNodes(nodes.map((item, i) => i === index ? { ...item, dependsText: event.target.value } : item))} placeholder="前置步骤（可选）" /><textarea className="input" rows={3} value={node.argumentsText} onChange={(event) => setNodes(nodes.map((item, i) => i === index ? { ...item, argumentsText: event.target.value } : item))} aria-label={`${node.name}参数`} /></div><button className="workflow-delete" type="button" onClick={() => setNodes(nodes.filter((_, i) => i !== index))}>删除步骤</button></article>)}</section>}
          </>}
        </main>
      </div>
    </div>
  </div>;
}
