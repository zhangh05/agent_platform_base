# runtime/__init__.py
"""Runtime operational modules — selfcheck, diagnostics, retention, archive."""

from core.runtime.selfcheck import (
    run_selfcheck, SelfcheckResult, SelfcheckIssue, SelfcheckStatus,
)
from core.runtime.retention import (
    RetentionPolicy, RetentionPreview, default_retention_policy,
    preview_retention, apply_retention,
)
from core.runtime.diagnostics import (
    get_diagnostics, DiagnosticReport, ComponentStatus,
)
from core.runtime.archive import (
    ArchivePolicy, ArchivePreview, default_archive_policy,
    preview_archive_candidates, apply_archive,
    get_archive_audits, get_archive_audit,
)
