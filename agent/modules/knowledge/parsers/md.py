# agent/modules/knowledge/parsers/md.py
"""Markdown parser (light normalization)."""

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
        # Normalize CRLF to LF; record as warning.
        text = text.replace("\r\n", "\n")
        warnings.append("normalized_crlf")
    metadata = dict(metadata or {})
    metadata.setdefault("format_hint", "md")
    return NormalizedDocument(
        title=title,
        author=author,
        source_type=source_type,
        scope=scope,
        language=language,
        format="md",
        normalized_markdown=text,
        metadata=metadata,
        warnings=warnings,
    )
