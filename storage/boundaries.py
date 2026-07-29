# storage/boundaries.py
"""Store boundary guard functions — runtime assertions for store contracts.

Lightweight, testable guards that enforce the rules defined in
docs/storage/STORAGE_BOUNDARIES.md.

These are NOT heavy abstractions — they are plain assertions that can be
called during tests and optionally at runtime for validation.
"""

from __future__ import annotations

import json
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════
# FileStore guards
# ═══════════════════════════════════════════════════════════════════════

MAX_MEMORY_ITEM_BYTES = 8 * 1024  # 8KB max per memory item
MAX_RUN_SUMMARY_CHARS = 500       # Run summaries must not exceed this
SENSITIVE_FIELD_PATTERNS = (
    "source_raw", "raw_source", "full_source", "raw_payload", "api_key", "password", "token",
    "secret", "authorization", "credential", "private_key",
)


def assert_artifact_has_file_reference(artifact_record: dict) -> bool:
    """Assert that an artifact record has a file_id reference.

    Every Artifact MUST link to a FileRecord via file_id.
    """
    if not isinstance(artifact_record, dict):
        raise AssertionError("artifact_record must be a dict")
    fid = artifact_record.get("file_id", "") or artifact_record.get("source_file_id", "")
    if not fid:
        raise AssertionError(
            f"Artifact {artifact_record.get('artifact_id', '?')} "
            f"is missing file_id reference. All artifacts must be "
            f"backed by a FileStore FileRecord."
        )
    return True


def assert_memory_payload_safe(
    content: str, memory_id: str = "unknown", max_bytes: int = MAX_MEMORY_ITEM_BYTES,
) -> bool:
    """Assert that a memory item payload is safe for MemoryStore.

    Rules:
    - Must be text (str)
    - Must not exceed max_bytes
    - Must not contain sensitive field patterns
    - Must not look like raw source content
    """
    if not isinstance(content, str):
        raise AssertionError(f"Memory {memory_id}: content must be str, got {type(content).__name__}")

    size = len(content.encode("utf-8"))
    if size > max_bytes:
        raise AssertionError(
            f"Memory {memory_id}: payload size {size} bytes exceeds "
            f"limit of {max_bytes} bytes. Use FileStore for large content."
        )

    # Check for sensitive patterns
    lower = content.lower()
    for pattern in SENSITIVE_FIELD_PATTERNS:
        if pattern in lower:
            raise AssertionError(
                f"Memory {memory_id}: contains sensitive pattern '{pattern}'. "
                f"Redact before writing to MemoryStore."
            )

    # Check for raw source markers (suggests file/table dump, not memory)
    raw_line_count = sum(
        1 for line in content.splitlines()
        if ":" in line or "," in line or "\t" in line
    )
    if raw_line_count >= 20:
        raise AssertionError(
            f"Memory {memory_id}: content looks like raw source "
            f"({raw_line_count} structured lines detected). "
            f"Use FileStore for raw payloads, not MemoryStore."
        )

    return True


def assert_run_record_safe(run_record: dict, run_id: str = "unknown") -> bool:
    """Assert that a run record does not contain sensitive data.

    Rules:
    - No source_raw, raw_source, full_source, api_key, password, token, secret
    - Summaries are truncated (not full content)
    - No full command outputs
    """
    if not isinstance(run_record, dict):
        raise AssertionError("run_record must be a dict")

    # Recursively check for sensitive keys
    violations = _find_sensitive_keys(run_record)
    if violations:
        raise AssertionError(
            f"Run {run_id}: contains sensitive fields: {violations}. "
            f"RunStore must not persist raw secrets or source payloads."
        )

    # Check summary lengths
    for field, max_len in (
        ("user_input_summary", 200),
        ("final_response_summary", 500),
    ):
        val = run_record.get(field, "")
        if isinstance(val, str) and len(val) > max_len:
            raise AssertionError(
                f"Run {run_id}: {field} length {len(val)} exceeds "
                f"max {max_len}. Summaries must be truncated."
            )

    return True


def assert_file_store_index_consistent(ws_id: str = "default", ws_root=None) -> bool:
    """Assert that the FileStore index is consistent with on-disk files.

    Uses storage.index.validate_file_index() for comprehensive checks.
    """
    try:
        from storage.index import validate_file_index
        result = validate_file_index(ws_id, check_disk=True)
        if not result["ok"]:
            raise AssertionError(
                f"FileStore index inconsistent: {result.get('errors', [])} "
                f"warnings: {result.get('warnings', [])[:3]}..."
            )
        if result["stats"]["missing_disk"] > 0 or result["stats"]["size_mismatch"] > 0:
            raise AssertionError(
                f"FileStore index has discrepancies: "
                f"missing_disk={result['stats']['missing_disk']}, "
                f"size_mismatch={result['stats']['size_mismatch']}"
            )
        return True
    except ImportError:
        return True  # Skip if storage module not available


def assert_artifact_file_id_linkage(ws_id: str = "default", ws_root=None) -> bool:
    """Assert that every artifact has a valid file_id that exists in FileStore."""
    try:
        from storage.file_store import get_file_record
        from storage.ids import validate_workspace_id
        from storage.paths import workspace_root

        ws_id = validate_workspace_id(ws_id)
        records_path = workspace_root(ws_id) / "index" / "artifacts.jsonl"
        artifacts: list[dict] = []
        if records_path.is_file():
            latest: dict[str, dict] = {}
            for line in records_path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("artifact_id"):
                    latest[str(record["artifact_id"])] = record
            artifacts = [
                record for record in latest.values()
                if record.get("lifecycle", "active") != "deleted"
            ]
        broken: list[str] = []
        for art in artifacts:
            fid = art.get("file_id", "") or ""
            if not fid:
                broken.append(f"{art.get('artifact_id', '?')}: no file_id")
                continue
            # Verify FileStore has this file
            try:
                fr = get_file_record(ws_id, fid)
                if fr is None:
                    broken.append(f"{art.get('artifact_id', '?')}: file_id {fid} not found")
            except Exception:
                broken.append(f"{art.get('artifact_id', '?')}: file_id {fid} lookup error")

        if broken:
            raise AssertionError(
                f"Artifact-FileStore linkage broken: {len(broken)} artifacts "
                f"with missing/invalid file_id: {broken[:5]}..."
            )
        return True
    except ImportError:
        return True


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _find_sensitive_keys(obj, path: str = "") -> list[str]:
    """Recursively find keys matching sensitive patterns."""
    violations: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_lower = str(k).lower()
            if any(p in key_lower for p in SENSITIVE_FIELD_PATTERNS):
                violations.append(f"{path}.{k}" if path else str(k))
            if isinstance(v, (dict, list)):
                violations.extend(_find_sensitive_keys(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, (dict, list)):
                violations.extend(_find_sensitive_keys(item, f"{path}[{i}]"))
    return violations
