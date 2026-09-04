import { memo } from "react";
import { QUICK_CHIPS } from "../WorkbenchQuickChips";
import { IconSparkle } from "../../../components/Icon";

export interface WorkbenchEmptyStateProps {
  currentSessionId: string | null;
  onPickChip: (prompt: string) => void;
}

export const WorkbenchEmptyState = memo(function WorkbenchEmptyState({
  currentSessionId,
  onPickChip,
}: WorkbenchEmptyStateProps) {
  return (
    <div className="wb-empty" data-testid="workbench-empty">
      <div className="wb-empty-badge">
        <IconSparkle size={13} weight="fill" />
        <span className="wb-empty-kicker">开始一次可靠的智能运维任务</span>
      </div>
      <h2>{currentSessionId ? "今天需要处理什么？" : "请先新建会话"}</h2>
      <p>
        {currentSessionId
          ? "描述问题、上传文件或给出目标。联智中枢会调用合适的工具，并在右侧实时展示处理进度与证据。"
          : "点击左侧“新会话”，创建后即可开始。"}
      </p>

      <div className="wb-empty-chips">
        {QUICK_CHIPS.map((chip) => (
          <button
            key={chip.label}
            className="wb-input-chip"
            type="button"
            onClick={() => onPickChip(chip.prompt)}
            title={currentSessionId ? chip.prompt : "请先新建会话"}
            disabled={!currentSessionId}
          >
            {chip.label}
          </button>
        ))}
      </div>
    </div>
  );
});
