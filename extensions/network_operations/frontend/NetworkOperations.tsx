import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { apiRequest } from "../../../frontend/src/api/client";
import { confirm } from "../../../frontend/src/components/ConfirmDialog";
import { Button, PageHeader } from "../../../frontend/src/components/ui";
import { useSessionStore } from "../../../frontend/src/stores/session";
import "./NetworkOperations.css";

type Region = { region_id: string; name: string };
type Device = { device_id: string; name: string; host: string; vendor: string; device_type: string; region_id: string };
type Connection = { connection_id: string; device_id: string; name?: string; protocol: "ssh" | "telnet"; port: number; username?: string; source_address?: string; effective_source_address?: string; auth_method?: string; status: string; verified: boolean; last_error?: string; last_tested_at?: string };
type Skill = { skill_id: string; name: string; description: string; instructions?: string; enabled: boolean; device_ids: string[]; connection_ids: string[]; allowed_tool_ids: string[] };
type DeviceForm = Omit<Device, "device_id"> & { device_id?: string };
type ConnectionForm = { connection_id?: string; device_id: string; name: string; protocol: "ssh" | "telnet"; port: string; username: string; source_address: string; password: string; private_key: string; passphrase: string; auth_method: string };
type SkillForm = { skill_id?: string; name: string; description: string; instructions: string; enabled: boolean; device_ids: string[]; connection_ids: string[]; allowed_tool_ids: string[] };

const base = "/extensions/network.operations";
const toolOptions = [
  { id: "network.operations.devices_read", label: "设备与连接目录", description: "允许模型读取已登记设备、区域和连接状态" },
  { id: "network.operations.skills_read", label: "Skill 配置读取", description: "允许模型核对当前 Skill 的配置边界" },
  { id: "network.operations.device.manage", label: "实时设备只读操作", description: "允许模型探测连接并执行明确的只读命令" },
  { id: "network.operations.inspection", label: "多设备巡检", description: "允许模型发起、跟踪和重试持久巡检任务" },
] as const;
const allowedToolIds = toolOptions.map((item) => item.id);
const emptyDevice: DeviceForm = { name: "", host: "", vendor: "h3c", device_type: "switch", region_id: "" };
const emptyConnection: ConnectionForm = { device_id: "", name: "", protocol: "ssh", port: "22", username: "", source_address: "", password: "", private_key: "", passphrase: "", auth_method: "password" };
const emptySkill: SkillForm = { name: "", description: "", instructions: "", enabled: true, device_ids: [], connection_ids: [], allowed_tool_ids: allowedToolIds };

export default function NetworkOperations() {
  const workspaceId = useSessionStore((state) => state.currentWorkspaceId);
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
  const byDevice = useMemo(() => new Map(devices.map((item) => [item.device_id, item])), [devices]);
  const byRegion = useMemo(() => new Map(regions.map((item) => [item.region_id, item.name])), [regions]);

  const run = async <T,>(work: () => Promise<T>, success: string | ((result: T) => string)): Promise<{ ok: true; result: T } | { ok: false }> => {
    setBusy(true); setNotice("");
    try {
      const result = await work();
      await load();
      setNotice(typeof success === "function" ? success(result) : success);
      return { ok: true, result };
    } catch (error) {
      setNotice(String((error as { message?: string }).message || "操作失败"));
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
      if (!deviceId) setConnectionForm((value) => ({ ...value, device_id: result.device.device_id }));
      return deviceId ? "设备信息已更新" : "设备已登记，请继续配置并测试连接";
    }).then((outcome) => { if (outcome.ok) setDeviceForm(emptyDevice); });
  };

  const saveConnection = (event: FormEvent) => {
    event.preventDefault();
    const connectionId = connectionForm.connection_id;
    void run(() => apiRequest<{ connection: Connection }>({
      method: connectionId ? "PUT" : "POST",
      url: connectionId ? `${base}/connections/${connectionId}` : `${base}/connections`,
      data: { ...connectionForm, workspace_id: workspaceId, port: Number(connectionForm.port), username: connectionForm.username || undefined, password: connectionForm.password || undefined, private_key: connectionForm.private_key || undefined, passphrase: connectionForm.passphrase || undefined },
    }), (result) => result.connection.verified ? "连接已保存并验证成功，可加入 Skill" : `连接已保存，但验证未通过：${result.connection.last_error || "请检查网络或凭据"}`)
      .then((outcome) => { if (outcome.ok) setConnectionForm(emptyConnection); });
  };

  const testConnection = (connection: Connection) => void run(() => apiRequest({ method: "POST", url: `${base}/connections/${connection.connection_id}/test`, data: { workspace_id: workspaceId, accept_host_key: connection.status === "trust_required" } }), "连接测试完成");

  const removeConnection = async (connection: Connection) => {
    const deviceName = byDevice.get(connection.device_id)?.name || "该设备";
    if (!await confirm({ title: "删除连接", body: `将硬删除 ${deviceName} 的 ${connection.protocol.toUpperCase()} 连接；失去全部有效连接的 Skill 将一并删除。`, confirmLabel: "删除", destructive: true })) return;
    void run(() => apiRequest({ method: "DELETE", url: `${base}/connections/${connection.connection_id}`, data: { workspace_id: workspaceId } }), "连接已删除");
  };

  const removeDevice = async (device: Device) => {
    if (!await confirm({ title: "删除设备", body: `将硬删除“${device.name}”及其连接；失去有效资源的 Skill 将一并删除。`, confirmLabel: "删除", destructive: true })) return;
    void run(() => apiRequest({ method: "DELETE", url: `${base}/devices/${device.device_id}`, data: { workspace_id: workspaceId } }), "设备已删除");
  };

  const saveSkill = (event: FormEvent) => {
    event.preventDefault();
    const skillId = skillForm.skill_id;
    void run(() => apiRequest({ method: skillId ? "PUT" : "POST", url: skillId ? `${base}/skills/${skillId}` : `${base}/skills`, data: { ...skillForm, workspace_id: workspaceId } }), skillId ? "Skill 已更新，工作台目录将使用新配置" : "Skill 已发布到工作台")
      .then((outcome) => { if (outcome.ok) setSkillForm(emptySkill); });
  };

  const removeSkill = async (skill: Skill) => {
    if (!await confirm({ title: "删除 Skill", body: `将硬删除“${skill.name}”，不会删除设备和连接。`, confirmLabel: "删除", destructive: true })) return;
    void run(() => apiRequest({ method: "DELETE", url: `${base}/skills/${skill.skill_id}`, data: { workspace_id: workspaceId } }), "Skill 已删除");
  };

  const editConnection = (connection: Connection) => {
    setConnectionForm({ connection_id: connection.connection_id, device_id: connection.device_id, name: connection.name || "", protocol: connection.protocol, port: String(connection.port), username: connection.username || "", source_address: connection.source_address || "", password: "", private_key: "", passphrase: "", auth_method: connection.auth_method || (connection.protocol === "telnet" ? "none" : "password") });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return <div className="network-admin">
    <PageHeader title="网络设备与 Skill" description="先登记设备并验证 SSH/Telnet 连接，再把多台设备组合成工作台可选 Skill。" actions={<Button onClick={() => void load()} disabled={busy}>刷新</Button>} />
    <div className="network-tabs"><button className={view === "devices" ? "active" : ""} onClick={() => setView("devices")}>设备与连接</button><button className={view === "skills" ? "active" : ""} onClick={() => setView("skills")}>Skill 配置</button></div>
    {notice ? <div className="network-notice">{notice}</div> : null}
    {view === "devices" ? <div className="network-grid">
      <section className="network-panel">
        <h2>{deviceForm.device_id ? "编辑设备" : "登记设备"}</h2><p>设备只保存身份与区域，凭据由独立连接安全管理。</p>
        <form onSubmit={saveRegion} className="inline-form"><input value={regionName} onChange={(event) => setRegionName(event.target.value)} placeholder={editingRegionId ? "修改区域名称" : "新建设备区域"} /><Button type="submit">{editingRegionId ? "保存" : "添加区域"}</Button>{editingRegionId ? <Button type="button" onClick={() => { setEditingRegionId(""); setRegionName(""); }}>取消</Button> : null}</form>
        {regions.length ? <div className="region-list">{regions.map((region) => <span key={region.region_id}>{region.name}<button type="button" onClick={() => { setEditingRegionId(region.region_id); setRegionName(region.name); }}>编辑</button><button type="button" onClick={() => void removeRegion(region)}>删除</button></span>)}</div> : null}
        <form onSubmit={saveDevice} className="form-grid">
          <label>设备名称<input required value={deviceForm.name} onChange={(event) => setDeviceForm({ ...deviceForm, name: event.target.value })} /></label>
          <label>管理地址<input required value={deviceForm.host} onChange={(event) => setDeviceForm({ ...deviceForm, host: event.target.value })} /></label>
          <label>厂商<select value={deviceForm.vendor} onChange={(event) => setDeviceForm({ ...deviceForm, vendor: event.target.value })}><option value="h3c">H3C</option><option value="huawei">华为</option><option value="cisco">Cisco</option><option value="generic">通用</option></select></label>
          <label>区域<select value={deviceForm.region_id} onChange={(event) => setDeviceForm({ ...deviceForm, region_id: event.target.value })}><option value="">未分区</option>{regions.map((region) => <option key={region.region_id} value={region.region_id}>{region.name}</option>)}</select></label>
          <div className="form-actions"><Button type="submit" disabled={busy}>{deviceForm.device_id ? "保存设备" : "登记设备"}</Button>{deviceForm.device_id ? <Button type="button" onClick={() => setDeviceForm(emptyDevice)}>取消</Button> : null}</div>
        </form>
      </section>
      <section className="network-panel">
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
          <div className="form-actions"><Button type="submit" disabled={busy}>保存并测试</Button>{connectionForm.connection_id ? <Button type="button" onClick={() => setConnectionForm(emptyConnection)}>取消</Button> : null}</div>
        </form>
      </section>
      <section className="network-panel network-span"><h2>已登记设备</h2><div className="device-list">{devices.length ? devices.map((device) => <article key={device.device_id} className="device-card">
        <div><strong>{device.name}</strong><span>{device.host} · {device.vendor.toUpperCase()} · {byRegion.get(device.region_id) || "未分区"}</span></div>
        <div className="connection-list">{connections.filter((item) => item.device_id === device.device_id).map((connection) => <div key={connection.connection_id}><span className={`status ${connection.status}`}>{connection.verified ? "已连接" : connection.status === "trust_required" ? "待确认指纹" : "连接失败"}</span><b>{connection.protocol.toUpperCase()}:{connection.port}</b><small title={connection.last_error || connection.last_tested_at}>{connection.verified && connection.effective_source_address ? `源地址 ${connection.effective_source_address}` : connection.last_error || connection.last_tested_at || "尚未测试"}</small><div className="row-actions"><Button onClick={() => editConnection(connection)}>编辑</Button><Button onClick={() => testConnection(connection)}>{connection.status === "trust_required" ? "确认并重试" : "测试"}</Button><Button variant="danger" onClick={() => void removeConnection(connection)}>删除</Button></div></div>)}</div>
        <div className="row-actions"><Button onClick={() => setDeviceForm({ ...device })}>编辑</Button><Button variant="danger" onClick={() => void removeDevice(device)}>删除</Button></div>
      </article>) : <div className="empty">尚未登记设备</div>}</div></section>
    </div> : <div className="network-grid">
      <section className="network-panel"><h2>{skillForm.skill_id ? "编辑 Skill" : "创建 Skill"}</h2><p>Skill 决定工作台可选设备与可信连接，模型在此边界内自主编排工具。</p><form onSubmit={saveSkill} className="form-grid">
        <label>名称<input required value={skillForm.name} onChange={(event) => setSkillForm({ ...skillForm, name: event.target.value })} /></label>
        <label className="check enabled-check"><input type="checkbox" checked={skillForm.enabled} onChange={(event) => setSkillForm({ ...skillForm, enabled: event.target.checked })} />工作台启用</label>
        <label className="full-field">说明<textarea value={skillForm.description} onChange={(event) => setSkillForm({ ...skillForm, description: event.target.value })} /></label>
        <fieldset><legend>多选设备</legend>{devices.map((device) => <label className="check" key={device.device_id}><input type="checkbox" checked={skillForm.device_ids.includes(device.device_id)} onChange={(event) => setSkillForm({ ...skillForm, device_ids: event.target.checked ? [...skillForm.device_ids, device.device_id] : skillForm.device_ids.filter((id) => id !== device.device_id), connection_ids: event.target.checked ? skillForm.connection_ids : skillForm.connection_ids.filter((id) => connections.find((connection) => connection.connection_id === id)?.device_id !== device.device_id) })} />{device.name} · {device.host}</label>)}</fieldset>
        <fieldset><legend>可用连接</legend>{connections.filter((connection) => connection.verified && skillForm.device_ids.includes(connection.device_id)).map((connection) => <label className="check" key={connection.connection_id}><input type="checkbox" checked={skillForm.connection_ids.includes(connection.connection_id)} onChange={(event) => setSkillForm({ ...skillForm, connection_ids: event.target.checked ? [...skillForm.connection_ids, connection.connection_id] : skillForm.connection_ids.filter((id) => id !== connection.connection_id) })} />{byDevice.get(connection.device_id)?.name} · {connection.protocol.toUpperCase()}:{connection.port}</label>)}</fieldset>
        <fieldset className="full-field"><legend>允许的能力</legend>{toolOptions.map((tool) => <label className="check capability-check" key={tool.id}><input type="checkbox" checked={skillForm.allowed_tool_ids.includes(tool.id)} onChange={(event) => setSkillForm({ ...skillForm, allowed_tool_ids: event.target.checked ? [...skillForm.allowed_tool_ids, tool.id] : skillForm.allowed_tool_ids.filter((id) => id !== tool.id) })} /><span><b>{tool.label}</b><small>{tool.description}</small></span></label>)}</fieldset>
        <label className="full-field">使用说明<textarea value={skillForm.instructions} onChange={(event) => setSkillForm({ ...skillForm, instructions: event.target.value })} placeholder="描述目标、证据要求和操作边界；模型自行决定工具顺序与并行关系。" /></label>
        <div className="form-actions"><Button type="submit" disabled={busy || !skillForm.device_ids.length || !skillForm.connection_ids.length || !skillForm.allowed_tool_ids.length}>{skillForm.skill_id ? "保存 Skill" : "发布到工作台"}</Button>{skillForm.skill_id ? <Button type="button" onClick={() => setSkillForm(emptySkill)}>取消</Button> : null}</div>
      </form></section>
      <section className="network-panel"><h2>已发布 Skill</h2><div className="skill-list">{skills.length ? skills.map((skill) => <article key={skill.skill_id}><div><strong>{skill.name}</strong><p>{skill.description || "暂无说明"}</p></div><span>{skill.device_ids.length} 台设备 · {skill.connection_ids.length} 条连接 · {skill.allowed_tool_ids.length} 项能力 · {skill.enabled ? "已启用" : "已停用"}</span><div className="row-actions"><Button onClick={() => setSkillForm({ ...emptySkill, ...skill, instructions: skill.instructions || "", allowed_tool_ids: skill.allowed_tool_ids?.length ? skill.allowed_tool_ids : allowedToolIds })}>编辑</Button><Button variant="danger" onClick={() => void removeSkill(skill)}>删除</Button></div></article>) : <div className="empty">尚未创建 Skill</div>}</div></section>
    </div>}
  </div>;
}
