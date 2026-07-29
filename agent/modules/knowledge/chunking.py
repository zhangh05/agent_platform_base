# agent/modules/knowledge/chunking.py
"""Parent / child chunking (v1.0.1).

Strategy (per spec § 3):
  1. Structure-first: split on Markdown headings (or page markers for
     PDF, or implicit section boundaries for TXT).
  2. Semantic block protection: never split inside code fences,
     tables, or list sequences.
  3. Length fallback: if a section is too long, split at paragraph
     boundaries; if a paragraph is too long, split at sentence
     boundaries; last resort, character split.
  4. Parent / child:
     - parent: 1200-3000 chars, one per (chapter, section) or implicit
              section
     - child:  400-800 chars target, 180-1200 range, with 60-100
              char overlap (we use 80)
     - each child links to its parent via parent_chunk_id
     - each child has chapter / section / page metadata

`index_text` is built as:
    title + " | " + chapter + " | " + section + " | " + tags + body
This is the string used for retrieval. `content` is the verbatim
chunk body (no artificial prefix).

The chunker is pure (no I/O). The caller persists the result.
"""

from __future__ import annotations

import re
import uuid
from typing import List, Tuple, Optional

from agent.modules.knowledge.schemas import (
    KnowledgeChunk, NormalizedDocument,
    CHILD_TARGET, CHILD_MIN, CHILD_MAX, CHILD_OVERLAP,
    PARENT_MIN, PARENT_MAX,
)


# Headings: # (h1), ## (h2), ### (h3), etc. Optional leading whitespace.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
# Page markers emitted by the PDF parser: <!-- page N -->
_PAGE_RE = re.compile(r"^<!--\s*page\s+(\d+)\s*-->\s*$", re.MULTILINE)
# Code fence
_FENCE_RE = re.compile(r"^```", re.MULTILINE)
# Table separator line: ---|---|---
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def _is_protected_block_start(lines: List[str], i: int) -> Optional[str]:
    """If lines[i:] opens a protected block (code fence, table), return
    the kind; otherwise None.

    Protected blocks are NEVER split. The chunker treats them as
    atomic units.
    """
    line = lines[i]
    if _FENCE_RE.match(line):
        return "fence"
    if _TABLE_ROW_RE.match(line):
        return "table"
    if line.lstrip().startswith(("- ", "* ", "1. ", "2. ", "3. ")):
        return "list"
    return None


def _scan_block(lines: List[str], start: int, kind: str) -> int:
    """Return the line index *after* the protected block (exclusive)."""
    i = start
    if kind == "fence":
        # Find next fence (could be ``` or ~~~)
        i += 1
        while i < len(lines) and not _FENCE_RE.match(lines[i]):
            i += 1
        if i < len(lines):
            i += 1
        return i
    if kind == "table":
        # A "table" is a sequence of | rows.
        i += 1
        while i < len(lines) and _TABLE_ROW_RE.match(lines[i]):
            i += 1
        return i
    if kind == "list":
        i += 1
        while i < len(lines) and lines[i].lstrip().startswith(("- ", "* ", "1. ", "2. ", "3. ")):
            i += 1
        return i
    return start + 1


def _split_into_sections(md: str) -> List[Tuple[str, str, str, str, Optional[int]]]:
    """Split markdown into (chapter, section, subsection, body, page_start) tuples.

    v1.0.2: added H3 (subsection) tracking. The v1.0.1 chunker treated
    H3 as body, which made "section 1.1 OSPF 简介" invisible to
    metadata-based queries. Now:
      - H1 -> chapter
      - H2 -> section
      - H3 -> subsection
    The chunker emits (chapter, section, subsection, body, page_start)
    so the chunk metadata exposes all three levels.
    """
    lines = md.splitlines()
    sections: List[Tuple[str, str, str, str, Optional[int]]] = []
    current_chapter = ""
    current_section = ""
    current_subsection = ""
    current_body: List[str] = []
    current_page: Optional[int] = None
    page_in_section: Optional[int] = None

    def flush():
        body = "\n".join(current_body).rstrip()
        sections.append((current_chapter, current_section,
                          current_subsection, body, page_in_section))

    i = 0
    while i < len(lines):
        line = lines[i]
        m_page = _PAGE_RE.match(line)
        if m_page:
            current_page = int(m_page.group(1))
            if page_in_section is None:
                page_in_section = current_page
            i += 1
            continue
        m_h = _HEADING_RE.match(line)
        if m_h:
            level = len(m_h.group(1))
            title = m_h.group(2).strip()
            if level == 1:
                # New chapter
                if current_body or current_chapter or current_section or current_subsection:
                    flush()
                current_chapter = title
                current_section = ""
                current_subsection = ""
                current_body = []
                page_in_section = current_page
            elif level == 2:
                # New section
                if current_body or current_section or current_subsection:
                    flush()
                current_section = title
                current_subsection = ""
                current_body = []
                page_in_section = current_page
            elif level == 3:
                # New subsection (v1.0.2)
                if current_body:
                    flush()
                current_subsection = title
                current_body = []
                page_in_section = current_page
            else:
                # h4+ is part of body
                current_body.append(line)
            i += 1
            continue
        # Check for protected block start
        kind = _is_protected_block_start(lines, i)
        if kind:
            end = _scan_block(lines, i, kind)
            current_body.extend(lines[i:end])
            i = end
            continue
        current_body.append(line)
        i += 1
    if current_body or current_chapter or current_section or current_subsection:
        flush()
    # Drop empty sections
    return [s for s in sections if s[3]]


def _split_body_by_paragraphs(body: str) -> List[str]:
    """Split a body into paragraph chunks, never breaking code/table/list."""
    if not body.strip():
        return []
    lines = body.splitlines()
    paras: List[str] = []
    buf: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        kind = _is_protected_block_start(lines, i)
        if kind:
            end = _scan_block(lines, i, kind)
            buf.extend(lines[i:end])
            i = end
            continue
        if not line.strip():
            if buf:
                paras.append("\n".join(buf).rstrip())
                buf = []
        else:
            buf.append(line)
        i += 1
    if buf:
        paras.append("\n".join(buf).rstrip())
    return [p for p in paras if p.strip()]


def _split_long_paragraph(p: str, max_len: int) -> List[str]:
    """Hard-split a single long paragraph to fit within max_len."""
    if len(p) <= max_len:
        return [p]
    # Try sentence boundaries (Chinese + English).
    out: List[str] = []
    s = p
    while len(s) > max_len:
        # Find a good split point: prefer .  or 。 or ; or ; within last 200 chars
        window = s[:max_len]
        split_at = -1
        for sep in ["。", ". ", ";", "；", "!", "?", "！", "？", "\n"]:
            idx = window.rfind(sep)
            if idx > max_len // 2:
                split_at = idx + len(sep)
                break
        if split_at <= 0:
            split_at = max_len  # hard cut
        out.append(s[:split_at].rstrip())
        s = s[split_at:].lstrip()
    if s.strip():
        out.append(s.strip())
    return out


def _make_parents(sections: List[Tuple[str, str, str, str, Optional[int]]],
                   source_id: str, source_title: str, scope: str) -> List[KnowledgeChunk]:
    """Create one parent per section, keeping it under PARENT_MAX by
    further splitting at paragraph boundaries if necessary.
    """
    parents: List[KnowledgeChunk] = []
    for ch_idx, (chapter, section, subsection, body, page_start) in enumerate(sections):
        # If body is small, one parent = the whole section.
        if len(body) <= PARENT_MAX:
            pc = KnowledgeChunk(
                chunk_id=f"kch_{source_id[5:21]}_p{ch_idx:04d}",
                source_id=source_id,
                parent_chunk_id="",
                chunk_type="parent",
                chapter=chapter,
                section=section,
                subsection=subsection,
                page_start=page_start,
                page_end=None,
                chunk_index=ch_idx,
                content=body,
                index_text=_build_index_text(source_title, chapter, section, subsection, body),
                token_count=_approx_token_count(body),
                metadata={
                    "scope": scope,
                    "source_title": source_title,
                    "chapter": chapter,
                    "section": section,
                    "subsection": subsection,
                },
            )
            parents.append(pc)
            continue
        # Long section: split at paragraph boundaries.
        paras = _split_body_by_paragraphs(body)
        cur: List[str] = []
        cur_len = 0
        sub_idx = 0
        for p in paras:
            if cur_len + len(p) > PARENT_MAX and cur:
                pc = KnowledgeChunk(
                    chunk_id=f"kch_{source_id[5:21]}_p{ch_idx:04d}_{sub_idx:02d}",
                    source_id=source_id,
                    parent_chunk_id="",
                    chunk_type="parent",
                    chapter=chapter,
                    section=section,
                    subsection=subsection,
                    page_start=page_start,
                    page_end=None,
                    chunk_index=ch_idx * 100 + sub_idx,
                    content="\n\n".join(cur),
                    index_text=_build_index_text(source_title, chapter, section, subsection, "\n\n".join(cur)),
                    token_count=_approx_token_count("\n\n".join(cur)),
                    metadata={
                        "scope": scope,
                        "source_title": source_title,
                        "chapter": chapter,
                        "section": section,
                        "subsection": subsection,
                    },
                )
                parents.append(pc)
                cur = []
                cur_len = 0
                sub_idx += 1
            cur.append(p)
            cur_len += len(p) + 2
        if cur:
            pc = KnowledgeChunk(
                chunk_id=f"kch_{source_id[5:21]}_p{ch_idx:04d}_{sub_idx:02d}",
                source_id=source_id,
                parent_chunk_id="",
                chunk_type="parent",
                chapter=chapter,
                section=section,
                subsection=subsection,
                page_start=page_start,
                page_end=None,
                chunk_index=ch_idx * 100 + sub_idx,
                content="\n\n".join(cur),
                index_text=_build_index_text(source_title, chapter, section, subsection, "\n\n".join(cur)),
                token_count=_approx_token_count("\n\n".join(cur)),
                metadata={
                    "scope": scope,
                    "source_title": source_title,
                    "chapter": chapter,
                    "section": section,
                    "subsection": subsection,
                },
            )
            parents.append(pc)
    return parents


def _make_children(parents: List[KnowledgeChunk], source_id: str,
                    source_title: str, scope: str) -> List[KnowledgeChunk]:
    """Make child chunks for each parent, with overlap.

    Each child is a slice of the parent's content, sized
    CHILD_TARGET (with min CHILD_MIN and max CHILD_MAX), with
    CHILD_OVERLAP between consecutive children.
    """
    children: List[KnowledgeChunk] = []
    for parent in parents:
        body = parent.content
        if not body.strip():
            continue
        # If body is already short, single child.
        if len(body) <= CHILD_MAX:
            cc = KnowledgeChunk(
                chunk_id=f"kch_{source_id[5:21]}_c{parent.chunk_index:05d}_0",
                source_id=source_id,
                parent_chunk_id=parent.chunk_id,
                chunk_type="child",
                chapter=parent.chapter,
                section=parent.section,
                subsection=parent.subsection,
                page_start=parent.page_start,
                page_end=None,
                chunk_index=parent.chunk_index * 1000,
                content=body,
                index_text=_build_index_text(source_title, parent.chapter, parent.section, parent.subsection, body),
                token_count=_approx_token_count(body),
                metadata={
                    "scope": scope,
                    "source_title": source_title,
                    "chapter": parent.chapter,
                    "section": parent.section,
                    "subsection": parent.subsection,
                    "parent_chunk_id": parent.chunk_id,
                },
            )
            children.append(cc)
            continue
        # Walk through body in CHILD_TARGET windows with overlap.
        start = 0
        child_idx = 0
        n = len(body)
        while start < n:
            end = min(start + CHILD_TARGET, n)
            # Try to extend to a paragraph boundary if close.
            if end < n:
                window = body[start:end]
                # Search for last \n\n in last 100 chars
                last_pp = window.rfind("\n\n")
                if last_pp > CHILD_TARGET * 0.6:
                    end = start + last_pp
            slice_text = body[start:end]
            # If still too small (and we have more), extend to CHILD_MAX
            if end - start < CHILD_MIN and end < n:
                end2 = min(start + CHILD_MAX, n)
                slice_text = body[start:end2]
                end = end2
            cc = KnowledgeChunk(
                chunk_id=f"kch_{source_id[5:21]}_c{parent.chunk_index:05d}_{child_idx}",
                source_id=source_id,
                parent_chunk_id=parent.chunk_id,
                chunk_type="child",
                chapter=parent.chapter,
                section=parent.section,
                subsection=parent.subsection,
                page_start=parent.page_start,
                page_end=None,
                chunk_index=parent.chunk_index * 1000 + child_idx,
                content=slice_text,
                index_text=_build_index_text(source_title, parent.chapter, parent.section, parent.subsection, slice_text),
                token_count=_approx_token_count(slice_text),
                metadata={
                    "scope": scope,
                    "source_title": source_title,
                    "chapter": parent.chapter,
                    "section": parent.section,
                    "subsection": parent.subsection,
                    "parent_chunk_id": parent.chunk_id,
                },
            )
            children.append(cc)
            child_idx += 1
            if end >= n:
                break
            start = max(end - CHILD_OVERLAP, start + 1)
    return children


def _approx_token_count(s: str) -> int:
    """Rough token estimate: words for English, characters / 1.5 for CJK."""
    if not s:
        return 0
    cjk_count = sum(1 for c in s if "\u4e00" <= c <= "\u9fff")
    non_cjk = "".join(c for c in s if not ("\u4e00" <= c <= "\u9fff"))
    return int(cjk_count / 1.5) + len(non_cjk.split())


def _build_index_text(title: str, chapter: str, section: str,
                      tags: str, body: str) -> str:
    """Build the retrieval index string.

    Format: title | chapter | section | tags | body
    `body` is kept verbatim — no artificial prefix. We only prepend
    the structural metadata for retrieval boost.
    """
    parts = []
    if title:
        parts.append(title)
    if chapter:
        parts.append(chapter)
    if section:
        parts.append(section)
    if tags:
        parts.append(tags)
    parts.append(body or "")
    return " | ".join(parts)


def chunk_document(doc: NormalizedDocument) -> Tuple[List[KnowledgeChunk],
                                                       List[KnowledgeChunk]]:
    """Public API: chunk a NormalizedDocument.

    Returns (parents, children). Both lists are non-overlapping with
    their own chunk_ids; children reference parents via parent_chunk_id.
    """
    if not doc.source_id:
        # Caller is expected to set source_id before chunking.
        # We tolerate by generating one.
        doc.source_id = "ksrc_" + uuid.uuid4().hex[:16]
    sections = _split_into_sections(doc.normalized_markdown or "")
    parents = _make_parents(sections, doc.source_id, doc.title or "",
                            doc.scope or "workspace")
    children = _make_children(parents, doc.source_id, doc.title or "",
                              doc.scope or "workspace")
    return parents, children
