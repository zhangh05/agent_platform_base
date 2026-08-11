import { useDeferredValue, useMemo } from "react";
import { ThinkingBlock } from "./ThinkingBlock";
import { renderAssistantHtml } from "../../../utils/displayText";

/** Parse <think>...</think> and <thinking>...</thinking> blocks from content */
function parseThinking(text: string): { thinking: string; body: string } {
  const pat = /<(?:think|thinking)>([\s\S]*?)<\/(?:think|thinking)>/i;
  const match = text.match(pat);
  if (match) {
    return { thinking: match[1].trim(), body: text.replace(match[0], "").trim() };
  }
  return { thinking: "", body: text };
}

interface StreamingContentProps {
  text: string;
}

/** Streaming content with live thinking block support */
export function StreamingContent({ text }: StreamingContentProps) {
  // Long model responses can receive many token updates per second. Parsing the
  // full Markdown synchronously for every urgent update blocks navigation and
  // makes the workbench appear frozen. Let React defer the expensive render and
  // memoize it until the deferred text actually advances.
  const deferredText = useDeferredValue(text);
  const { thinking, body, html } = useMemo(() => {
    const parsed = parseThinking(deferredText);
    return {
      ...parsed,
      html: parsed.body ? renderAssistantHtml(parsed.body) : "",
    };
  }, [deferredText]);
  return (
    <>
      {thinking && <ThinkingBlock content={thinking} defaultOpen />}
      {html && <div className="streaming-markdown markdown-body" dangerouslySetInnerHTML={{ __html: html }} />}
      {!body && !thinking && <span className="text-sm">{deferredText}</span>}
    </>
  );
}
