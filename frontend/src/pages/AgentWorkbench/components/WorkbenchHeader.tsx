import { memo } from "react";
import type { ChatMsg } from "../../../stores/workbench";
import { IconChat, IconChevronDown, IconHistory } from "../../../components/Icon";

export interface WorkbenchHeaderProps {
  sessionTitle: string;
  viewMode: "chat" | "timeline";
  onViewModeChange: (mode: "chat" | "timeline") => void;
  headerCollapsed: boolean;
  onToggleHeaderCollapsed: () => void;
  llmHealth: {
    connected: boolean;
    provider?: string;
    model?: string;
    recentFailure?: string;
  };
  currentSessionId: string | null;
  visibleHistory: ChatMsg[];
}

export const WorkbenchHeader = memo(function WorkbenchHeader({
  sessionTitle,
  viewMode,
  onViewModeChange,
  headerCollapsed,
  onToggleHeaderCollapsed,
  llmHealth,
  currentSessionId,
  visibleHistory,
}: WorkbenchHeaderProps) {
  const llmStatusLabel = llmHealth.connected
    ? llmHealth.recentFailure
      ? "模型可用 · 最近一次请求超时，可重试"
      : `模型可用 · ${llmHealth.model || llmHealth.provider || "在线"}`
    : "模型不可用";

  const handleExport = () => {
    if (!currentSessionId || visibleHistory.length === 0) return;
    const md = visibleHistory
      .map((m) => `## ${m.role === "user" ? "用户" : "AI"}\n\n${m.text}\n\n---\n`)
      .join("\n");
    const blob = new Blob([md], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `session-${currentSessionId.slice(0, 8)}-${new Date().toISOString().slice(0, 10)}.md`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 100);
  };

  return (
    <header className="wb-header" id="workbench-session-header">
      <div className="wb-header-context">
        <span className="wb-header-kicker">{viewMode === "chat" ? "当前会话" : "运行记录"}</span>
        <h1 title={sessionTitle}>{viewMode === "chat" ? sessionTitle : "完整时间线"}</h1>
      </div>
      <div className="wb-header-actions">
        <button
          type="button"
          className="wb-header-collapse"
          aria-label={headerCollapsed ? "展开会话栏" : "收起会话栏"}
          aria-controls="workbench-session-header"
          aria-expanded={!headerCollapsed}
          onClick={onToggleHeaderCollapsed}
          data-testid="btn-toggle-session-header"
        >
          <IconChevronDown size={14} />
          <span>{headerCollapsed ? "展开" : "收起"}</span>
        </button>
        <span className="wb-header-status" title={llmStatusLabel}>
          <span className={"dot " + (llmHealth.connected ? (llmHealth.recentFailure ? "warn" : "ok") : "err")} />
          <span className="wb-header-status-text">{llmStatusLabel}</span>
        </span>
        <button
          type="button"
          className={`wb-mode-btn ${viewMode === "chat" ? "active" : ""}`}
          onClick={() => onViewModeChange("chat")}
          aria-label="对话"
          aria-pressed={viewMode === "chat"}
          data-testid="view-chat"
        >
          <IconChat size={15} />
          <span>对话</span>
        </button>
        <button
          type="button"
          className={`wb-mode-btn ${viewMode === "timeline" ? "active" : ""}`}
          onClick={() => onViewModeChange("timeline")}
          aria-label="时间线"
          aria-pressed={viewMode === "timeline"}
          data-testid="view-timeline"
        >
          <IconHistory size={15} />
          <span>时间线</span>
        </button>
        {currentSessionId && visibleHistory.length > 0 ? (
          <button className="wb-export-btn" title="导出对话" onClick={handleExport}>
            导出
          </button>
        ) : null}
      </div>
    </header>
  );
});
