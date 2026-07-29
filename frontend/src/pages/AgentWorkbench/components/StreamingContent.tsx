import { ThinkingBlock } from "./ThinkingBlock";

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
  return (
    <>
      {thinking && <ThinkingBlock content={thinking} defaultOpen />}
      {body && <span className="text-sm">{body}</span>}
      {!body && !thinking && <span className="text-sm">{text}</span>}
    </>
  );
}
