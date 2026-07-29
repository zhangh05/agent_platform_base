# agent/modules/knowledge/parsers/txt.py
"""Plain-text fallback parser."""

from __future__ import annotations

from typing import Optional

from agent.modules.knowledge.schemas import NormalizedDocument


def parse(
    raw: bytes,
    *,
    title: str = "",
    author: str = "",
    source_type: str = "project_doc",
    scope: str = "workspace",
    language: str = "zh",
    metadata: Optional[dict] = None,
) -> NormalizedDocument:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    warnings = []
    if "\r\n" in text:
        text = text.replace("\r\n", "\n")
        warnings.append("normalized_crlf")
    # Convert plain text to "naive" markdown: paragraph-by-paragraph
    # blank line separation. No headings inferred (we don't fabricate
    # chapter names; the chunker will treat it as one section).
    paras = [p.strip() for p in text.split("\n\n")]
    md_lines = []
    for p in paras:
        if not p:
            continue
        # Wrap long lines naively (no transform).
        md_lines.append(p)
        md_lines.append("")
    md_text = "\n".join(md_lines)
    metadata = dict(metadata or {})
    metadata.setdefault("format_hint", "txt")
    return NormalizedDocument(
        title=title,
        author=author,
        source_type=source_type,
        scope=scope,
        language=language,
        format="txt",
        normalized_markdown=md_text,
        metadata=metadata,
        warnings=warnings,
    )
