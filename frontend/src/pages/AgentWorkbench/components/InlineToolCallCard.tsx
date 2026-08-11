import { useState } from "react";
import type { InlineToolCall } from "../../../types";

interface InlineToolCallCardProps {
  toolCall: InlineToolCall;
  seq: number;
}

export function InlineToolCallCard({ toolCall, seq }: InlineToolCallCardProps) {
  const [open, setOpen] = useState(false);
  const errText = toolCall.errors?.join(", ");
  return (
    <div className={`tool-call-card ${toolCall.ok ? "ok" : "fail"}`} onClick={() => setOpen(!open)}>
      <div className="tool-call-card-header">
        <span className="tc-seq">#{seq}</span>
        <span className="tc-icon">{toolCall.ok ? "✅" : "❌"}</span>
        <span className="tc-name">{toolCall.tool_name}</span>
        <span className="tc-chev">{open ? "▾" : "▸"}</span>
      </div>
      {open && (
        <div className="tool-call-card-body">
          {toolCall.summary && <div className="tc-summary">{toolCall.summary}</div>}
          {errText && <div className="tc-error">{errText}</div>}
          {toolCall.duration_ms != null && (
            <div className="tc-duration">{(toolCall.duration_ms / 1000).toFixed(1)}s</div>
          )}
          {toolCall.orchestration?.step_id && (
            <div className="tc-orchestration">
              <span>步骤：{toolCall.orchestration.step_id}</span>
              {toolCall.orchestration.layer != null && <span>第 {toolCall.orchestration.layer} 组</span>}
              {toolCall.orchestration.parallel && <span>并行执行</span>}
              {toolCall.orchestration.depends_on?.length ? (
                <span>依赖：{toolCall.orchestration.depends_on.join("、")}</span>
              ) : null}
            </div>
          )}
          {toolCall.artifacts && toolCall.artifacts.length > 0 && (
            <div className="tc-artifacts">
              {toolCall.artifacts.map((a) => (
                <span key={a.artifact_id} className="tc-artifact-tag">📄 {a.title || a.artifact_id}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
