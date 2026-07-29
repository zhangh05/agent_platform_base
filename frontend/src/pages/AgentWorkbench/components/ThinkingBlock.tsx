import { useState } from "react";

interface ThinkingBlockProps {
  content: string;
  defaultOpen?: boolean;
}

/** Collapsible thinking/reasoning block */
export function ThinkingBlock({ content, defaultOpen }: ThinkingBlockProps) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  return (
    <div className="thinking-block">
      <div className={`thinking-header ${open ? "open" : ""}`} onClick={() => setOpen(!open)}>
        <span className="chev">▸</span>
        <span>💭 思考过程</span>
        <span className="thinking-header-toggle">点击{open ? "收起" : "展开"}</span>
      </div>
      {open && <div className="thinking-body">{content}</div>}
    </div>
  );
}
