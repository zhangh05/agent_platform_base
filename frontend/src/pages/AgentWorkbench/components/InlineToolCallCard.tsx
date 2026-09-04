import { useState } from "react";
import type { InlineToolCall } from "../../../types";
import { IconCheck, IconAlert, IconChevronDown, IconChevronRight, IconClock, IconDocument } from "../../../components/Icon";

interface InlineToolCallCardProps {
  toolCall: InlineToolCall;
  seq: number;
}

export function InlineToolCallCard({ toolCall, seq }: InlineToolCallCardProps) {
  const [open, setOpen] = useState(false);
  const errText = toolCall.errors?.join(", ");
  const orchestration = toolCall.orchestration;
  const orchestrationStep = typeof orchestration?.step_id === "string" ? orchestration.step_id : "";
  const orchestrationLayer = typeof orchestration?.layer === "number" && Number.isFinite(orchestration.layer)
    ? orchestration.layer
    : null;
  const orchestrationDependsOn = Array.isArray(orchestration?.depends_on)
    ? orchestration.depends_on.filter((item): item is string => typeof item === "string")
    : [];

  return (
    <div
      className={`tool-call-card ${toolCall.ok ? "ok" : "fail"}${open ? " is-open" : ""}`}
      onClick={() => setOpen(!open)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setOpen(!open);
        }
      }}
    >
      <div className="tool-call-card-header">
        <span className="tc-seq">#{seq}</span>
        <span className={`tc-icon ${toolCall.ok ? "ok" : "fail"}`}>
          {toolCall.ok ? <IconCheck size={13} weight="bold" /> : <IconAlert size={13} weight="bold" />}
        </span>
        <span className="tc-name">{toolCall.tool_name}</span>
        {toolCall.duration_ms != null && (
          <span className="tc-duration-pill">
            <IconClock size={11} />
            {(toolCall.duration_ms / 1000).toFixed(1)}s
          </span>
        )}
        <span className="tc-chev">
          {open ? <IconChevronDown size={13} /> : <IconChevronRight size={13} />}
        </span>
      </div>
      {open && (
        <div className="tool-call-card-body">
          {toolCall.summary && <div className="tc-summary">{toolCall.summary}</div>}
          {errText && <div className="tc-error">{errText}</div>}
          {toolCall.duration_ms != null && (
            <div className="tc-duration">{(toolCall.duration_ms / 1000).toFixed(1)}s</div>
          )}
          {orchestrationStep && (
            <div className="tc-orchestration">
              <span>步骤：{orchestrationStep}</span>
              {orchestrationLayer != null && <span>第 {orchestrationLayer} 组</span>}
              {orchestration?.parallel === true && <span>并行执行</span>}
              {orchestrationDependsOn.length ? (
                <span>依赖：{orchestrationDependsOn.join("、")}</span>
              ) : null}
            </div>
          )}
          {toolCall.artifacts && toolCall.artifacts.length > 0 && (
            <div className="tc-artifacts">
              {toolCall.artifacts.map((a) => (
                <span key={a.artifact_id} className="tc-artifact-tag">
                  <IconDocument size={12} /> {a.title || a.artifact_id}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
