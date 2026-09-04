import { useEffect, useId, useRef, useState } from "react";
import { IconBrain, IconChevronDown, IconChevronRight, IconCopy, IconCheck } from "../../../components/Icon";

interface ThinkingBlockProps {
  content: string;
  defaultOpen?: boolean;
}

/** Collapsible thinking/reasoning block with modern crystal styling */
export function ThinkingBlock({ content, defaultOpen }: ThinkingBlockProps) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "failed">("idle");
  const resetTimerRef = useRef<number | null>(null);
  const bodyId = useId();

  useEffect(() => () => {
    if (resetTimerRef.current !== null) window.clearTimeout(resetTimerRef.current);
  }, []);

  const showTemporaryCopyStatus = (status: "copied" | "failed") => {
    if (resetTimerRef.current !== null) window.clearTimeout(resetTimerRef.current);
    setCopyStatus(status);
    resetTimerRef.current = window.setTimeout(() => {
      setCopyStatus("idle");
      resetTimerRef.current = null;
    }, 1800);
  };

  const handleCopy = async () => {
    if (!navigator.clipboard?.writeText) {
      showTemporaryCopyStatus("failed");
      return;
    }
    try {
      await navigator.clipboard.writeText(content);
      showTemporaryCopyStatus("copied");
    } catch {
      showTemporaryCopyStatus("failed");
    }
  };

  const copyLabel = copyStatus === "copied" ? "已复制" : copyStatus === "failed" ? "复制失败" : "复制";

  return (
    <div className={`thinking-block${open ? " is-expanded" : ""}`}>
      <div className={`thinking-header ${open ? "open" : ""}`}>
        <button
          type="button"
          className="thinking-disclosure"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen((current) => !current)}
        >
          <span className="chev" aria-hidden="true">
            {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
          </span>
          <span className="thinking-title">
            <IconBrain size={15} className="thinking-icon" />
            <span>思考与推理过程</span>
          </span>
          <span className="thinking-header-toggle">{open ? "收起" : "展开"}</span>
        </button>
        {open ? (
          <button
            type="button"
            className={`thinking-copy-btn${copyStatus === "failed" ? " is-failed" : ""}`}
            onClick={() => void handleCopy()}
            title="复制思考过程"
            aria-live="polite"
          >
            {copyStatus === "copied" ? <IconCheck size={12} /> : <IconCopy size={12} />}
            <span>{copyLabel}</span>
          </button>
        ) : null}
      </div>
      {open && <div className="thinking-body" id={bodyId}>{content}</div>}
    </div>
  );
}
