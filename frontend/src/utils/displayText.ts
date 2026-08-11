import { renderMarkdown } from './markdown';
import { formatDate } from './format';

export function sanitizeAssistantText(text: string): string {
  const raw = text ?? "";
  if (
    /I'm (Agent Platform Base|联智中枢),\s*your AI assistant/i.test(raw) ||
    /What would you like to do today\?/i.test(raw)
  ) {
    return [
      "当前可处理：知识库检索、工作区文件、制品查看、数据处理、报告生成和系统诊断。",
      "未接入的业务能力不会假装可用。",
    ].join("\n");
  }
  // Strip tool-call JSON blocks that accidentally leak into display text.
  const cleaned = raw
    .replace(/^\s*(exec|knowledge|workspace|web|memory|agent|browser|system|data|text|report|skill)\.\w+\s*:\s*\{.*\}\s*$/gm, "")
    .replace(/^\{[\s\S]*"canonical_tool_id"[\s\S]*\}$\s*/gm, "")
    .replace(/^\s*<function_calls>[\s\S]*?<\/function_calls>\s*$/gm, "");
  return normalizeAssistantMarkdown(stripThinkTags(cleaned))
    .replace(/^\s*(reasoning|思考过程)\s*[:：][\s\S]*?(?=\n\s*(answer|回答|结论)\s*[:：]|\s*$)/gim, "")
    .replace(/\n{4,}/g, "\n\n")
    .trim();
}

function normalizeAssistantMarkdown(text: string): string {
  let normalized = text
    .replace(/\\r\\n/g, "\n")
    .replace(/\\n/g, "\n")
    .replace(/\\t/g, "  ")
    .replace(/\\`/g, "`");

  normalized = normalized.replace(/(^|\n)(\s*(?:[^|\n]*[：:]\s*)?)((?:\|[^\n]*\|\s*\n?){2,})/g, (_match, prefix: string, intro: string, tableBlock: string) => {
    const table = tableBlock
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .join("\n");
    const lead = intro.trim() ? `${intro.trim()}\n` : "";
    return `${prefix}${lead}${table}`;
  });

  // Split a prose introduction from an inline table header, but never split
  // the first cell from an already valid pipe-prefixed Markdown table.
  normalized = normalized.replace(
    /(^|\n)([^|\n]*\S)\s+(\|[^\n]*\|)\n(\|?\s*:?-{2,})/g,
    "$1$2\n$3\n$4",
  );
  normalized = normalized.replace(/\n{3,}/g, "\n\n");

  return normalized;
}

/**
 * Strip <think>...</think> and <thinking>...</thinking> blocks.
 * Handles nested tags, mid-stream partial tags, and both tag variants.
 */
function stripThinkTags(text: string): string {
  // Unified regex matches both <think> and <thinking> (case-insensitive)
  const re = /<\/?(?:think|thinking)\b[^>]*>/gi;
  let depth = 0;
  let start = -1;
  const parts: string[] = [];
  let lastEnd = 0;

  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const tag = m[0].toLowerCase();
    if (tag.startsWith('</')) {
      if (depth > 0) {
        depth--;
        if (depth === 0) {
          parts.push(text.slice(lastEnd, start));
          lastEnd = m.index + tag.length;
        }
      }
    } else {
      if (depth === 0) {
        start = m.index;
      }
      depth++;
    }
  }
  parts.push(text.slice(lastEnd));
  return parts.join('');
}

/**
 * Streaming-time think tag filter.
 *
 * Unlike stripThinkTags (final text post-processing), this handles
 * partial/mid-stream tags. It maintains a state machine across
 * successive token chunks:
 *
 *   IDLE → OPEN  on <think> or <thinking>
 *   OPEN → DONE  on </think> or </thinking>
 *   DONE → IDLE  next token after close
 *
 * Returns the visible text for this chunk.
 */
export type ThinkFilterState = 'idle' | 'open' | 'done';

export function filterStreamingThink(
  chunk: string,
  state: { mode: ThinkFilterState },
): string {
  if (!chunk) return '';

  const re = /<\/?(?:think|thinking)\b[^>]*>/gi;
  const tags: Array<{ index: number; len: number; isClose: boolean }> = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(chunk)) !== null) {
    tags.push({
      index: m.index,
      len: m[0].length,
      isClose: m[0].toLowerCase().startsWith('</'),
    });
  }

  if (tags.length === 0) {
    // No tags in this chunk — pass through only if not inside think block
    if (state.mode === 'open') return '';
    if (state.mode === 'done') {
      state.mode = 'idle';
    }
    return chunk;
  }

  // Has tags — handle state transitions
  let visible = '';
  let pos = 0;

  for (const tag of tags) {
    if (!tag.isClose) {
      // Opening tag: material before it is visible, then enter think mode
      if (state.mode === 'idle' || state.mode === 'done') {
        visible += chunk.slice(pos, tag.index);
        state.mode = 'open';
      }
    } else {
      // Closing tag: exit think mode, material after it is visible
      if (state.mode === 'open') {
        state.mode = 'done';
        pos = tag.index + tag.len;
      }
    }
  }

  // Remaining text after last tag
  if (state.mode === 'done') {
    visible += chunk.slice(pos);
    state.mode = 'idle';
  } else if (state.mode === 'idle') {
    visible += chunk.slice(pos);
  }
  // if mode === 'open', remaining text is inside think block — discard

  return visible;
}

/** Render assistant message text as safe HTML (Markdown → styled HTML). */
export function renderAssistantHtml(text: string): string {
  const cleaned = sanitizeAssistantText(text);
  if (!cleaned) return '';
  return renderMarkdown(cleaned);
}

export function shortId(id: string | undefined | null, fallback = "—"): string {
  if (!id) return fallback;
  if (id.length <= 14) return id;
  return `${id.slice(0, 8)}…${id.slice(-4)}`;
}

export function formatCompactDate(value: string | undefined | null): string {
  return value ? formatDate(value, "compact") : "";
}

// ───────────────────── Tool display helpers ─────────────────────

/**
 * Shared tool label mapping.  Single source of truth for tool display names.
 */
export function toolLabel(toolId: string): string {
  if (toolId.startsWith("host.")) return "本机工具";
  if (toolId.startsWith("workspace.file.")) return "工作区文件";
  if (toolId.startsWith("workspace.artifact.")) return "工作区制品";
  if (toolId.startsWith("web.")) return "外部资料";
  if (toolId.startsWith("memory.")) return "记忆";
  if (toolId.startsWith("report.") || toolId.startsWith("data.") || toolId.startsWith("text.")) return "输出处理";
  if (toolId.startsWith("agent.")) return "多 Agent";
  if (toolId.startsWith("knowledge.")) return "知识检索";
  if (toolId.startsWith("artifact.")) return "制品操作";
  if (toolId.startsWith("review.")) return "评审流转";
  if (toolId.startsWith("runtime.")) return "运行诊断";
  return "工具调用";
}
