# agent/protocol/module_result.py
"""ModuleResult — the standard business output contract for a Capability Module.

v0.8.2 introduction.

Every Capability Module (knowledge, data, reporting, or future
product extensions) returns a ModuleResult when its
service operation completes. The ModuleResult is the **business
output contract** — distinct from ToolResult (the runtime / LLM
contract) and AgentResult.tool_calls (the audit / UI contract).

Shape:
  ok:       bool — success flag (NOT for transport: the caller decides
                   whether to surface "ok" to the LLM, or redact it).
  summary:  str — short human-readable description (one line).
  data:     dict — structured, capability-specific payload. NEVER
                   fabricated; only the module's real output.
  artifacts:list — artifact references produced by the module (e.g.
                   generated output persisted to artifact store).
  warnings: list — non-fatal warnings (e.g. "artifact_save_failed").
  errors:   list — fatal error codes / messages.
  metadata: dict — capability-specific auxiliary info (elapsed_ms,
                   quality_summary, audit, build_commit, ...).

Invariants:
- When `ok=False`, `errors` MUST be non-empty.
- When `artifacts` is non-empty, every entry MUST be a dict with
  at least `artifact_id` and `artifact_type`.
- `data` may be empty `{}` for failure paths.
- `metadata` is opaque to the contract; consumers MUST NOT depend
  on the presence of any specific key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class ModuleResult:
    """Standard business output contract for a Capability Module."""

    ok: bool = False
    summary: str = ""
    data: dict = field(default_factory=dict)
    artifacts: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    # ── Factory methods ──

    @classmethod
    def success(
        cls,
        summary: str,
        data: dict | None = None,
        artifacts: Iterable[dict] | None = None,
        warnings: Iterable[str] | None = None,
        metadata: dict | None = None,
    ) -> "ModuleResult":
        """Build a success ModuleResult. errors is empty by definition."""
        return cls(
            ok=True,
            summary=summary or "",
            data=dict(data or {}),
            artifacts=list(artifacts or []),
            warnings=list(warnings or []),
            errors=[],
            metadata=dict(metadata or {}),
        )

    @classmethod
    def failure(
        cls,
        summary: str,
        errors: Iterable[str] | None = None,
        warnings: Iterable[str] | None = None,
        data: dict | None = None,
        metadata: dict | None = None,
    ) -> "ModuleResult":
        """Build a failure ModuleResult. errors MUST be non-empty."""
        errs = list(errors or [])
        if not errs:
            errs = ["unknown_error"]
        return cls(
            ok=False,
            summary=summary or "",
            data=dict(data or {}),
            artifacts=[],
            warnings=list(warnings or []),
            errors=errs,
            metadata=dict(metadata or {}),
        )

    # ── Serialization ──

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict."""
        return {
            "ok": self.ok,
            "summary": self.summary,
            "data": dict(self.data),
            "artifacts": list(self.artifacts),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "ModuleResult":
        """Deserialize from a dict. Missing fields fall back to defaults
        so the result is always usable, even when an upstream producer
        omits a field by mistake."""
        d = d or {}
        ok = bool(d.get("ok", False))
        # Heuristic: if "ok" is missing, infer from "errors".
        if "ok" not in d and d.get("errors"):
            ok = False
        return cls(
            ok=ok,
            summary=str(d.get("summary", "")),
            data=dict(d.get("data") or {}),
            artifacts=list(d.get("artifacts") or []),
            warnings=list(d.get("warnings") or []),
            errors=list(d.get("errors") or []),
            metadata=dict(d.get("metadata") or {}),
        )

    # ── Helpers ──

    @property
    def is_success(self) -> bool:
        return self.ok and not self.errors

    @property
    def is_failure(self) -> bool:
        return (not self.ok) or bool(self.errors)

    def source_count(self) -> int | None:
        """Return `source_count` from data, or None if missing."""
        sc = self.data.get("source_count")
        return int(sc) if sc is not None else None

    def manual_review_count(self) -> int | None:
        """Return `manual_review_count` from data, or None if missing."""
        mrc = self.data.get("manual_review_count")
        if mrc is not None:
            return int(mrc)
        # Fallback to top-level metadata if data is missing the key
        mrc = self.metadata.get("manual_review_count")
        return int(mrc) if mrc is not None else None
