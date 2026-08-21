import { useCallback, useEffect, useMemo, useState } from "react";
import { workflowsApi, workflowTemplatesApi, type WorkflowDefinition, type WorkflowRun, type WorkflowTemplate } from "../../api";
import { apiRequest } from "../../api/client";
import { useSessionStore } from "../../stores/session";

type InputValue = string | string[];
type InputOption = { value: string; label: string; detail: string };

function workflowKind(workflow: WorkflowDefinition, templates: WorkflowTemplate[]) {
  return templates.find((template) => template.template_id === workflow.template_id)?.name || "自动化流程";
}
function statusText(status?: string) {
  return ({ succeeded: "已完成", failed: "执行失败", cancelled: "已取消", awaiting_approval: "等待审批", running: "执行中", queued: "排队中" } as Record<string, string>)[status || ""] || status || "未运行";
}

export function WorkflowStudio() {
  const workspaceId = useSessionStore((state) => state.currentWorkspaceId);
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [inputValues, setInputValues] = useState<Record<string, InputValue>>({});
  const [inputOptions, setInputOptions] = useState<Record<string, InputOption[]>>({});
  const [lastRun, setLastRun] = useState<WorkflowRun | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [flowData, templateData] = await Promise.all([
        workflowsApi.list(workspaceId),
        workflowTemplatesApi.list(),
      ]);
      setWorkflows(flowData.workflows || []);
      setTemplates(templateData.templates || []);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "页面数据读取失败"); }
  }, [workspaceId]);
  useEffect(() => { void load(); }, [load]);

  const selected = useMemo(() => workflows.find((item) => item.workflow_id === selectedId) || null, [workflows, selectedId]);
  const selectedTemplate = useMemo(
    () => templates.find((template) => template.template_id === selected?.template_id) || null,
    [selected, templates],
  );

  useEffect(() => {
    let active = true;
    const fields = selectedTemplate?.input_fields || [];
    setInputOptions({});
    const sourcedFields = fields.filter((field) => field.source);
    if (!sourcedFields.length) return () => { active = false; };
    void Promise.all(sourcedFields.map(async (field) => {
      const source = field.source!;
      const response = await apiRequest<Record<string, unknown>>({
        method: "GET",
        url: source.url.replace(/^\/api/, ""),
        params: { workspace_id: workspaceId },
      });
      const records = Array.isArray(response[source.collection]) ? response[source.collection] as Array<Record<string, unknown>> : [];
      const options = records.map((record) => ({
        value: String(record[source.value_field] ?? ""),
        label: String(record[source.label_field] ?? record[source.value_field] ?? ""),
        detail: (source.detail_fields || []).map((key) => record[key]).filter((value) => value !== undefined && value !== "").map(String).join(" · "),
      })).filter((option) => option.value);
      return [field.name, options] as const;
    })).then((entries) => {
      if (active) setInputOptions(Object.fromEntries(entries));
    }).catch((reason) => {
      if (active) setError(reason instanceof Error ? reason.message : "流程运行条件读取失败");
    });
    return () => { active = false; };
  }, [selectedTemplate, workspaceId]);

  async function create(template: WorkflowTemplate) {
    setBusy(true); setError("");
    try {
      const result = await workflowTemplatesApi.instantiate(workspaceId, template.template_id);
      await load();
      setSelectedId(result.workflow.workflow_id);
      setInputValues({});
      setLastRun(null);
      setShowNew(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "创建任务失败"); }
    finally { setBusy(false); }
  }

  async function run() {
    if (!selected) return;
    const missing = (selectedTemplate?.input_fields || []).find((field) => {
      if (!field.required) return false;
      const value = inputValues[field.name];
      return Array.isArray(value) ? value.length === 0 : !String(value || "").trim();
    });
    if (missing) {
      setError(`请填写${missing.label}。`);
      return;
    }
    setBusy(true); setError("");
    try {
      const result = await workflowsApi.run(workspaceId, selected.workflow_id, inputValues);
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
      if (selectedId === workflow.workflow_id) { setSelectedId(""); setInputValues({}); setLastRun(null); }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "删除失败" ); }
    finally { setBusy(false); }
  }

  return <div className="page workflow-studio workflow-task" data-testid="page-workflows">
    <div className="page-body workflow-task-body">
      <div className="workflow-task-toolbar"><h1>流程</h1><button className="btn primary" type="button" onClick={() => setShowNew((value) => !value)}>{showNew ? "关闭" : "新建流程"}</button></div>
      {error ? <div className="workflow-error" role="alert">{error}</div> : null}
      {showNew ? <section className="workflow-new-menu" aria-label="新建流程"><p>选择要完成的工作：</p>{templates.map((template) => <button type="button" key={template.template_id} disabled={busy} onClick={() => void create(template)}><b>{template.name}</b><span>{template.description}</span></button>)}</section> : null}
      <div className="workflow-task-layout">
        <aside className="workflow-task-list"><div>我的流程 <span>{workflows.length}</span></div>{workflows.length ? workflows.map((workflow) => <article className={selectedId === workflow.workflow_id ? "selected" : ""} key={workflow.workflow_id}><button type="button" onClick={() => { setSelectedId(workflow.workflow_id); setInputValues({}); setLastRun(null); setError(""); }}><b>{workflow.name}</b><small>{workflowKind(workflow, templates)}</small></button><button className="workflow-delete" type="button" disabled={busy} onClick={() => void remove(workflow)}>删除</button></article>) : <p>还没有流程。</p>}</aside>
        <main className="workflow-task-detail">
          {!selected ? <div className="workflow-task-empty"><h2>选择一个流程</h2><p>点击左侧流程，或点右上角“新建流程”。</p></div> : <>
            <header><div><h2>{selected.name}</h2><p>{workflowKind(selected, templates)}</p></div><button className="btn primary" type="button" disabled={busy} onClick={() => void run()}>{busy ? "正在启动…" : "开始运行"}</button></header>
            {(selectedTemplate?.input_fields || []).length ? <section className="workflow-device-picker"><h3>填写运行条件</h3><p>运行条件由流程所属扩展提供，平台不会替扩展猜测业务参数。</p>{selectedTemplate!.input_fields!.map((field) => field.type === "text" ? <label className="workflow-script-picker" key={field.name}>{field.label}<input value={String(inputValues[field.name] || "")} onChange={(event) => setInputValues((values) => ({ ...values, [field.name]: event.target.value }))} /></label> : field.type === "select" ? <label className="workflow-script-picker" key={field.name}>{field.label}<select value={String(inputValues[field.name] || "")} onChange={(event) => setInputValues((values) => ({ ...values, [field.name]: event.target.value }))}><option value="">请选择{field.label}</option>{(inputOptions[field.name] || []).map((option) => <option key={option.value} value={option.value}>{option.label}{option.detail ? `（${option.detail}）` : ""}</option>)}</select></label> : <div key={field.name}><h4>{field.label}</h4>{(inputOptions[field.name] || []).length ? (inputOptions[field.name] || []).map((option) => { const selectedValues = Array.isArray(inputValues[field.name]) ? inputValues[field.name] as string[] : []; return <label key={option.value}><input type="checkbox" checked={selectedValues.includes(option.value)} onChange={(event) => setInputValues((values) => ({ ...values, [field.name]: event.target.checked ? [...selectedValues, option.value] : selectedValues.filter((value) => value !== option.value) }))} /><span><b>{option.label}</b>{option.detail ? <small>{option.detail}</small> : null}</span></label>; }) : <div className="workflow-no-assets">暂无可选项</div>}</div>)}</section> : <section className="workflow-task-summary"><h3>将执行</h3><p>{selected.nodes.map((node) => node.name || node.node_id).join("、")}</p><p>此流程不需要填写额外参数。</p></section>}
            {lastRun ? <section className={`workflow-task-result ${lastRun.status}`}><h3>{statusText(lastRun.status)}</h3><p>运行编号：{lastRun.run_id}</p>{lastRun.nodes.map((node) => <span key={node.node_id}>{node.node_id}：{statusText(node.status)}</span>)}</section> : null}
          </>}
        </main>
      </div>
    </div>
  </div>;
}
