# artifacts/classifier.py
"""Artifact classifier — determine artifact_type, sensitivity, tags from content."""

import re


def classify_file(path: str = "", content: str = "") -> dict:
    """Classify a file's artifact_type, sensitivity, and tags. Returns dict."""
    result = {
        "artifact_type": "unknown",
        "mime_type": "text/plain",
        "file_ext": "",
        "sensitivity": "internal",
        "probable_vendor": "",
        "line_count": 0,
        "contains_secret": False,
        "tags": [],
    }

    if path:
        result["file_ext"] = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        ext = result["file_ext"]
        result["mime_type"] = _ext_mime(ext)

    if content:
        from artifacts.redaction import contains_secret
        lines = content.strip().split("\n")
        result["line_count"] = len(lines)
        result["contains_secret"] = contains_secret(content)
        result["probable_vendor"] = _guess_vendor(content)

        if re.search(r'^\s*(<!doctype\s+html|<html[\s>])', content, re.I):
            result["artifact_type"] = "report"
            result["mime_type"] = "text/html"
            result["file_ext"] = "html"
            result["tags"].append("html")

        # Generic structured/text input detection
        if result["file_ext"] != "html" and re.search(r'(\{|\[|,|\t|:)', content, re.I):
            result["artifact_type"] = "input_data"
            result["sensitivity"] = "sensitive"
            result["tags"].append("data")

        # Log detection
        if re.search(r'(WARNING|ERROR|INFO|DEBUG|TRACE)', content, re.I) and len(lines) > 10:
            result["artifact_type"] = "output_data"
            result["tags"].append("log")

        # Output marker
        if re.search(r'(analysis_output|generated_output|tool_output)', content, re.I):
            result["artifact_type"] = "output_data"
            result["sensitivity"] = "sensitive"

        # Secret override
        if result["contains_secret"]:
            result["sensitivity"] = "secret"

    return result


def _ext_mime(ext: str) -> str:
    m = {
        "json": "application/json", "yaml": "text/yaml", "yml": "text/yaml",
        "cfg": "text/plain", "conf": "text/plain", "txt": "text/plain",
        "svg": "image/svg+xml", "png": "image/png",
        "md": "text/markdown", "pdf": "application/pdf", "docx": "application/docx",
        "log": "text/plain", "csv": "text/csv", "html": "text/html",
    }
    return m.get(ext, "text/plain")


def _guess_vendor(content: str) -> str:
    return ""
