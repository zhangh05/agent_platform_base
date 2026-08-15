import { renderMarkdown } from './markdown';
import { formatDate } from './format';

export function sanitizeAssistantText(text: string): string {
  const raw = text ?? "";
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

  // Split a prose introduction from an inline table header, but never split
  // the first cell from an already valid pipe-prefixed Markdown table. Keep
  // existing line boundaries untouched: consuming trailing `\s*` here used
  // to remove the blank line between consecutive tables and merge the next
  // heading into the previous table's final cell.
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
  const re = /<\/?(?:think|thinking|reasoning)\b[^>]*>/gi;
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

export type ThinkFilterState = 'idle' | 'open' | 'done';
type ThinkFilterRuntimeState = { mode: ThinkFilterState; pending?: string };

const THINK_TAG = /<\/?(?:think|thinking|reasoning)\b[^>]*>/i;
const THINK_TAG_PREFIXES = ['<think', '<thinking', '<reasoning', '</think', '</thinking', '</reasoning'];

function trailingThinkTagPrefix(source: string): string {
  const start = source.lastIndexOf('<');
  if (start < 0) return '';
  const suffix = source.slice(start);
  const normalized = suffix.toLowerCase();
  if (THINK_TAG_PREFIXES.some((prefix) => prefix.startsWith(normalized))) {
    return suffix;
  }
  return /^<\/?(?:think|thinking|reasoning)\b[^>]*$/i.test(suffix) ? suffix : '';
}

/**
 * Streaming-time think tag filter.
 *
 * Provider chunks are arbitrary byte/token boundaries, so an opening or closing
 * think tag can be split across several chunks. Keep an incomplete tag suffix in
 * state until it can be classified; never expose a potential reasoning marker.
 */
export function filterStreamingThink(
  chunk: string,
  state: ThinkFilterRuntimeState,
): string {
  if (!chunk) return '';

  if (state.mode === 'done') state.mode = 'idle';
  let source = `${state.pending || ''}${chunk}`;
  state.pending = '';
  let visible = '';

  while (source) {
    const tag = THINK_TAG.exec(source);
    if (tag) {
      const before = source.slice(0, tag.index);
      if (state.mode !== 'open') visible += before;
      state.mode = tag[0].toLowerCase().startsWith('</') ? 'idle' : 'open';
      source = source.slice(tag.index + tag[0].length);
      continue;
    }

    const pending = trailingThinkTagPrefix(source);
    const stable = source.slice(0, source.length - pending.length);
    if (state.mode !== 'open') visible += stable;
    state.pending = pending;
    break;
  }

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
