import type { ComponentType } from "react";
import { Link } from "../../router";
import { IconCheck, IconLayers } from "../../components/Icon";

type AdvancedEntry = {
  to: string;
  label: string;
  subtitle: string;
  description: string;
  Icon: ComponentType<{ size?: string | number }>;
};

const ENTRIES: AdvancedEntry[] = [
  {
    to: "/reviews",
    label: "结果审核",
    subtitle: "Review Center",
    description: "集中处理需要人工确认、复核或追踪的运行结果。",
    Icon: IconCheck,
  },
  {
    to: "/extensions",
    label: "扩展管理",
    subtitle: "Extension Center",
    description: "查看扩展版本、权限、运行状态和工作区迁移信息。",
    Icon: IconLayers,
  },
  {
    to: "/workflows",
    label: "流程编排",
    subtitle: "Workflow Studio",
    description: "构建并维护可复用的工作流定义与运行策略。",
    Icon: IconLayers,
  },
];

export function AdvancedCenter() {
  return (
    <div className="page advanced-center" data-testid="page-advanced">
      <header className="page-header">
        <div>
          <h1>高级 <span>Advanced</span></h1>
          <p className="subtitle">低频治理、扩展与流程编排能力统一入口。</p>
        </div>
      </header>
      <section className="advanced-center-grid" aria-label="高级功能列表">
        {ENTRIES.map(({ to, label, subtitle, description, Icon }) => (
          <Link key={to} to={to} className="advanced-center-card" viewTransition>
            <span className="advanced-center-icon"><Icon size={20} /></span>
            <span className="advanced-center-copy">
              <strong>{label}</strong>
              <small>{subtitle}</small>
              <span>{description}</span>
            </span>
          </Link>
        ))}
      </section>
    </div>
  );
}
