# storage/policy.py
"""Storage policy constants."""

from __future__ import annotations

# ── Size limits ──────────────────────────────────────────────────────

MAX_UPLOAD_BYTES = 200 * 1024 * 1024          # 200 MB
MAX_TEXT_PREVIEW_BYTES = 512 * 1024            # 512 KB
MAX_INLINE_CONTENT_BYTES = 50 * 1024          # 50 KB (above this → artifact)
ARTIFACT_THRESHOLD_BYTES = 50 * 1024          # same as above

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
    "markdown", "json", "yaml", "xml", "csv", "html", "log",
    "zip", "tar", "gz", "png", "jpg", "jpeg", "gif", "svg", "webp",
})

# ── Retention ────────────────────────────────────────────────────────

DEFAULT_RETENTION = "workspace_default"
RETENTION_POLICIES = frozenset({
    "workspace_default",   # follow workspace-level policy
    "session",             # delete when session ends
    "run",                 # delete when run ends
    "permanent",           # never auto-delete
    "temporary",           # delete after 24h or on GC
})

# ── Sensitivity ──────────────────────────────────────────────────────

SENSITIVITY_LEVELS = ("public", "internal", "confidential", "restricted")
