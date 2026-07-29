# agent/modules/knowledge/parsers/html.py
"""HTML parser — BeautifulSoup-based, normalized to Markdown-ish text.

v1.0.1 only goes to plain markdown style (h1/h2/h3 -> `#`/`##`/`###`,
`p` -> paragraph, `pre` -> code fence). It does NOT attempt full
md round-trip; that is the job of a future round.
"""

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
        from bs4 import BeautifulSoup
    except ImportError as e:
        return NormalizedDocument(
            title=title, author=author, source_type=source_type,
            scope=scope, language=language, format="html",
            normalized_markdown="",
            metadata=metadata or {},
            warnings=[f"html_parser_unavailable: {e!r}"],
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    soup = BeautifulSoup(text, "lxml")
    # Drop scripts/styles/noscript.
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()
    out_lines = []
    # Try to surface <title> as a heading.
    if not title:
        t = soup.find("title")
        if t and t.get_text(strip=True):
            title = t.get_text(strip=True)
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6",
                              "p", "pre", "ul", "ol", "table", "blockquote"]):
        name = el.name
        if name and name.startswith("h"):
            level = int(name[1])
            out_lines.append("#" * level + " " + el.get_text(" ", strip=True))
            out_lines.append("")
        elif name == "p":
            txt = el.get_text(" ", strip=True)
            if txt:
                out_lines.append(txt)
                out_lines.append("")
        elif name == "pre":
            txt = el.get_text("\n", strip=False)
            if txt:
                out_lines.append("```")
                out_lines.append(txt)
                out_lines.append("```")
                out_lines.append("")
        elif name in ("ul", "ol"):
            for li in el.find_all("li", recursive=False):
                prefix = "- " if name == "ul" else "1. "
                out_lines.append(prefix + li.get_text(" ", strip=True))
            out_lines.append("")
        elif name == "table":
            for tr in el.find_all("tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                if cells:
                    out_lines.append("| " + " | ".join(cells) + " |")
            out_lines.append("")
        elif name == "blockquote":
            txt = el.get_text(" ", strip=True)
            if txt:
                out_lines.append("> " + txt)
                out_lines.append("")
    md = "\n".join(out_lines).strip()
    metadata = dict(metadata or {})
    metadata.setdefault("format_hint", "html")
    return NormalizedDocument(
        title=title,
        author=author,
        source_type=source_type,
        scope=scope,
        language=language,
        format="html",
        normalized_markdown=md,
        metadata=metadata,
        warnings=[],
    )
