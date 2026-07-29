/**
 * CapabilityCenter — business capabilities + canonical tool catalog.
 *
 * 展示可用能力、风险边界；工具目录索引。
 */
import { useMemo, useState } from "react";
import { capabilitiesApi, toolsApi } from "../../api";
import { useAsync, AsyncView, Badge, InlineCode } from "../../components/common";
import type { BusinessCapability, RiskLevel, ToolCatalogCategory, ToolCatalogItem, ToolGovernanceStatus } from "../../types";
import { IconBolt, IconShield } from "../../components/Icon";

const R_KIND: Record<RiskLevel, "ok" | "info" | "warn" | "err"> = { low: "ok", medium: "info", high: "warn", critical: "err", forbidden: "err" };
const R_LABEL: Record<RiskLevel, string> = { low: "低", medium: "中", high: "高", critical: "严重", forbidden: "禁止" };
const G_KIND: Record<ToolGovernanceStatus, "ok" | "info" | "warn" | "muted"> = { active: "ok", disabled: "info", internal: "info", forbidden: "warn" };

type ToolFilter = "all" | "planner" | "active" | "disabled" | "internal" | "forbidden" | "high" | "host" | "workspace" | "knowledge";
const T_FILTERS: { id: ToolFilter; label: string }[] = [
  { id: "all", label: "全部" }, { id: "planner", label: "可见" }, { id: "active", label: "活跃" },
  { id: "disabled", label: "停用" }, { id: "internal", label: "内部" }, { id: "forbidden", label: "禁止" },
  { id: "high", label: "高风险" }, { id: "host", label: "Host" }, { id: "workspace", label: "空间" },
  { id: "knowledge", label: "知识" },
];

const CAP_TITLES: Record<string, string> = { knowledge: "知识问答", knowledge_qa: "知识问答", memory_lookup: "记忆检索", workspace_read: "工作区读取", report_drafting: "报告草拟", runtime_diagnostics: "运行诊断", agent_delegation: "Agent 协作", browser: "浏览器自动化", artifact: "制品管理" };

export function CapabilityCenter() {
  const [tq, setTq] = useState("");
  const [tf, setTf] = useState<ToolFilter>("all");
  const list = useAsync<{ capabilities: BusinessCapability[] }>((s) => capabilitiesApi.manifest(s));
  const catalog = useAsync((s) => toolsApi.catalog(s));

  const counts = useMemo(() => {
    if (list.state.kind !== "success") return { tot: 0, hi: 0, dep: 0 };
    const c = list.state.data.capabilities ?? [];
    return { tot: c.length, hi: c.filter((x) => x.risk_level === "high" || x.risk_level === "forbidden").length, dep: c.filter((x) => x.can_create_sensitive_output).length };
  }, [list.state]);

  return (
    <div className="page" data-testid="page-capabilities">
      <div className="page-header cc-page-header">
        <div>
          <h1>能力矩阵<span className="cc-title-aux">· Capabilities</span></h1>
          <p className="subtitle">查看当前可用能力、风险边界和人工复核要求；规划中能力不提供调用入口</p>
        </div>
        <div className="cc-pill-row">
          <span className="status-pill"><span className="dot accent" />{counts.tot} 项</span>
          {counts.dep > 0 && <span className="status-pill"><IconBolt size={10} />{counts.dep} 涉及产物</span>}
        </div>
      </div>

      <div className="page-body">
        {/* Tool Catalog */}
        <div className="card cc-card-mb">
          <div className="card-title">
            工具目录
            {catalog.state.kind === "success" && <span className="count">{catalog.state.data.count ?? 0}</span>}
          </div>
          <div className="cc-controls">
            <span className="cc-controls-desc">
              {catalog.state.kind === "success" && <>Planner 可见 {catalog.state.data.planner_visible_count ?? 0} 个工具</>}
            </span>
            <input className="input cc-search-input" value={tq} onChange={(e) => setTq(e.target.value)} placeholder="搜索 canonical / action…" />
          </div>
          <div className="segmented cc-segmented">
            {T_FILTERS.map((f) => (
              <button key={f.id} className={tf === f.id ? "active" : ""} onClick={() => setTf(f.id)} type="button">{f.label}</button>
            ))}
          </div>
          <AsyncView state={catalog.state} onRetry={catalog.reload} emptyText="无工具目录" emptyHint="/api/tools/catalog 未返回数据">
            {(d) => <ToolTree cats={d.categories ?? []} query={tq} filter={tf} />}
          </AsyncView>
        </div>

        {/* Capability cards */}
        <AsyncView state={list.state} onRetry={list.reload} emptyText="无业务能力" emptyHint="agent.capabilities.catalog 未返回能力">
          {(d) => (
            <div className="capability-grid" data-testid="capability-list">
              {(d.capabilities ?? []).map((cap) => <CapCard key={cap.capability_id} cap={cap} />)}
            </div>
          )}
        </AsyncView>
      </div>
    </div>
  );
}

/* ── Tool tree ── */

function ToolTree({ cats, query, filter }: { cats: ToolCatalogCategory[]; query: string; filter: ToolFilter }) {
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return cats.map((c) => ({
      ...c, groups: c.groups.map((g) => {
        const tools = g.tools.filter((t) => matchT(t, q) && matchF(t, filter));
        return { ...g, tools, count: tools.length };
      }).filter((g) => g.tools.length > 0),
    })).filter((c) => c.groups.length > 0);
  }, [cats, query, filter]);

  if (!cats.length) return <div className="cc-empty-state">暂无目录数据</div>;

  return (
    <div className="tool-tree">
      {filtered.map((c) => (
        <details key={c.id} className="tool-category" open={!query}>
          <summary><span className="cc-category-name">{c.name}</span><span className="tool-count">{c.count}</span></summary>
          {c.description && <div className="cc-category-desc">{c.description}</div>}
          <div className="tool-groups">
            {c.groups.map((g) => (
              <details key={g.id} className="tool-group" open={Boolean(query)}>
                <summary><span>{g.name}</span><span className="tool-count">{g.count}</span></summary>
                <div className="tool-list">
                  {g.tools.map((t) => <TRow key={t.canonical_tool_id} tool={t} />)}
                </div>
              </details>
            ))}
          </div>
        </details>
      ))}
    </div>
  );
}

/** 工具治理状态 → 中文标签 */
const G_LABEL: Record<ToolGovernanceStatus, string> = { active: "活跃", disabled: "停用", internal: "内部", forbidden: "禁止" };

function TRow({ tool }: { tool: ToolCatalogItem }) {
  const canonicalRefs = tool.capability_actions ?? [];
  const actionProfiles = tool.action_profiles ?? [];
  const actionProfileMap = new Map(actionProfiles.map((profile) => [profile.action, profile]));
  const needsApproval = tool.requires_approval;
  const riskBadge = R_LABEL[tool.risk_level] ?? tool.risk_level;
  const govLabel = G_LABEL[(tool.governance_status ?? "active")] ?? tool.governance_status ?? "未知";

  // 用户可读的动作列表
  const actionLabels: Record<string, string> = {
    list: "查看列表", find: "查找", search: "搜索", get: "获取详情",
    create: "创建", update: "更新", delete: "删除", load: "加载",
    inspect: "检查", execute: "执行", send: "发送", upload: "上传",
    download: "下载", approve: "审批", reject: "驳回",
  };

  return (
    <details className="tool-row">
      <summary>
        <span className="cc-tool-name">{tool.display_name}</span>
        <InlineCode>{tool.canonical_tool_id}</InlineCode>
        <Badge kind={G_KIND[(tool.governance_status ?? "active")] ?? "muted"}>{govLabel}</Badge>
        <Badge kind={R_KIND[tool.risk_level] ?? "muted"}>{riskBadge}</Badge>
      </summary>

      {/* ── 用户视角的详情卡片 ── */}
      <div className="tool-detail-card">
        {/* 第一行：描述 + 状态摘要 */}
        {(tool.description || tool.usage_hint) && (
          <div className="tool-desc-block">
            {tool.description && (
              <p className="tool-main-desc">{tool.description}</p>
            )}
            {tool.usage_hint && (
              <p className="tool-usage-hint">{tool.usage_hint}</p>
            )}
          </div>
        )}

        {/* 安全与权限 */}
        <div className="tool-info-grid">
          <div className="tool-info-item">
            <span className="tool-info-label">风险等级</span>
            <Badge kind={R_KIND[tool.risk_level] ?? "muted"}>{riskBadge}</Badge>
          </div>
          <div className="tool-info-item">
            <span className="tool-info-label">人工审批</span>
            <Badge kind={needsApproval ? "warn" : "ok"}>{needsApproval ? "需要审批" : "无需审批"}</Badge>
          </div>
          <div className="tool-info-item">
            <span className="tool-info-label">Planner 可见</span>
            <Badge kind={tool.planner_visible ? "ok" : "muted"}>{tool.planner_visible ? "对 AI 可见" : "对 AI 隐藏"}</Badge>
          </div>
          <div className="tool-info-item">
            <span className="tool-info-label">运行状态</span>
            <Badge kind={G_KIND[(tool.governance_status ?? "active")] ?? "muted"}>{govLabel}</Badge>
          </div>
        </div>

        {/* 支持的操作（翻译为中文） */}
        {tool.actions?.length ? (
          <div className="tool-actions-block">
            <span className="tool-info-label">支持操作</span>
            <div className="tool-action-chips">
              {tool.actions.map((a) => (
                <span key={a} className="tool-action-chip">
                  {actionLabels[a] || a}
                  {actionProfileMap.get(a)?.requires_approval ? " · 审批" : ""}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        {/* 限制说明 */}
        {tool.not_for && (
          <div className="tool-restriction">
            <span className="tool-info-label">⚠ 使用限制</span>
            <span>{tool.not_for}</span>
          </div>
        )}

        {/* 技术细节（折叠） */}
        <details className="collapse tool-tech-details cc-tech-collapse">
          <summary className="cc-tech-summary">技术详情</summary>
          <div className="cc-tech-grid">
            <D label="命名空间"><InlineCode>{tool.category}/{tool.group}/{tool.action}</InlineCode></D>
            {canonicalRefs.length > 0 && <D label="关联能力">{canonicalRefs.map((a) => <InlineCode key={a}>{a}</InlineCode>)}</D>}
            {tool.permission_action && <D label="权限动作"><InlineCode>{tool.permission_action}</InlineCode></D>}
            {actionProfiles.length > 0 && (
              <D label="动作边界">
                {actionProfiles.map((profile) => (
                  <InlineCode key={profile.action}>
                    {profile.action}:{profile.permission_action}/{R_LABEL[profile.risk_level] ?? profile.risk_level}{profile.requires_approval ? "/审批" : ""}
                  </InlineCode>
                ))}
              </D>
            )}
            {tool.allowed_callers?.length ? <D label="允许调用方">{tool.allowed_callers.map((c) => <InlineCode key={c}>{c}</InlineCode>)}</D> : null}
          </div>
        </details>
      </div>
    </details>
  );
}

function D({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="tool-detail"><span className="cc-tech-label">{label}</span><div className="cc-tech-chips">{children}</div></div>;
}

/* ── Capability card ── */

function CapCard({ cap }: { cap: BusinessCapability }) {
  const title = CAP_TITLES[cap.capability_id] || cap.description?.split(/[.。]/)[0] || cap.capability_id;
  const outcome = cap.can_create_sensitive_output ? "可能产出敏感材料，必须人工复核" : cap.requires_verification ? "结果需要人工确认后使用" : "可用于当前工作区的辅助分析";

  return (
    <div className="card" data-testid={`cap-${cap.capability_id}`}>
      {/* Header */}
      <div className="cap-header-row">
        <div className="cap-title-block">
          <h4 className="cap-title-h4">{title}</h4>
          <div className="cap-outcome">{outcome}</div>
        </div>
        <Badge kind="ok" withDot>已启用</Badge>
      </div>

      {cap.description && <div className="cap-desc">{cap.description}</div>}

      {/* Safety */}
      <div className="card-title cap-safety-title"><IconShield size={11} /> 使用边界</div>
      <div className="cap-info-grid">
        <SR label="风险等级">{<Badge kind={R_KIND[cap.risk_level]}>{R_LABEL[cap.risk_level]}</Badge>}</SR>
        <SR label="敏感输出">{cap.can_create_sensitive_output ? <Badge kind="warn">需复核</Badge> : <Badge kind="ok" withDot>不产生</Badge>}</SR>
        <SR label="评审要求">{cap.requires_verification ? <Badge kind="warn">需要</Badge> : <Badge kind="ok" withDot>无需</Badge>}</SR>
        <SR label="LLM 调用"><Badge kind="ok" withDot>可</Badge></SR>
      </div>

      <details className="collapse cap-collapse">
        <summary className="cap-collapse-summary">技术详情</summary>
        <div className="cap-tech-chips">
          <InlineCode>{cap.capability_id}</InlineCode>
          <InlineCode>{cap.intent}</InlineCode>
          <InlineCode>{cap.module}</InlineCode>
          {cap.category && <Badge kind="muted">{cap.category}</Badge>}
        </div>
      </details>
    </div>
  );
}

function SR({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="cap-sr-row"><span className="cap-row-label">{label}</span>{children}</div>;
}

/* ── Tool filter/matcher ── */

function matchT(t: ToolCatalogItem, q: string): boolean {
  if (!q) return true;
  return [
    t.display_name,
    t.canonical_tool_id,
    t.category,
    t.group,
    t.action,
    t.governance_status ?? "",
    ...(t.actions ?? []),
    ...(t.capability_actions ?? []),
    t.usage_hint ?? "",
    t.not_for ?? "",
    t.description ?? "",
  ].some((v) => String(v ?? "").toLowerCase().includes(q));
}

function matchF(t: ToolCatalogItem, f: ToolFilter): boolean {
  if (f === "planner") return Boolean(t.planner_visible);
  if (f === "active") return t.governance_status === "active";
  if (f === "disabled") return t.governance_status === "disabled";
  if (f === "internal") return t.governance_status === "internal";
  if (f === "forbidden") return t.governance_status === "forbidden";
  if (f === "high") return t.risk_level === "high" || t.requires_approval;
  if (f === "host" || f === "workspace" || f === "knowledge") return t.category === f;
  return true;
}
