# storage/policy.py
"""Storage policy constants."""

from __future__ import annotations

# ── Size limits ──────────────────────────────────────────────────────

MAX_UPLOAD_BYTES = 200 * 1024 * 1024          # 200 MB

# ── File kind classification ─────────────────────────────────────────

BINARY_KINDS = frozenset({
    "pdf", "docx", "xlsx", "pptx",
    "zip", "tar", "gz", "bz2", "7z",
    "png", "jpg", "jpeg", "gif", "svg", "webp",
})

TEXT_KINDS = frozenset({
    "text", "config", "markdown", "json", "yaml", "xml",
    "csv", "html", "log", "script", "diff",
})

# ── Logical type → expected file kinds ───────────────────────────────

ALLOWED_UPLOAD_KINDS = frozenset({
    "text", "binary", "pdf", "docx", "xlsx",
    "pptx", "markdown", "config", "json", "yaml", "xml", "csv", "html", "log",
    "zip", "tar", "gz", "png", "jpg", "jpeg", "gif", "svg", "webp",
})

# ── Sensitivity ──────────────────────────────────────────────────────

SENSITIVITY_LEVELS = ("public", "internal", "confidential", "restricted")
