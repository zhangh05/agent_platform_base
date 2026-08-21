import { useCallback, useEffect, useMemo, useState } from "react";
import { extensionsApi, toolsApi, workflowsApi, workflowTemplatesApi, type InstalledExtension, type WorkflowDefinition, type WorkflowRun, type WorkflowTemplate } from "../../api";
import { useSessionStore } from "../../stores/session";

type DraftNode = {
  node_id: string;
  name: string;
  tool_id: string;
  dependsText: string;
  argumentsText: string;
};

const runStatusText: Record<string, string> = {
  succeeded: "已完成",
  failed: "执行失败",
  awaiting_approval: "等待审批",
  cancelled: "已取消",
  running: "执行中",
  queued: "排队中",
};

function toDraftNodes(workflow: WorkflowDefinition): DraftNode[] {
  return (workflow.nodes || []).map((node) => ({
    node_id: node.node_id,
    name: node.name || node.node_id,
    tool_id: node.tool_id,
    dependsText: (node.depends_on || []).join(", "),
    argumentsText: JSON.stringify(node.arguments || {}, null, 2),
  }));
}

function statusText(status?: string) { return runStatusText[status || ""] || status || "未运行"; }

export function WorkflowStudio() {
  const workspaceId = useSessionStore((state) => state.currentWorkspaceId);
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [tools, setTools] = useState<{ tool_id: string; display_name?: string }[]>([]);
  const [apps, setApps] = useState<InstalledExtension[]>([]);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [nodes, setNodes] = useState<DraftNode[]>([]);
  const [inputText, setInputText] = useState("{}");
  const [lastRun, setLastRun] = useState<WorkflowRun | null>(null);
  const [showEditor, setShowEditor] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [flowData, toolData, extensionData, templateData] = await Promise.all([
        workflowsApi.list(workspaceId),
        toolsApi.catalog(),
        extensionsApi.list(),
        workflowTemplatesApi.list(),
      ]);
      setWorkflows(flowData.workflows || []);
      setTools((toolData.tools || []).map((tool) => ({ tool_id: tool.tool_id, display_name: tool.display_name })));
      setApps((extensionData.extensions || []).filter((item) => item.lifecycle?.enabled !== false));
      setTemplates(templateData.templates || []);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "流程数据读取失败");
    }
  }, [workspaceId]);

  useEffect(() => { void load(); }, [load]);

  const selectedWorkflow = useMemo(
    () => workflows.find((item) => item.workflow_id === selected) || null,
    [workflows, selected],
  );

  function openWorkflow(workflow: WorkflowDefinition, editing = false) {
    setSelected(workflow.workflow_id);
    setName(workflow.name || "");
    setDescription(workflow.description || "");
    setNodes(toDraftNodes(workflow));
    setInputText("{}");
    setLastRun(null);
    setShowEditor(editing);
    setError("");
  }

  function createExpertWorkflow() {
    const toolId = tools[0]?.tool_id || "";
    const workflowId = `custom-${Date.now()}`;
    setSelected("");
    setName("未命名流程");
    setDescription("说明此流程解决的运维任务和预期结果。");
    setNodes(toolId ? [{ node_id: "step_1", name: "执行步骤", tool_id: toolId, dependsText: "", argumentsText: "{}" }] : []);
    setInputText("{}");
    setLastRun(null);
    setShowEditor(true);
    setError(toolId ? "" : "当前没有可编排工具，请先启用业务扩展。");
    // Keep an explicit id only after the user saves; this avoids writing draft records.
    void workflowId;
  }

  async function createFromTemplate(template: WorkflowTemplate) {
    setBusy(true); setError("");
    try {
      const result = await workflowTemplatesApi.instantiate(workspaceId, template.template_id);
      await load();
      openWorkflow(result.workflow, false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "模板创建失败");
    } finally { setBusy(false); }
  }

  function addNode() {
    const toolId = tools[0]?.tool_id || "";
    if (!toolId) { setError("当前没有可编排工具，请先启用业务扩展。"); return; }
    setNodes((current) => [...current, {
      node_id: `step_${current.length + 1}`,
      name: "新增步骤",
      tool_id: toolId,
      dependsText: "",
      argumentsText: "{}",
    }]);
  }

  async function save() {
    if (!name.trim() || !nodes.length) return;
    setBusy(true); setError("");
    try {
      const definition = {
        workflow_id: selected || `workflow-${Date.now()}`,
        name: name.trim(),
        description: description.trim(),
        nodes: nodes.map((node) => ({
          node_id: node.node_id.trim(),
          name: node.name.trim(),
          tool_id: node.tool_id,
          depends_on: node.dependsText.split(",").map((item) => item.trim()).filter(Boolean),
          arguments: JSON.parse(node.argumentsText || "{}"),
        })),
      } as WorkflowDefinition;
      const result = selected ? await workflowsApi.update(workspaceId, definition) : await workflowsApi.save(workspaceId, definition);
      await load();
      openWorkflow(result.workflow, true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败：请检查步骤标识、依赖关系和 JSON 参数。");
    } finally { setBusy(false); }
  }

  async function run() {
    if (!selected) return;
    setBusy(true); setError("");
    try {
      const result = await workflowsApi.run(workspaceId, selected, JSON.parse(inputText || "{}"));
      setLastRun(result.run);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "运行失败：请检查本次输入。");
    } finally { setBusy(false); }
  }

  async function archive(workflow: WorkflowDefinition) {
    if (!window.confirm(`归档“${workflow.name}”吗？归档后不能再运行或编辑，但历史运行记录会保留。`)) return;
    setBusy(true); setError("");
    try {
      await workflowsApi.archive(workspaceId, workflow.workflow_id);
      const remaining = workflows.filter((item) => item.workflow_id !== workflow.workflow_id);
      setWorkflows(remaining);
      if (selected === workflow.workflow_id) {
        const next = remaining[0];
        if (next) openWorkflow(next, false);
        else { setSelected(""); setName(""); setDescription(""); setNodes([]); setLastRun(null); setShowEditor(false); }
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "归档失败");
    } finally { setBusy(false); }
  }

  const activeAppCount = apps.length;

  return <div className="page workflow-studio workflow-refresh" data-testid="page-workflows">
    <header className="page-header ui-page-header workflow-hero">
      <div>
        <p className="workflow-kicker">运维自动化</p>
        <h1>把重复操作变成可追踪的流程</h1>
        <p className="subtitle">先从业务模板开始，再按需要调整步骤。每次运行都会保留状态和结果。</p>
      </div>
      <button className="btn secondary" type="button" onClick={createExpertWorkflow}>高级编辑</button>
    </header>
    <div className="page-body workflow-refresh-body">
      {error ? <div className="workflow-notice" role="alert">{error}</div> : null}
      <section className="workflow-quick-stats" aria-label="当前工作区概览">
        <div><strong>{workflows.length}</strong><span>我的流程</span></div>
        <div><strong>{templates.length}</strong><span>可用模板</span></div>
        <div><strong>{activeAppCount}</strong><span>已启用业务能力</span></div>
        <p>工作区：<b>{workspaceId}</b>。归档不会删除历史运行记录。</p>
      </section>

      <section className="workflow-template-section" aria-label="推荐模板">
        <div className="workflow-section-heading"><div><p className="workflow-kicker">推荐开始方式</p><h2>选择一个运维任务</h2><p>模板会创建一份独立流程；创建后可以立即运行，也可以再进入高级编辑。</p></div></div>
        <div className="workflow-template-grid workflow-template-grid-refresh">
          {templates.map((template) => <article key={template.template_id} className="workflow-template-card workflow-template-card-refresh">
            <div className="workflow-template-tag">{template.audience || "业务流程"}</div>
            <h3>{template.name}</h3><p>{template.description}</p>
            <div className="workflow-template-result"><b>完成后会得到</b><span>{template.expected_result}</span></div>
            <button className="btn primary" type="button" onClick={() => void createFromTemplate(template)} disabled={busy} data-testid={`create-template-${template.template_id}`}>{busy ? "正在创建…" : "使用此模板"}</button>
          </article>)}
          {!templates.length ? <div className="workflow-empty">当前没有可用模板。请先启用提供业务流程模板的扩展。</div> : null}
        </div>
      </section>

      <section className="workflow-workbench" aria-label="流程管理工作台">
        <aside className="workflow-library">
          <div className="workflow-library-head"><div><h2>我的流程</h2><p>选择一条流程查看、运行或归档。</p></div><span>{workflows.length}</span></div>
          <div className="workflow-list workflow-list-refresh">
            {workflows.map((item) => <article className={`workflow-list-item ${selected === item.workflow_id ? "selected" : ""}`} key={item.workflow_id}>
              <button type="button" onClick={() => openWorkflow(item, false)}>
                <span className="workflow-list-status">可运行</span><b>{item.name}</b><small>{item.nodes.length} 个步骤 · 最近更新 {item.updated_at ? new Date(item.updated_at).toLocaleDateString() : "未记录"}</small>
              </button>
              <button className="workflow-archive-button" type="button" onClick={() => void archive(item)} disabled={busy} aria-label={`归档${item.name}`}>归档</button>
            </article>)}
            {!workflows.length ? <div className="workflow-empty">还没有流程。选择上方模板即可创建。</div> : null}
          </div>
        </aside>

        <main className="workflow-detail">
          {!selectedWorkflow && !nodes.length ? <div className="workflow-welcome"><p className="workflow-kicker">下一步</p><h2>先选择一个模板或已有流程</h2><p>日常运维只需要使用模板和运行入口。高级编辑仅在你要调整步骤时使用。</p></div> : null}
          {(selectedWorkflow || nodes.length) ? <>
            <div className="workflow-detail-head"><div><p className="workflow-kicker">{showEditor ? "高级编辑" : "流程概览"}</p><h2>{name || selectedWorkflow?.name || "未命名流程"}</h2><p>{description || "尚未填写说明"}</p></div><div className="workflow-detail-actions">{selected ? <button className="btn secondary" type="button" onClick={() => setShowEditor((value) => !value)}>{showEditor ? "返回概览" : "高级编辑"}</button> : null}{showEditor ? <button className="btn primary" type="button" onClick={() => void save()} disabled={busy || !name.trim() || !nodes.length}>{busy ? "正在保存…" : "保存流程"}</button> : <button className="btn primary" type="button" onClick={() => void run()} disabled={busy || !selected}>{busy ? "正在启动…" : "运行此流程"}</button>}</div></div>

            {!showEditor ? <>
              <section className="workflow-steps-summary"><h3>执行步骤</h3><ol>{nodes.map((node, index) => <li key={`${node.node_id}-${index}`}><span>{index + 1}</span><div><b>{node.name || node.node_id}</b><small>{node.dependsText ? `在 ${node.dependsText} 完成后执行` : "流程开始后执行"}</small></div></li>)}</ol></section>
              <section className="workflow-run-panel"><div><h3>本次运行</h3><p>只有流程需要参数时才填写。普通流程可保持默认的空对象。</p></div><textarea className="input" rows={3} value={inputText} onChange={(event) => setInputText(event.target.value)} aria-label="流程运行输入" placeholder='例如：{"asset_ids":["设备ID"]}' /><button className="btn primary" type="button" onClick={() => void run()} disabled={busy || !selected}>{busy ? "正在启动…" : "运行此流程"}</button>{lastRun ? <div className={`workflow-run-result ${lastRun.status}`}><b>{statusText(lastRun.status)}</b><span>运行 ID：{lastRun.run_id}</span>{lastRun.nodes.map((node) => <span key={node.node_id}>{node.node_id} · {statusText(node.status)}</span>)}</div> : null}</section>
            </> : <section className="workflow-editor workflow-editor-refresh">
              <div className="workflow-editor-intro"><div><h3>编辑流程定义</h3><p>这里用于调整步骤、依赖关系和工具参数。请只在确有编排需求时修改。</p></div><button className="btn secondary" type="button" onClick={addNode}>添加步骤</button></div>
              <div className="workflow-basic-fields"><label>流程名称<input className="input" value={name} onChange={(event) => setName(event.target.value)} /></label><label>流程说明<input className="input" value={description} onChange={(event) => setDescription(event.target.value)} /></label></div>
              <div className="workflow-node-list">{nodes.map((node, index) => <article className="workflow-node" key={`${node.node_id}-${index}`}><span className="workflow-step-number">{index + 1}</span><div className="workflow-node-fields"><div className="workflow-node-row"><input className="input" value={node.name} onChange={(event) => setNodes(nodes.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} placeholder="步骤名称" /><input className="input" value={node.node_id} onChange={(event) => setNodes(nodes.map((item, i) => i === index ? { ...item, node_id: event.target.value } : item))} placeholder="步骤标识" /><select className="input" value={node.tool_id} onChange={(event) => setNodes(nodes.map((item, i) => i === index ? { ...item, tool_id: event.target.value } : item))}>{tools.map((tool) => <option key={tool.tool_id} value={tool.tool_id}>{tool.display_name || tool.tool_id}</option>)}</select></div><label>前置步骤<input className="input" value={node.dependsText} onChange={(event) => setNodes(nodes.map((item, i) => i === index ? { ...item, dependsText: event.target.value } : item))} placeholder="多个步骤标识用逗号分隔" /></label><label>输入参数（JSON）<textarea className="input" rows={4} value={node.argumentsText} onChange={(event) => setNodes(nodes.map((item, i) => i === index ? { ...item, argumentsText: event.target.value } : item))} /></label></div><button className="workflow-node-remove" type="button" onClick={() => setNodes(nodes.filter((_, i) => i !== index))} aria-label={`删除${node.name}`}>删除步骤</button></article>)}</div>
            </section>}
          </> : null}
        </main>
      </section>
    </div>
  </div>;
}
