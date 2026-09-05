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
        <span className="wb-empty-kicker">运维工作台</span>
      </div>
      <h2>{currentSessionId ? "开始处理任务" : "请先新建会话"}</h2>
      <p>
        {currentSessionId
          ? "描述问题、上传文件或给出目标。联智中枢会调用合适的工具，实时展示处理进度，并保留执行证据。"
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
