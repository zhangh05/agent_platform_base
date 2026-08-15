import { memo } from "react";
import type { ActiveTurnSnapshot } from "../../../types";
import type { ChatMsg } from "../../../stores/workbench";
import { IconBolt, IconCheck, IconChevronLeft, IconChevronRight, IconClock, IconDocument, IconProbe, IconShield } from "../../../components/Icon";
import { buildTaskProgress } from "../../../utils/taskProgress";

type Props = {
  latestAssistant?: ChatMsg;
  snapshot?: ActiveTurnSnapshot;
  onShowTimeline: () => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
};

function statusText(status: string, evidenceCount: number): string {
  if (status === "running") return evidenceCount ? `正在处理 · ${evidenceCount} 项来源` : "正在处理";
  if (status === "succeeded") return evidenceCount ? `已收集 ${evidenceCount} 项证据` : "本轮已完成";
  if (status === "failed") return "本轮需要检查";
  return "等待任务";
}

function EvidenceIcon({ title }: { title: string }) {
  if (/防火墙|策略|安全|network|device/i.test(title)) return <IconShield size={16} />;
  if (/流量|数据|分析|python/i.test(title)) return <IconBolt size={16} />;
  if (/健康|检查|诊断/i.test(title)) return <IconProbe size={16} />;
  return <IconDocument size={15} />;
}

export const TaskProgressPanel = memo(function TaskProgressPanel({ latestAssistant, snapshot, onShowTimeline, collapsed, onToggleCollapsed }: Props) {
  const model = buildTaskProgress(latestAssistant, snapshot);
  const visibleEvidence = model.evidence.slice(0, 6);

  return (
    <aside className={collapsed ? "task-progress-panel is-collapsed" : "task-progress-panel"} aria-label="任务进度" data-testid="task-progress-panel">
      <header className="task-progress-header">
        <div>
          <span className="task-progress-kicker">实时状态</span>
          <h2>任务进度</h2>
        </div>
        <span className={`task-progress-summary ${model.status}`}>
          <span className="status-dot" />
          {statusText(model.status, model.evidence.filter((item) => item.status === "done").length)}
        </span>
        <button
          className="task-progress-collapse"
          type="button"
          onClick={onToggleCollapsed}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "展开实时状态" : "收起实时状态"}
          title={collapsed ? "展开实时状态" : "收起实时状态"}
        >
          <span className={"task-progress-summary " + model.status} aria-hidden="true"><span className="status-dot" /></span>
          {collapsed ? <IconChevronLeft size={16} /> : <IconChevronRight size={16} />}
          <span>{collapsed ? "展开" : "收起"}</span>
        </button>
      </header>

      <div className="task-phase-list">
        {model.phases.map((phase, index) => (
          <section className={`task-phase ${phase.state}`} key={phase.id}>
            <div className="task-phase-rail" aria-hidden="true">
              <span className="task-phase-index">
                {phase.state === "done" ? <IconCheck size={13} /> : index + 1}
              </span>
              {index < model.phases.length - 1 ? <span className="task-phase-line" /> : null}
            </div>
            <div className="task-phase-content">
              <div className="task-phase-title-row">
                <h3>{phase.title}</h3>
                <span>{phase.state === "done" ? "已完成" : phase.state === "active" ? "进行中" : phase.state === "failed" ? "需检查" : "等待中"}</span>
              </div>
              <p>{phase.description}</p>
              {phase.id === "evidence" && visibleEvidence.length > 0 ? (
                <div className="task-evidence-list">
                  {visibleEvidence.map((item) => (
                    <article className={`task-evidence ${item.status}`} key={item.id}>
                      <span className="task-evidence-icon"><EvidenceIcon title={item.title} /></span>
                      <div>
                        <strong>{item.title}</strong>
                        <span>来源：{item.source}</span>
                        {item.summary ? <small>{item.summary}</small> : null}
                      </div>
                      <span className="task-evidence-status">
                        {item.status === "running" ? <IconClock size={14} /> : item.status === "done" ? <IconCheck size={14} /> : "!"}
                      </span>
                    </article>
                  ))}
                </div>
              ) : null}
            </div>
          </section>
        ))}
      </div>

      <footer className="task-progress-footer">
        <span><IconShield size={14} />所有工具调用与分析结果均来自真实运行记录</span>
        <button type="button" onClick={onShowTimeline}>查看完整时间线</button>
      </footer>
    </aside>
  );
});
