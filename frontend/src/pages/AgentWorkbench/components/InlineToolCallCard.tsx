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
