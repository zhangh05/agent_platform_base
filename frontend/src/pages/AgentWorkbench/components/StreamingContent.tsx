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
  const { thinking, body } = parseThinking(text);
  const html = body ? renderAssistantHtml(body) : "";
  return (
    <>
      {thinking && <ThinkingBlock content={thinking} defaultOpen />}
      {html && <div className="streaming-markdown markdown-body" dangerouslySetInnerHTML={{ __html: html }} />}
      {!body && !thinking && <span className="text-sm">{text}</span>}
    </>
  );
}
