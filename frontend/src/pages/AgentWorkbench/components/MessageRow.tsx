import { memo, useCallback } from "react";
import type { InlineToolCall } from "../../../types";
import type { ChatMsg } from "../../../stores/workbench";
import { renderAssistantHtml, sanitizeAssistantText, toolLabel } from "../../../utils/displayText";
import { humanFailure } from "../../../utils/humanizeError";
import { InlineToolCallCard } from "./InlineToolCallCard";
import { ResultInline } from "./ResultInline";
import { StreamingContent } from "./StreamingContent";
import { ThinkingBlock } from "./ThinkingBlock";
import hljs from "highlight.js/lib/core";
import { useSessionStore } from "../../../stores/session";
import { IconAlert, IconDocument, IconSparkle } from "../../../components/Icon";

const COPY_FEEDBACK_MS = 2000;

interface MessageRowProps {
  m: ChatMsg;
  idx: number;
  total: number;
  lastUserInput: string;
  onRetryOriginal: (text: string) => void;
}

/** Parse <think>...</think> and <thinking>...</thinking> blocks from content */
function parseThinking(text: string): { thinking: string; body: string } {
  const pat = /<(?:think|thinking)>([\s\S]*?)<\/(?:think|thinking)>/i;
  const match = text.match(pat);
  if (match) {
    return { thinking: match[1].trim(), body: text.replace(match[0], "").trim() };
  }
  return { thinking: "", body: text };
}

const highlightCache = new Map<string, string>();
const HL_CACHE_MAX = 2000;

function ensureHighlightCacheRoom() {
  if (highlightCache.size < HL_CACHE_MAX) return;
  const overage = highlightCache.size - HL_CACHE_MAX + 1;
  const keys = highlightCache.keys();
  for (let i = 0; i < overage; i++) {
    const next = keys.next();
    if (next.done) break;
    highlightCache.delete(next.value);
  }
}

function highlightCode(html: string): string {
  return html.replace(/<pre><code class="language-([^"]+)">([\s\S]*?)<\/code><\/pre>/g, (_, lang, code) => {
    try {
      const decoded = new DOMParser().parseFromString(code, "text/html").body.textContent || "";
      const cacheKey = (lang || "") + " " + decoded;
      const hit = highlightCache.get(cacheKey);
      if (hit !== undefined) {
        highlightCache.delete(cacheKey);
        highlightCache.set(cacheKey, hit);
        return hit;
      }
      const langClass = lang && hljs.getLanguage(lang) ? lang : "plaintext";
      const result = hljs.highlight(decoded, { language: langClass }).value;
      const wrapped = `<div class="code-block-wrap"><div class="code-block-header"><span>${lang || "code"}</span><button class="code-copy-btn" type="button" data-code-copy="1">复制</button></div><pre><code class="hljs language-${langClass}">${result}</code></pre></div>`;
      ensureHighlightCacheRoom();
      highlightCache.set(cacheKey, wrapped);
      return wrapped;
    } catch {
      return `<pre><code>${code}</code></pre>`;
    }
  });
}

function handleCodeCopyClick(event: React.MouseEvent<HTMLDivElement>) {
  const target = event.target as HTMLElement | null;
  const button = target?.closest("[data-code-copy]") as HTMLButtonElement | null;
  if (!button) return;
  const code = button.closest(".code-block-wrap")?.querySelector("code")?.textContent || "";
  void navigator.clipboard?.writeText(code);
  button.textContent = "已复制";
  window.setTimeout(() => {
    button.textContent = "复制";
  }, COPY_FEEDBACK_MS);
}

export const MessageRow = memo(function MessageRow({ m, idx: _idx, total: _total, lastUserInput, onRetryOriginal }: MessageRowProps) {
  const workspaceId = useSessionStore((s) => s.currentWorkspaceId);
  const handleRetry = useCallback(() => {
    if (lastUserInput) onRetryOriginal(lastUserInput);
  }, [lastUserInput, onRetryOriginal]);

  if (m.role === "user") {
    return (
      <div className="message-row user" data-testid="chat-user">
        <div className="message-stack"><div className="chat-bubble user">
          {m.text && <div className="user-message-text">{m.text}</div>}
          {m.attachments?.length ? <div className="chat-attachments">
            {m.attachments.map((attachment) => attachment.kind === "image" ? (
              <a className="chat-image-attachment" key={attachment.file_id} href={attachment.previewUrl || `/api/storage/files/${encodeURIComponent(attachment.file_id)}/preview?workspace_id=${encodeURIComponent(workspaceId || "")}`} target="_blank" rel="noreferrer" title="点击查看原图">
                <img src={attachment.previewUrl || `/api/storage/files/${encodeURIComponent(attachment.file_id)}/preview?workspace_id=${encodeURIComponent(workspaceId || "")}`} alt={attachment.name} />
                <span>{attachment.name}</span>
              </a>
            ) : <span className="chat-file-attachment" key={attachment.file_id}><IconDocument size={14} /> {attachment.name}</span>)}
          </div> : null}
        </div></div>
        <div className="message-avatar user">我</div>
      </div>
    );
  }

  return (
    <div className={`message-row assistant${m.status === "error" ? " error" : ""}${m.status === "streaming" ? " streaming" : ""}`} data-testid="chat-assistant">
      <div className="message-avatar agent"><IconSparkle size={14} weight="fill" /></div>
      <div className="message-stack">
        {/* Live tool call chips during streaming */}
        {m.status === "streaming" && m.toolCalls && m.toolCalls.length > 0 && (
          <div className="tool-calls-inline">
            {m.toolCalls.map((tc: InlineToolCall, tci: number) => (
              <span key={tc.call_id || `${tc.tool_id}-${tci}`} className={`live-tool-chip ${tc.status || "running"}`}>
                <span className={`live-tool-dot ${tc.status || "running"}`} />
                {tc.tool_name || toolLabel(tc.tool_id)}
                {tc.summary && <span className="live-tool-summary">{tc.summary.slice(0, 40)}</span>}
              </span>
            ))}
          </div>
        )}
        {/* Completed tool call cards */}
        {m.status !== "streaming" && m.toolCalls && m.toolCalls.length > 0 && (
          <div className="tool-calls-inline">
            {m.toolCalls.map((tc: InlineToolCall, tci: number) => (
              <InlineToolCallCard key={tc.call_id || `${tc.tool_id}-${tci}`} toolCall={tc} seq={tci + 1} />
            ))}
          </div>
        )}
        {m.status === "streaming" ? (
          <div className={`chat-bubble assistant sending-line${m.text ? " has-content" : ""}`}>
            {m.progressText && (
              <div className="ssot-runtime-progress-row" data-testid="ssot-runtime-progress">
                <span className="typing-indicator">
                  <span className="typing-dot" />
                  <span className="typing-dot" />
                  <span className="typing-dot" />
                </span>
                <span className="text-sm wb-progress-text">
                  {m.progressText}
                  {m.stageElapsedMs != null && m.stageElapsedMs > 0 ? (
                    <span className="muted wb-progress-elapsed">
                      {" · 阶段 "}
                      {m.stageElapsedMs >= 1000
                        ? (m.stageElapsedMs / 1000).toFixed(1) + "s"
                        : m.stageElapsedMs + "ms"}
                    </span>
                  ) : null}
                  {m.progressElapsedMs != null && m.progressElapsedMs > 0 ? (
                    <span className="muted wb-progress-elapsed">
                      {" · 本轮 ("}{m.progressElapsedMs >= 1000
                        ? `${(m.progressElapsedMs / 1000).toFixed(1)}s`
                        : `${m.progressElapsedMs}ms`})
                    </span>
                  ) : null}
                </span>
              </div>
            )}
            {m.text ? (
              <StreamingContent text={m.text} />
            ) : !m.progressText ? (
              <div className="wb-thinking-row">
                <span className="typing-indicator"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></span>
                <span className="text-sm muted wb-thinking-label">思考中…</span>
              </div>
            ) : null}
          </div>
        ) : (
          <>
            {(() => {
              const { thinking, body } = parseThinking(m.text);
              const html = body ? renderAssistantHtml(body) : "";
              return (<>
                {thinking && <ThinkingBlock content={thinking} />}
                {html ? (
                  <div className="chat-bubble assistant markdown-body" onClick={handleCodeCopyClick} dangerouslySetInnerHTML={{ __html: highlightCode(html) }} />
                ) : (!m.text) ? (
                  <span className="muted">(空消息)</span>
                ) : null}
              </>);
            })()}
            <ResultInline
              result={m.result}
              fallbackText={sanitizeAssistantText(m.text)}
              onRetryOriginal={lastUserInput ? handleRetry : undefined}
            />
          </>
        )}
        {m.status === "error" && m.error && (
          <div className="msg-error-box">
            <span><IconAlert size={14} /> {humanFailure(m.result?.error_type, m.error ?? "").msg}</span>
          </div>
        )}
      </div>
    </div>
  );
}, (prev, next) => {
  return prev.m.text === next.m.text
    && prev.m.status === next.m.status
    && prev.m.attachments === next.m.attachments
    && prev.m.toolCalls === next.m.toolCalls
    && prev.m.result === next.m.result
    && prev.m.progressText === next.m.progressText
    && prev.m.progressElapsedMs === next.m.progressElapsedMs
    && prev.m.stageElapsedMs === next.m.stageElapsedMs
    && prev.idx === next.idx
    && prev.total === next.total
    && prev.lastUserInput === next.lastUserInput;
});
