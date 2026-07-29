/**
 * Lightweight Markdown-to-HTML renderer for LLM chat messages.
 *
 * Handles: code blocks (```), inline code (`), tables, headers,
 * bold/italic, lists, blockquotes, horizontal rules, links.
 *
 * No external dependencies. Token-budget friendly (~3KB).
 */

// ── Helper: escape HTML (except what we intentionally render) ──

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Block javascript:, data:, and other dangerous URL schemes. */
const DANGEROUS_URL_RE = /^(javascript|data|vbscript):/i;
function safeUrl(url: string): string {
  const trimmed = url.trim();
  return DANGEROUS_URL_RE.test(trimmed) ? "#blocked" : trimmed;
}

function safeHref(url: string, label: string): string {
  // `renderInline` receives HTML-escaped text, so escaping the URL again would
  // turn query separators into `&amp;amp;`.
  return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
}

function normalizeMarkdown(text: string): string {
  return text
    .replace(/\r\n?/g, '\n')
    .replace(/&lt;br\s*\/?&gt;/gi, '\n')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{4,}/g, '\n\n\n');
}

type RenderedTable = {
  html: string;
  consumed: number;
  scrollable: boolean;
};

function renderSoftBreakLines(lines: string[]): string {
  // Escape `|` so it renders as a literal pipe character instead of leaking
  // raw into the HTML (browsers would otherwise render it as-is, which looks
  // like a broken table cell boundary in natural-language paragraphs).
  const escaped = escapeHtml(lines.join('\n')).replace(/\|/g, '&#124;');
  return renderInline(escaped).replace(/\n/g, '<br />');
}

// ── Inline formatting ──

function renderInline(raw: string): string {
  let text = raw;
  const protectedSegments: string[] = [];
  const protect = (html: string): string => {
    const index = protectedSegments.push(html) - 1;
    return `\uE000${index}\uE001`;
  };

  // Protect code and links before emphasis parsing. Otherwise identifiers such
  // as `ins_abc` and URLs with underscores are incorrectly split into <em>.
  // Note: callers escape HTML before invoking renderInline, so we preserve the
  // protected inline-code content as-is to avoid double-escaping.
  text = text.replace(/`([^`]+)`/g, (_: string, code: string) =>
    protect(`<code>${code}</code>`)
  );
  text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
    (_: string, label: string, url: string) =>
      protect(safeHref(safeUrl(url), label))
  );

  // Bold + italic combined
  text = text.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');

  // Bold
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

  // Italic
  text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');

  // Strikethrough
  text = text.replace(/~~(.+?)~~/g, '<s>$1</s>');

  text = text.replace(/\uE000(\d+)\uE001/g, (_: string, index: string) =>
    protectedSegments[Number(index)] || ''
  );

  return text;
}

// ── Table rendering ──

function isTableSeparator(line: string): boolean {
  return /^\|?[\s:-]+\|[\s|:-]+\|?$/.test(line.trim());
}

function isChineseSectionHeading(line: string): boolean {
  return /^[一二三四五六七八九十]+、\S/.test(line.trim());
}

function splitTableRow(line: string): string[] {
  const trimmed = line.trim();
  const withoutLeading = trimmed.startsWith('|') ? trimmed.slice(1) : trimmed;
  const withoutEdges = withoutLeading.endsWith('|') ? withoutLeading.slice(0, -1) : withoutLeading;
  return withoutEdges.split('|').map(c => c.trim());
}

function isTableBodyLine(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed || !trimmed.includes('|') || isTableSeparator(trimmed)) return false;
  return splitTableRow(trimmed).length >= 2;
}

function isBulletLike(text: string): boolean {
  return /^[·•*-]\s*/.test(text.trim());
}

function renderTableCell(raw: string): string {
  return renderInline(escapeHtml(raw)).replace(/\n/g, '<br />');
}

function isReportTable(headers: string[]): boolean {
  const joined = headers.join('|');
  return /模块|类别/.test(headers[0] || '') &&
    (/题量|数量|占比|分值/.test(joined) || /趋势|变化|说明|考点|要点/.test(joined));
}

function lastContentIndex(row: string[]): number {
  for (let index = row.length - 1; index >= 0; index--) {
    if (row[index]?.trim()) return index;
  }
  return Math.max(0, row.length - 1);
}

function appendToReportRow(row: string[], text: string): void {
  const targetIndex = Math.max(lastContentIndex(row), Math.min(row.length - 1, 2));
  row[targetIndex] = [row[targetIndex], text].filter(Boolean).join('\n');
}

function renderReportCards(headers: string[], rows: string[][]): string {
  const metaIndexes = headers
    .map((header, index) => (/题量|数量|占比|分值/.test(header) ? index : -1))
    .filter(index => index > 0);
  const bodyIndexes = headers
    .map((header, index) => (/趋势|变化|说明|考点|要点|备注/.test(header) ? index : -1))
    .filter(index => index > 0);

  const cards = rows.map((row) => {
    const title = row[0] || '项目';
    const meta = (metaIndexes.length ? metaIndexes : [1])
      .map(index => row[index])
      .filter(Boolean)
      .join(' · ');
    const body = (bodyIndexes.length ? bodyIndexes : row.map((_, index) => index).filter(index => index > 0 && !metaIndexes.includes(index)))
      .map(index => row[index])
      .filter(Boolean)
      .join('\n');

    return [
      '<article class="report-card">',
      `<div class="report-card-head"><span class="report-card-title">${renderInline(escapeHtml(title))}</span>`,
      meta ? `<span class="report-card-meta">${renderInline(escapeHtml(meta))}</span>` : '',
      '</div>',
      body ? `<div class="report-card-body">${renderTableCell(body)}</div>` : '',
      '</article>',
    ].join('');
  }).join('');

  return `<div class="report-grid">${cards}</div>`;
}

function renderTable(lines: string[]): RenderedTable | null {
  if (lines.length < 2) return null;

  const headerLine = lines[0];
  const separatorLine = lines[1];

  // Check if it's a table (header with |, separator with |---|---|)
  if (!isTableBodyLine(headerLine) || !isTableSeparator(separatorLine)) {
    return null;
  }

  const headers = splitTableRow(headerLine).filter(Boolean);
  if (headers.length < 2) return null;
  const reportTable = isReportTable(headers);

  const bodyRows: string[][] = [];
  let consumed = 2;
  while (consumed < lines.length) {
    const current = lines[consumed];
    const trimmed = current.trim();

    if (reportTable && bodyRows.length > 0 && isBulletLike(trimmed)) {
      appendToReportRow(bodyRows[bodyRows.length - 1], trimmed);
      consumed++;
      continue;
    }

    if (!isTableBodyLine(current)) break;

    const cells = splitTableRow(lines[consumed]);
    const normalized = cells.length >= headers.length
      ? cells.slice(0, headers.length)
      : [...cells, ...Array(headers.length - cells.length).fill('')];
    const nonEmptyIndexes = normalized
      .map((cell, index) => cell.trim() ? index : -1)
      .filter(index => index >= 0);

    if (
      bodyRows.length > 0 &&
      nonEmptyIndexes.length === 1 &&
      nonEmptyIndexes[0] === 0 &&
      isBulletLike(normalized[0])
    ) {
      appendToReportRow(bodyRows[bodyRows.length - 1], normalized[0]);
    } else {
      bodyRows.push(normalized);
    }
    consumed++;
  }

  const alignments: string[] = splitTableRow(separatorLine).map(a =>
    a.startsWith(':') && a.endsWith(':') ? 'center' :
    a.endsWith(':') ? 'right' :
    'left'
  );

  if (reportTable) {
    return { html: renderReportCards(headers, bodyRows), consumed, scrollable: false };
  }

  const thead = `<thead><tr>${headers
    .map((h, i) => `<th style="text-align:${alignments[i] || 'left'}">${renderInline(escapeHtml(h))}</th>`)
    .join('')}</tr></thead>`;

  const tbody = bodyRows.length
    ? `<tbody>${bodyRows.map(cells => {
        return `<tr>${headers.map((_, i) => {
          const cell = cells[i] ?? '';
          return `<td style="text-align:${alignments[i] || 'left'}">${renderTableCell(cell)}</td>`;
        }).join('')}</tr>`;
      }).join('')}</tbody>`
    : '';

  return { html: `<table>${thead}${tbody}</table>`, consumed, scrollable: true };
}

function renderPipeParagraph(line: string): string {
  const cells = splitTableRow(line).filter(Boolean);
  if (cells.length >= 3) {
    const [title, meta, ...body] = cells;
    return [
      '<div class="pipe-card">',
      `<span class="pipe-card-title">${renderInline(escapeHtml(title))}</span>`,
      `<span class="pipe-card-meta">${renderInline(escapeHtml(meta))}</span>`,
      `<span class="pipe-card-body">${renderInline(escapeHtml(body.join(' · ')))}</span>`,
      '</div>',
    ].join('');
  }
  if (cells.length === 2) {
    return [
      '<div class="pipe-card">',
      `<span class="pipe-card-title">${renderInline(escapeHtml(cells[0]))}</span>`,
      `<span class="pipe-card-body">${renderInline(escapeHtml(cells[1]))}</span>`,
      '</div>',
    ].join('');
  }
  if (cells.length >= 2) {
    return `<p class="pipe-row">${cells.map(c => renderInline(escapeHtml(c))).join('<span class="pipe-row-separator"> · </span>')}</p>`;
  }
  return `<p>${renderInline(escapeHtml(line.trim()))}</p>`;
}

function parseInsightLine(line: string): { label: string; text: string; emphasized: boolean } | null {
  // v3.17: require the label to actually be bold-wrapped (**Label：**) to avoid
  // treating natural-language paragraphs like "流量路径：共享链路 — PE1..."
  // as structured label-value pairs in an insight grid.
  const match = line.trim().match(/^\*\*([^：:]{2,28}[：:])\*\*\s+(.+)$/);
  if (!match) return null;
  return { label: match[1], text: match[2], emphasized: true };
}

function renderInsightList(lines: string[]): string | null {
  const items = lines.map(parseInsightLine);
  if (items.some(item => !item)) return null;
  // v3.17: require ≥2 bold-labeled lines — short un-emphasized runs no longer trigger.
  if (items.length < 2) return null;
  return `<div class="insight-list">${items.map((item) => [
    '<div class="insight-item">',
    `<span class="insight-label">${renderInline(escapeHtml(item!.label))}</span>`,
    `<span class="insight-text">${renderInline(escapeHtml(item!.text))}</span>`,
    '</div>',
  ].join('')).join('')}</div>`;
}

// ── Main renderer ──

export function renderMarkdown(text: string): string {
  if (!text) return '';

  const lines = normalizeMarkdown(text).split('\n');
  const out: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // ── Code blocks (fenced) ──
    if (line.trimStart().startsWith('```')) {
      const lang = line.trimStart().slice(3).trim();
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith('```')) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing ```
      const codeText = escapeHtml(codeLines.join('\n'));
      const language = lang.replace(/[^a-zA-Z0-9_+-]/g, '') || 'plaintext';
      // AgentWorkbench enhances this canonical shape with syntax highlighting
      // and the copy toolbar. Keeping one code-block contract avoids the old
      // double-frame appearance and makes unlabelled blocks work as well.
      out.push(`<pre><code class="language-${escapeHtml(language)}">${codeText || ' '}</code></pre>`);
      continue;
    }

    // ── Headers (trim leading whitespace, # or ## or ###) ──
    const trimmed = line.trimStart();
    const headerMatch = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (headerMatch) {
      const level = Math.min(headerMatch[1].length, 6);
      out.push(`<h${level}>${renderInline(escapeHtml(headerMatch[2].trim()))}</h${level}>`);
      i++;
      continue;
    }

    if (isChineseSectionHeading(trimmed)) {
      out.push(`<h3>${renderInline(escapeHtml(trimmed))}</h3>`);
      i++;
      continue;
    }

    // ── Horizontal rule ──
    if (/^(---|\*\*\*|___)\s*$/.test(line.trim())) {
      out.push('<hr />');
      i++;
      continue;
    }

    // ── Blockquote ──
    if (line.startsWith('>')) {
      const quoteLines: string[] = [];
      while (i < lines.length && lines[i].startsWith('>')) {
        quoteLines.push(lines[i].slice(1).trimStart());
        i++;
      }
      out.push(`<blockquote><p>${renderSoftBreakLines(quoteLines)}</p></blockquote>`);
      continue;
    }

    // ── Table detection (multi-line) ──
    const remaining = lines.slice(i);
    const tableResult = renderTable(remaining);
    if (tableResult) {
      out.push(tableResult.scrollable
        ? `<div class="markdown-table-scroll">${tableResult.html}</div>`
        : tableResult.html);
      i += tableResult.consumed;
      continue;
    }
    // Orphan | line without valid table — render as paragraph
    if (line.trimStart().startsWith('|') && !tableResult) {
      out.push(renderPipeParagraph(line));
      i++;
      continue;
    }

    // ── Task list ──
    if (/^[\s]*[-*+]\s+\[[ xX]\]\s+/.test(line)) {
      out.push('<ul class="task-list">');
      while (i < lines.length && /^[\s]*[-*+]\s+\[[ xX]\]\s+/.test(lines[i])) {
        const match = lines[i].match(/^[\s]*[-*+]\s+\[([ xX])\]\s+(.+)$/)!;
        const checked = match[1].toLowerCase() === 'x';
        out.push(`<li class="task-list-item${checked ? ' is-complete' : ''}"><span class="task-checkbox" aria-hidden="true">${checked ? '&#10003;' : ''}</span><span>${renderInline(escapeHtml(match[2]))}</span></li>`);
        i++;
      }
      out.push('</ul>');
      continue;
    }

    // ── Unordered list ──
    if (/^[\s]*[-*+]\s+/.test(line)) {
      out.push('<ul>');
      while (i < lines.length && /^[\s]*[-*+]\s+/.test(lines[i])) {
        const item = lines[i].replace(/^[\s]*[-*+]\s+/, '');
        out.push(`<li>${renderInline(escapeHtml(item))}</li>`);
        i++;
      }
      out.push('</ul>');
      continue;
    }

    // ── Ordered list (only if starts at 1, or there are ≥2 consecutive numbered lines) ──
    const orderedMatch = line.match(/^(\d+)\.\s+/);
    if (orderedMatch) {
      // Peek ahead to see if this is part of a real list (≥2 consecutive items)
      let listCount = 0;
      let peek = i;
      while (peek < lines.length && /^\d+\.\s+/.test(lines[peek])) {
        listCount++;
        peek++;
      }
      if (listCount >= 2 || orderedMatch[1] === '1') {
        out.push('<ol>');
        while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
          const item = lines[i].replace(/^\d+\.\s+/, '');
          out.push(`<li>${renderInline(escapeHtml(item))}</li>`);
          i++;
        }
        out.push('</ol>');
        continue;
      }
      // Single numbered line (not a real list) — render as paragraph and advance
      out.push(`<p>${renderInline(escapeHtml(line.trim()))}</p>`);
      i++;
      continue;
    }

    // ── Empty line → soft break (skip consecutive blanks) ──
    if (line.trim() === '') {
      i++;
      continue;
    }

    // ── Regular paragraph ──
    const paraLines: string[] = [];
    while (i < lines.length && lines[i].trim() !== '' &&
           !lines[i].trimStart().startsWith('```') && !lines[i].trimStart().startsWith('#') &&
           !isChineseSectionHeading(lines[i].trimStart()) &&
           !lines[i].trimStart().startsWith('>') && !lines[i].trimStart().startsWith('|') &&
           !/^[\s]*[-*+]\s+/.test(lines[i]) && !/^\d+\.\s+/.test(lines[i]) &&
           !/^(---|\*\*\*|___)\s*$/.test(lines[i].trim())) {
      paraLines.push(lines[i]);
      i++;
    }
    if (paraLines.length) {
      out.push(renderInsightList(paraLines) || `<p>${renderSoftBreakLines(paraLines)}</p>`);
    }
  }

  return out.join('\n');
}
