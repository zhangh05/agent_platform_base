# agent/modules/knowledge/parsers/base.py
"""Common parser plumbing."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from agent.modules.knowledge.schemas import NormalizedDocument, SUPPORTED_FORMATS


class ParserError(Exception):
    """A parser failed in a recoverable way (recorded as warning, not crash)."""


class UnsupportedFormatError(Exception):
    """The format is not supported by the current parser set (e.g. scanned PDF)."""


def _detect_format(path_or_ext: Union[str, Path, bytes]) -> str:
    if isinstance(path_or_ext, (str, Path)):
        s = str(path_or_ext or "").lower()
        if s.endswith((".md", ".markdown")):
            return "md"
        if s.endswith((".html", ".htm")):
            return "html"
        if s.endswith(".txt"):
            return "txt"
        if s.endswith(".docx"):
            return "docx"
        if s.endswith(".pdf"):
            return "pdf"
        return ""
    # bytes: sniff magic bytes
    if isinstance(path_or_ext, (bytes, bytearray)):
        b = bytes(path_or_ext[:32])
        if b.startswith(b"%PDF"):
            return "pdf"
        if b.startswith(b"PK") and b[2:4] == b"\x03\x04":
            # ZIP magic -> likely docx
            return "docx"
        if b.startswith(b"<?xml") or b.startswith(b"<!"):
            return "html"
        if b.startswith(b"<html") or b.startswith(b"<HTML"):
            return "html"
        return ""  # md / txt not sniffable; caller must pass fmt
    return ""


def parse_document(
    source: Union[str, Path, bytes],
    *,
    fmt: str = "",
    title: str = "",
    author: str = "",
    source_type: str = "project_doc",
    scope: str = "workspace",
    language: str = "zh",
    metadata: Optional[dict] = None,
) -> NormalizedDocument:
    """Dispatch to the right parser by format.

    - `source` may be a file path (str/Path) or raw bytes.
    - `fmt` is one of "md", "txt", "html", "docx", "pdf"; if
      empty, inferred from the file extension.
    """
    if not fmt:
        if isinstance(source, (str, Path, bytes, bytearray)):
            fmt = _detect_format(source)
        else:
            fmt = ""
        # md / txt bytes have no magic bytes; default to md
        if not fmt and isinstance(source, (bytes, bytearray)):
            fmt = "md"
    fmt = fmt.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise UnsupportedFormatError(f"unsupported format: {fmt!r}")

    metadata = dict(metadata or {})
    if isinstance(source, (str, Path)):
        path = Path(source)
        raw = path.read_bytes()
        if not title:
            title = path.stem
    else:
        raw = bytes(source)
        if not title:
            title = "untitled"

    if fmt in ("md", "markdown"):
        from . import md as _md
        return _md.parse(raw, title=title, author=author, source_type=source_type,
                          scope=scope, language=language, metadata=metadata)
    if fmt == "txt":
        from . import txt as _txt
        return _txt.parse(raw, title=title, author=author, source_type=source_type,
                          scope=scope, language=language, metadata=metadata)
    if fmt == "html":
        from . import html as _html
        return _html.parse(raw, title=title, author=author, source_type=source_type,
                           scope=scope, language=language, metadata=metadata)
    if fmt == "docx":
        from . import docx as _docx
        return _docx.parse(raw, title=title, author=author, source_type=source_type,
                           scope=scope, language=language, metadata=metadata)
    if fmt == "pdf":
        from . import pdf as _pdf
        return _pdf.parse(raw, title=title, author=author, source_type=source_type,
                          scope=scope, language=language, metadata=metadata)
    raise UnsupportedFormatError(f"unsupported format: {fmt!r}")
