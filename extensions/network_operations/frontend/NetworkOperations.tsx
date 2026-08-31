import { useCallback, useEffect, useMemo, useState, useRef, type FormEvent } from "react";
import { apiRequest } from "../../../frontend/src/api/client";
import { confirm } from "../../../frontend/src/components/ConfirmDialog";
import { IconEdit, IconPlus, IconRefresh, IconTrash } from "../../../frontend/src/components/Icon";
import { Button, PageHeader } from "../../../frontend/src/components/ui";
import { useSessionStore } from "../../../frontend/src/stores/session";
import "./NetworkOperations.css";

type Region = { region_id: string; name: string };
type Device = { device_id: string; name: string; host: string; vendor: string; device_type: string; region_id: string };
type Connection = { connection_id: string; device_id: string; name?: string; protocol: "ssh" | "telnet"; port: number; username?: string; source_address?: string; effective_source_address?: string; auth_method?: string; status: string; verified: boolean; credential_configured?: boolean; last_error?: string; last_tested_at?: string; driver_id?: string; detected_vendor?: string; os_family?: string; semantic_facts?: string[]; profile_detected_from?: string };
type Skill = { skill_id: string; name: string; description: string; instructions?: string; enabled: boolean; device_ids: string[]; connection_ids: string[]; allowed_tool_ids: string[]; capabilities?: string[] };
type DeviceForm = Omit<Device, "device_id"> & { device_id?: string };
type ConnectionForm = { connection_id?: string; device_id: string; name: string; protocol: "ssh" | "telnet"; port: string; username: string; source_address: string; password: string; private_key: string; passphrase: string; auth_method: string };
type SkillForm = { capabilities: string[]; skill_id?: string; name: string; description: string; instructions: string; enabled: boolean; device_ids: string[]; connection_ids: string[]; allowed_tool_ids: string[] };

const base = "/extensions/network.operations";
const toolOptions = [
  { id: "network.operations.devices_read", label: "设备与连接目录", description: "允许模型读取已登记设备、区域和连接状态" },
  { id: "network.operations.skills_read", label: "Skill 配置读取", description: "允许模型核对当前 Skill 的配置边界" },
  { id: "network.operations.device.manage", label: "实时设备只读操作", description: "允许模型自主选择设备与只读命令；运行时按需连接、复用会话并处理分页" },
  { id: "network.operations.inspection", label: "多设备巡检", description: "允许模型发起、跟踪和重试持久巡检任务" },
] as const;
const allowedToolIds = toolOptions.map((item) => item.id);
const emptyDevice: DeviceForm = { name: "", host: "", vendor: "h3c", device_type: "switch", region_id: "" };
const emptyConnection: ConnectionForm = { device_id: "", name: "", protocol: "ssh", port: "22", username: "", source_address: "", password: "", private_key: "", passphrase: "", auth_method: "password" };
const emptySkill: SkillForm = { capabilities: [], name: "", description: "", instructions: "", enabled: true, device_ids: [], connection_ids: [], allowed_tool_ids: allowedToolIds };
const friendlyErrors: Record<string, string> = {
  "device name and host already exist": "设备名称与管理地址均相同的设备已存在",
};

export default function NetworkOperations() {
  const workspaceId = useSessionStore((state) => state.currentWorkspaceId);
  const [editor, setEditor] = useState<"device" | "connection" | "skill" | null>(null);
  const [query, setQuery] = useState("");
  const [regionFilter, setRegionFilter] = useState("");
  const editorRef = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (editor) editorRef.current?.showModal(); }, [editor]);
  const [view, setView] = useState<"devices" | "skills">("devices");
  const [regions, setRegions] = useState<Region[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [deviceForm, setDeviceForm] = useState<DeviceForm>(emptyDevice);
  const [connectionForm, setConnectionForm] = useState<ConnectionForm>(emptyConnection);
  const [skillForm, setSkillForm] = useState<SkillForm>(emptySkill);
  const [editingRegionId, setEditingRegionId] = useState("");
  const [regionName, setRegionName] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    const params = { workspace_id: workspaceId };
    const [regionResult, deviceResult, connectionResult, skillResult] = await Promise.all([
      apiRequest<{ regions: Region[] }>({ method: "GET", url: `${base}/regions`, params }),
      apiRequest<{ devices: Device[] }>({ method: "GET", url: `${base}/devices`, params }),
      apiRequest<{ connections: Connection[] }>({ method: "GET", url: `${base}/connections`, params }),
      apiRequest<{ skills: Skill[] }>({ method: "GET", url: `${base}/skills`, params }),
    ]);
    setRegions(regionResult.regions || []);
    setDevices(deviceResult.devices || []);
    setConnections(connectionResult.connections || []);
    setSkills(skillResult.skills || []);
  }, [workspaceId]);

  useEffect(() => { void load().catch(() => setNotice("数据加载失败，请检查服务。")); }, [load]);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 4500);
    return () => window.clearTimeout(timer);
  }, [notice]);
  const filteredDevices = devices.filter((item) => (!regionFilter || item.region_id === regionFilter) && `${item.name} ${item.host}`.toLowerCase().includes(query.toLowerCase()));
  const filteredSkills = skills.filter((item) => `${item.name} ${item.description}`.toLowerCase().includes(query.toLowerCase()));
  const byDevice = useMemo(() => new Map(devices.map((item) => [item.device_id, item])), [devices]);
  const byRegion = useMemo(() => new Map(regions.map((item) => [item.region_id, item.name])), [regions]);

  const run = async <T,>(work: () => Promise<T>, success: string | ((result: T) => string)): Promise<{ ok: true; result: T } | { ok: false }> => {
    setBusy(true); setNotice("");
    try {
      const result = await work();
      const message = typeof success === "function" ? success(result) : success;
      try { await load(); setNotice(message); }
      catch { setNotice(`${message}；列表刷新失败，请点击刷新，勿重复提交。`); }
      return { ok: true, result };
    } catch (error) {
      const message = String((error as { message?: string }).message || "操作失败");
      setNotice(friendlyErrors[message] || message);
      return { ok: false };
    } finally { setBusy(false); }
  };

  const saveRegion = (event: FormEvent) => {
    event.preventDefault();
    if (!regionName.trim()) return;
    const regionId = editingRegionId;
    void run(() => apiRequest({ method: regionId ? "PUT" : "POST", url: regionId ? `${base}/regions/${regionId}` : `${base}/regions`, data: { workspace_id: workspaceId, name: regionName } }), regionId ? "区域已更新" : "区域已创建")
      .then((outcome) => { if (outcome.ok) { setEditingRegionId(""); setRegionName(""); } });
  };

  const removeRegion = async (region: Region) => {
    if (!await confirm({ title: "删除区域", body: `将硬删除“${region.name}”。包含设备的区域不能删除。`, confirmLabel: "删除", destructive: true })) return;
    void run(() => apiRequest({ method: "DELETE", url: `${base}/regions/${region.region_id}`, data: { workspace_id: workspaceId } }), "区域已删除");
  };

  const saveDevice = (event: FormEvent) => {
    event.preventDefault();
    const deviceId = deviceForm.device_id;
    void run(() => apiRequest<{ device: Device }>({ method: deviceId ? "PUT" : "POST", url: deviceId ? `${base}/devices/${deviceId}` : `${base}/devices`, data: { ...deviceForm, workspace_id: workspaceId } }), (result) => {
      if (!deviceId) setConnectionForm({ ...emptyConnection, device_id: result.device.device_id });
      return deviceId ? "设备信息已更新" : "设备已登记，请继续配置并测试连接";
    }).then((outcome) => { if (outcome.ok) { setDeviceForm(emptyDevice); setEditor(deviceId ? null : "connection"); } });
  };

  const saveConnection = (event: FormEvent) => {
    event.preventDefault();
    const connectionId = connectionForm.connection_id;
    void run(() => apiRequest<{ connection: Connection }>({
      method: connectionId ? "PUT" : "POST",
      url: connectionId ? `${base}/connections/${connectionId}` : `${base}/connections`,
      data: { ...connectionForm, workspace_id: workspaceId, port: Number(connectionForm.port), username: connectionForm.username || undefined, password: connectionForm.password || undefined, private_key: connectionForm.private_key || undefined, passphrase: connectionForm.passphrase || undefined },
    }), (result) => result.connection.verified ? "连接已保存并验证成功，可加入 Skill" : `连接已保存；当前验证未通过，Skill 调用时会主动重连：${result.connection.last_error || "请检查网络或凭据"}`)
      .then((outcome) => { if (outcome.ok) { setConnectionForm(emptyConnection); setEditor(null); } });
  };

  const testConnection = (connection: Connection) => void run(() => apiRequest({ method: "POST", url: `${base}/connections/${connection.connection_id}/test`, data: { workspace_id: workspaceId, accept_host_key: connection.status === "trust_required" } }), "连接测试完成");

  const removeConnection = async (connection: Connection) => {
    const deviceName = byDevice.get(connection.device_id)?.name || "该设备";
    if (!await confirm({ title: "删除连接", body: `将硬删除 ${deviceName} 的 ${connection.protocol.toUpperCase()} 连接；失去全部有效连接的 Skill 将一并删除。`, confirmLabel: "删除", destructive: true })) return;
    void run(() => apiRequest({ method: "DELETE", url: `${base}/connections/${connection.connection_id}`, data: { workspace_id: workspaceId } }), "连接已删除");
  };

  const removeDevice = async (device: Device) => {
    const connectionCount = connections.filter((item) => item.device_id === device.device_id).length;
    if (!await confirm({
      title: "永久删除设备",
      body: `将硬删除“${device.name}”及其 ${connectionCount} 条连接。关联 Skill 会移除该设备；失去全部设备或连接的 Skill 将一并硬删除。此操作不可恢复。`,
      confirmLabel: "永久删除",
      destructive: true,
    })) return;
    void run(
      () => apiRequest({ method: "DELETE", url: `${base}/devices/${device.device_id}`, data: { workspace_id: workspaceId } }),
      "设备及其连接已永久删除",
    ).then((outcome) => {
      if (!outcome.ok) return;
      if (deviceForm.device_id === device.device_id) setDeviceForm(emptyDevice);
      if (connectionForm.device_id === device.device_id) setConnectionForm(emptyConnection);
    });
  };

  const saveSkill = (event: FormEvent) => {
    event.preventDefault();
    const skillId = skillForm.skill_id;
    void run(() => apiRequest({ method: skillId ? "PUT" : "POST", url: skillId ? `${base}/skills/${skillId}` : `${base}/skills`, data: { ...skillForm, workspace_id: workspaceId } }), skillId ? "Skill 已更新，工作台目录将使用新配置" : "Skill 已发布到工作台")
      .then((outcome) => { if (outcome.ok) { setSkillForm(emptySkill); setEditor(null); } });
  };

  const removeSkill = async (skill: Skill) => {
    if (!await confirm({
      title: "永久删除 Skill",
      body: `将硬删除“${skill.name}”，工作台将无法再选择它；已登记设备和连接不会被删除。此操作不可恢复。`,
      confirmLabel: "永久删除",
      destructive: true,
    })) return;
    void run(
      () => apiRequest({ method: "DELETE", url: `${base}/skills/${skill.skill_id}`, data: { workspace_id: workspaceId } }),
      "Skill 已永久删除",
    ).then((outcome) => {
      if (outcome.ok && skillForm.skill_id === skill.skill_id) setSkillForm(emptySkill);
    });
  };

  const toggleSkill = (skill: Skill) => void run(
    () => apiRequest({
      method: "PUT",
      url: `${base}/skills/${skill.skill_id}`,
      data: { ...skill, workspace_id: workspaceId, enabled: !skill.enabled },
    }),
    skill.enabled ? "Skill 已停用，工作台不再展示" : "Skill 已启用，可在工作台选择",
  );

  const editSkill = (skill: Skill) => {
    setSkillForm({
      ...emptySkill,
      ...skill,
      capabilities: skill.capabilities || [],
      instructions: skill.instructions || "",
      allowed_tool_ids: skill.allowed_tool_ids?.length ? skill.allowed_tool_ids : allowedToolIds,
    });
    setEditor("skill");
  };

  const editConnection = (connection: Connection) => {
    setConnectionForm({ connection_id: connection.connection_id, device_id: connection.device_id, name: connection.name || "", protocol: connection.protocol, port: String(connection.port), username: connection.username || "", source_address: connection.source_address || "", password: "", private_key: "", passphrase: "", auth_method: connection.auth_method || (connection.protocol === "telnet" ? "none" : "password") });
    setEditor("connection");
  };

  const editDevice = (device: Device) => {
    setDeviceForm({ ...device });
    setEditor("device");
  };

  const addConnectionFor = (device: Device) => {
    setConnectionForm({ ...emptyConnection, device_id: device.device_id });
    setEditor("connection");
  };

  return <div className="network-admin">
    <PageHeader title="网络设备与 Skill" subtitle="集中管理设备连接，按 Skill 授权读取、巡检与配置能力。"><Button icon={<IconRefresh size={14} />} onClick={() => void load().catch(() => setNotice("刷新失败，请检查服务。"))} disabled={busy}>刷新</Button></PageHeader>
    <div className="network-tabs"><button className={view === "devices" ? "active" : ""} onClick={() => { setView("devices"); setQuery(""); }}>设备与连接 <span>{devices.length}</span></button><button className={view === "skills" ? "active" : ""} onClick={() => { setView("skills"); setQuery(""); }}>Skill 配置 <span>{skills.length}</span></button></div>
    {notice ? <div role="status" className="network-notice">{notice}</div> : null}
    <div className="network-toolbar">
      <div className="network-filters"><input aria-label={view === "devices" ? "搜索设备" : "搜索 Skill"} placeholder={view === "devices" ? "搜索设备名称、管理地址" : "搜索 Skill 名称、说明"} value={query} onChange={(event) => setQuery(event.target.value)} />
      {view === "devices" && <select aria-label="筛选区域" value={regionFilter} onChange={(event) => setRegionFilter(event.target.value)}><option value="">全部区域</option>{regions.map((region) => <option key={region.region_id} value={region.region_id}>{region.name}</option>)}</select>}</div>
      <Button icon={<IconPlus size={14} />} onClick={() => { if (view === "devices") { setDeviceForm(emptyDevice); setEditor("device"); } else { setSkillForm(emptySkill); setEditor("skill"); } }}>{view === "devices" ? "登记设备" : "创建 Skill"}</Button>
    </div>
    {editor && <dialog ref={editorRef} className="network-editor" aria-label={editor === "skill" ? "Skill 编辑面板" : editor === "device" ? "设备编辑面板" : "连接编辑面板"} onCancel={(event) => { if (busy) event.preventDefault(); else setEditor(null); }}>
      <div className="network-editor-top"><span>{editor === "skill" ? "配置工作台能力" : editor === "device" ? "维护设备与区域" : "配置设备访问方式"}</span><Button disabled={busy} onClick={() => setEditor(null)}>关闭</Button></div>
      {notice && <div role="alert" className="network-notice">{notice}</div>}
      {editor === "device" ? (<section className="network-panel">
        <h2>{deviceForm.device_id ? "编辑设备" : "登记设备"}</h2><p>设备只保存身份与区域，凭据由独立连接安全管理。</p>
        <form onSubmit={saveRegion} className="inline-form"><input value={regionName} onChange={(event) => setRegionName(event.target.value)} placeholder={editingRegionId ? "修改区域名称" : "新建设备区域"} /><Button type="submit">{editingRegionId ? "保存" : "添加区域"}</Button>{editingRegionId ? <Button type="button" onClick={() => { setEditingRegionId(""); setRegionName(""); }}>取消</Button> : null}</form>
        {regions.length ? <div className="region-list">{regions.map((region) => <span key={region.region_id}>{region.name}<button type="button" onClick={() => { setEditingRegionId(region.region_id); setRegionName(region.name); }}>编辑</button><button type="button" onClick={() => void removeRegion(region)}>删除</button></span>)}</div> : null}
        <form onSubmit={saveDevice} className="form-grid">
          <label>设备名称<input required value={deviceForm.name} onChange={(event) => setDeviceForm({ ...deviceForm, name: event.target.value })} /></label>
          <label>管理地址<input required value={deviceForm.host} onChange={(event) => setDeviceForm({ ...deviceForm, host: event.target.value })} /></label>
          <label>厂商<select value={deviceForm.vendor} onChange={(event) => setDeviceForm({ ...deviceForm, vendor: event.target.value })}><option value="h3c">H3C</option><option value="huawei">华为</option><option value="cisco">Cisco</option><option value="generic">通用</option></select></label>
          <label>区域<select value={deviceForm.region_id} onChange={(event) => setDeviceForm({ ...deviceForm, region_id: event.target.value })}><option value="">未分区</option>{regions.map((region) => <option key={region.region_id} value={region.region_id}>{region.name}</option>)}</select></label>
          <div className="form-actions"><Button type="submit" disabled={busy}>{deviceForm.device_id ? "保存设备" : "登记设备"}</Button>{deviceForm.device_id ? <Button type="button" onClick={() => setEditor(null)}>取消</Button> : null}</div>
        </form>
      </section>) : editor === "connection" ? (<section className="network-panel">
        <h2>{connectionForm.connection_id ? "编辑连接" : "配置连接"}</h2><p>SSH 必须提供用户名；Telnet 支持无认证，端口均可自定义。</p>
        <form onSubmit={saveConnection} className="form-grid">
          <label>设备<select required value={connectionForm.device_id} onChange={(event) => setConnectionForm({ ...connectionForm, device_id: event.target.value })}><option value="">选择设备</option>{devices.map((device) => <option key={device.device_id} value={device.device_id}>{device.name} · {device.host}</option>)}</select></label>
          <label>连接名称<input value={connectionForm.name} onChange={(event) => setConnectionForm({ ...connectionForm, name: event.target.value })} placeholder="如：生产管理口" /></label>
          <label>协议<select value={connectionForm.protocol} onChange={(event) => { const protocol = event.target.value as "ssh" | "telnet"; setConnectionForm({ ...connectionForm, protocol, port: protocol === "ssh" ? "22" : "23", auth_method: protocol === "ssh" ? "password" : "none" }); }}><option value="ssh">SSH</option><option value="telnet">Telnet</option></select></label>
          <label>端口<input required type="number" min="1" max="65535" value={connectionForm.port} onChange={(event) => setConnectionForm({ ...connectionForm, port: event.target.value })} /></label>
          <label>认证<select value={connectionForm.auth_method} onChange={(event) => setConnectionForm({ ...connectionForm, auth_method: event.target.value })}>{connectionForm.protocol === "telnet" ? <option value="none">无认证</option> : null}<option value="password">用户名/密码</option>{connectionForm.protocol === "ssh" ? <option value="private_key">SSH 私钥</option> : null}</select></label>
          <label>源地址（可选）<input value={connectionForm.source_address} onChange={(event) => setConnectionForm({ ...connectionForm, source_address: event.target.value })} placeholder="留空自动选择；也可填写本机出口 IP" /></label>
          {connectionForm.auth_method !== "none" ? <label>用户名<input required={connectionForm.protocol === "ssh"} value={connectionForm.username} onChange={(event) => setConnectionForm({ ...connectionForm, username: event.target.value })} /></label> : null}
          {connectionForm.auth_method === "password" ? <label>密码<input type="password" value={connectionForm.password} onChange={(event) => setConnectionForm({ ...connectionForm, password: event.target.value })} placeholder={connectionForm.connection_id ? "留空则保留原密码" : ""} /></label> : null}
          {connectionForm.auth_method === "private_key" ? <><label className="full-field">SSH 私钥<textarea value={connectionForm.private_key} onChange={(event) => setConnectionForm({ ...connectionForm, private_key: event.target.value })} placeholder={connectionForm.connection_id ? "留空则保留原私钥" : "粘贴 PEM/OpenSSH 私钥"} /></label><label>私钥口令<input type="password" value={connectionForm.passphrase} onChange={(event) => setConnectionForm({ ...connectionForm, passphrase: event.target.value })} placeholder="无口令可留空" /></label></> : null}
          <div className="form-actions"><Button type="submit" disabled={busy}>保存并测试</Button>{connectionForm.connection_id ? <Button type="button" onClick={() => setEditor(null)}>取消</Button> : null}</div>
        </form>
      </section>) : (<section className="network-panel"><h2>{skillForm.skill_id ? "编辑 Skill" : "创建 Skill"}</h2><p>Skill 决定工作台可选设备与可信连接，模型在此边界内自主编排工具。</p><form onSubmit={saveSkill} className="form-grid">
        <label>名称<input required value={skillForm.name} onChange={(event) => setSkillForm({ ...skillForm, name: event.target.value })} /></label>
        <label className="check enabled-check"><input type="checkbox" checked={skillForm.enabled} onChange={(event) => setSkillForm({ ...skillForm, enabled: event.target.checked })} />工作台启用</label>
        <label className="full-field">说明<textarea value={skillForm.description} onChange={(event) => setSkillForm({ ...skillForm, description: event.target.value })} /></label>
        <fieldset><legend>多选设备</legend>{devices.map((device) => <label className="check" key={device.device_id}><input type="checkbox" checked={skillForm.device_ids.includes(device.device_id)} onChange={(event) => setSkillForm({ ...skillForm, device_ids: event.target.checked ? [...skillForm.device_ids, device.device_id] : skillForm.device_ids.filter((id) => id !== device.device_id), connection_ids: event.target.checked ? skillForm.connection_ids : skillForm.connection_ids.filter((id) => connections.find((connection) => connection.connection_id === id)?.device_id !== device.device_id) })} />{device.name} · {device.host}</label>)}</fieldset>
        <fieldset><legend>设备连接</legend>{connections.filter((connection) => connection.credential_configured && skillForm.device_ids.includes(connection.device_id)).map((connection) => <label className="check" key={connection.connection_id}><input type="checkbox" checked={skillForm.connection_ids.includes(connection.connection_id)} onChange={(event) => setSkillForm({ ...skillForm, connection_ids: event.target.checked ? [...skillForm.connection_ids, connection.connection_id] : skillForm.connection_ids.filter((id) => id !== connection.connection_id) })} />{byDevice.get(connection.device_id)?.name} · {connection.protocol.toUpperCase()}:{connection.port} · {connection.verified ? "最近连接成功" : "调用时主动连接"}</label>)}</fieldset>
        <fieldset className="full-field"><legend>允许的能力</legend>{toolOptions.map((tool) => <label className="check capability-check" key={tool.id}><input type="checkbox" checked={skillForm.allowed_tool_ids.includes(tool.id)} onChange={(event) => setSkillForm({ ...skillForm, capabilities: !event.target.checked && tool.id === "network.operations.device.manage" ? [] : skillForm.capabilities, allowed_tool_ids: event.target.checked ? [...skillForm.allowed_tool_ids, tool.id] : skillForm.allowed_tool_ids.filter((id) => id !== tool.id) })} /><span><b>{tool.label}</b><small>{tool.description}</small></span></label>)}</fieldset>
        <fieldset className="full-field write-capability"><legend>配置权限</legend>
          <label className="check capability-check"><input type="checkbox" checked={skillForm.capabilities.includes("configuration_write")} disabled={!skillForm.allowed_tool_ids.includes("network.operations.device.manage")} onChange={(event) => setSkillForm({ ...skillForm, capabilities: event.target.checked ? ["configuration_write"] : [] })} /><span><b>允许配置写入</b><small>允许 LLM 自主生成配置命令。仅限所选设备和连接，每批配置执行前需审批；失败不自动重试。</small></span></label>
          <p>默认只读。开启不会立即连接或修改设备，也不会自动保存配置、确认交互或重启。</p>
        </fieldset>
        <label className="full-field">使用说明<textarea value={skillForm.instructions} onChange={(event) => setSkillForm({ ...skillForm, instructions: event.target.value })} placeholder="描述目标、证据要求和操作边界；模型自行决定工具顺序与并行关系。" /></label>
        <div className="form-actions"><Button type="submit" disabled={busy || !skillForm.device_ids.length || !skillForm.connection_ids.length || !skillForm.allowed_tool_ids.length}>{skillForm.skill_id ? "保存 Skill" : "发布到工作台"}</Button>{skillForm.skill_id ? <Button type="button" onClick={() => setEditor(null)}>取消</Button> : null}</div>
      </form></section>)}
    </dialog>}
    {view === "devices" ? <div className="network-grid">
      <section className="network-panel network-span registered-devices">
        <div className="panel-heading">
          <div><h2>已登记设备</h2><p>在这里维护设备身份、连接和删除操作。</p></div>
          <span className="record-count">{devices.length} 台设备</span>
        </div>
        <div className="device-list">{filteredDevices.length ? filteredDevices.map((device) => {
          const deviceConnections = connections.filter((item) => item.device_id === device.device_id);
          return <article key={device.device_id} className="device-card" data-testid={`device-card-${device.device_id}`}>
            <header className="device-card-header">
              <div className="device-identity">
                <strong>{device.name}</strong>
                <span>{device.host} · {device.vendor.toUpperCase()} · {byRegion.get(device.region_id) || "未分区"}</span>
              </div>
              <div className="device-actions" aria-label={`${device.name} 设备管理`}>
                <Button size="sm" icon={<IconEdit size={13} />} onClick={() => editDevice(device)}>编辑设备</Button>
                <Button size="sm" variant="danger" icon={<IconTrash size={13} />} onClick={() => void removeDevice(device)}>永久删除设备</Button>
              </div>
            </header>
            <details className="connection-section">
              <summary><span>{deviceConnections.length ? deviceConnections.map((item) => `${item.protocol.toUpperCase()}:${item.port}`).join(" · ") : "尚未配置连接"}</span><span>管理连接 · {deviceConnections.length} 条</span></summary>
              <div className="connection-heading">
                <div><b>设备连接</b><span>{deviceConnections.length} 条</span></div>
                <Button size="sm" icon={<IconPlus size={13} />} onClick={() => addConnectionFor(device)}>添加连接</Button>
              </div>
              <div className="connection-list">{deviceConnections.length ? deviceConnections.map((connection) => <div key={connection.connection_id} className="connection-row">
                <div className="connection-summary">
                  <span className={`status ${connection.status}`}>{connection.verified ? "最近验证成功" : connection.status === "trust_required" ? "待确认指纹" : connection.status === "untested" ? "未测试" : "连接失败"}</span>
                  <b>{connection.protocol.toUpperCase()}:{connection.port}</b>
                  <small title={connection.last_error || connection.last_tested_at}>{connection.verified ? [connection.driver_id || connection.os_family, connection.effective_source_address ? `源地址 ${connection.effective_source_address}` : ""].filter(Boolean).join(" · ") || connection.last_tested_at : connection.last_error || connection.last_tested_at || "尚未测试"}</small>
                </div>
                <div className="connection-actions" aria-label={`${device.name} ${connection.protocol.toUpperCase()}:${connection.port} 连接管理`}>
                  <Button size="sm" onClick={() => editConnection(connection)}>编辑连接</Button>
                  <Button size="sm" onClick={() => testConnection(connection)}>{connection.status === "trust_required" ? "确认并重试" : "测试连接"}</Button>
                  <Button size="sm" variant="danger-ghost" onClick={() => void removeConnection(connection)}>永久删除连接</Button>
                </div>
              </div>) : <div className="connection-empty">尚未配置连接。添加连接后即可加入 Skill，执行时按需连接。</div>}</div>
            </details>
          </article>;
        }) : <div className="empty">{devices.length ? "没有匹配的设备" : "尚未登记设备，点击右上角登记设备开始"}</div>}</div>
      </section>
    </div> : <div className="network-grid">
      <section className="network-panel published-skills">
        <div className="panel-heading">
          <div><h2>已发布 Skill</h2><p>维护工作台可选 Skill 的状态、设备、连接与能力边界。</p></div>
          <span className="record-count">{skills.length} 个 Skill</span>
        </div>
        <div className="skill-list">{filteredSkills.length ? filteredSkills.map((skill) => {
          const skillDevices = skill.device_ids.map((id) => byDevice.get(id)?.name).filter(Boolean);
          const skillConnections = skill.connection_ids.map((id) => {
            const connection = connections.find((item) => item.connection_id === id);
            return connection ? `${byDevice.get(connection.device_id)?.name || "未知设备"} ${connection.protocol.toUpperCase()}:${connection.port}` : "失效连接";
          });
          return <article key={skill.skill_id} className="skill-card" data-testid={`skill-card-${skill.skill_id}`}>
            <header className="skill-card-header">
              <div className="skill-title">
                <strong>{skill.name}</strong>
                <span className={`skill-state ${skill.enabled ? "enabled" : "disabled"}`}>{skill.enabled ? "已启用" : "已停用"}</span>
              </div>
              <div className="skill-actions" aria-label={`${skill.name} Skill 管理`}>
                <Button size="sm" icon={<IconEdit size={13} />} onClick={() => editSkill(skill)}>编辑 Skill</Button>
                <Button size="sm" onClick={() => toggleSkill(skill)}>{skill.enabled ? "停用 Skill" : "启用 Skill"}</Button>
                <Button size="sm" variant="danger" icon={<IconTrash size={13} />} onClick={() => void removeSkill(skill)}>永久删除 Skill</Button>
              </div>
            </header>
            <p className="skill-description">{skill.description || "暂无说明"}</p>
            <dl className="skill-scope">
              <div><dt>设备</dt><dd>{skillDevices.length ? skillDevices.join("、") : "无可用设备"}</dd></div>
              <div><dt>连接</dt><dd>{skillConnections.length ? skillConnections.join("、") : "无可用连接"}</dd></div>
              <div><dt>能力</dt><dd>{skill.allowed_tool_ids.length} 项已授权 · <b className={skill.capabilities?.includes("configuration_write") ? "write-enabled" : ""}>{skill.capabilities?.includes("configuration_write") ? "可配置 · 需审批" : "只读"}</b></dd></div>
            </dl>
          </article>;
        }) : <div className="empty">{skills.length ? "没有匹配的 Skill" : "尚未创建 Skill，选择设备并配置能力后发布到工作台"}</div>}</div>
      </section>
    </div>}
  </div>;
}
