# agent/modules/knowledge/parsers/__init__.py
"""Document parsers for v1.0.1 ingestion.

Each parser takes raw bytes or a file path and returns a
NormalizedDocument. Supported formats:
  - md / markdown:  text + heading preservation (light normalization)
  - txt:            line-based fallback
  - html / htm:     BeautifulSoup-based extraction
  - docx:           python-docx based
  - pdf:            pdfplumber based; scanned PDFs return
                    error=unsupported_ocr
"""
from . import md, txt, html, docx, pdf  # noqa: F401
from .base import parse_document, ParserError, UnsupportedFormatError  # noqa: F401

__all__ = [
    "parse_document",
    "ParserError",
    "UnsupportedFormatError",
]
