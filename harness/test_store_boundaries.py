# harness/test_store_boundaries.py
"""P2-A: Store boundary contract tests.

Coverage:
  - FileStore: index consistency
  - Artifact: file_id linkage requirement
  - MemoryStore: payload size, raw source detection, sensitive field rejection
  - RunStore: sensitive field rejection, summary truncation
"""

import pytest
from storage import boundaries


# ═══════════════════════════════════════════════════════════════════════
# Artifact guards
# ═══════════════════════════════════════════════════════════════════════


class TestArtifactFileReference:
    def test_requires_file_id(self):
        with pytest.raises(AssertionError, match="missing file_id"):
            boundaries.assert_artifact_has_file_reference({
                "artifact_id": "art_001",
                "title": "test",
            })

    def test_accepts_file_id(self):
        assert boundaries.assert_artifact_has_file_reference({
            "artifact_id": "art_001",
            "file_id": "file_abc123",
        }) is True

    def test_accepts_source_file_id(self):
        assert boundaries.assert_artifact_has_file_reference({
            "artifact_id": "art_001",
            "source_file_id": "file_xyz789",
        }) is True

    def test_rejects_non_dict(self):
        with pytest.raises(AssertionError):
            boundaries.assert_artifact_has_file_reference("not_a_dict")


# ═══════════════════════════════════════════════════════════════════════
# MemoryStore guards
# ═══════════════════════════════════════════════════════════════════════


class TestMemoryPayloadSafe:
    def test_accepts_normal_memory(self):
        assert boundaries.assert_memory_payload_safe(
            "The user prefers concise Markdown summaries.",
            memory_id="mem_001",
        ) is True

    def test_rejects_non_string(self):
        with pytest.raises(AssertionError, match="must be str"):
            boundaries.assert_memory_payload_safe(b"binary data", memory_id="mem_bin")

    def test_rejects_large_content(self):
        large = "x" * (boundaries.MAX_MEMORY_ITEM_BYTES + 1)
        with pytest.raises(AssertionError, match="exceeds limit"):
            boundaries.assert_memory_payload_safe(large, memory_id="mem_large")

    def test_rejects_content_with_secrets(self):
        with pytest.raises(AssertionError, match="sensitive pattern"):
            boundaries.assert_memory_payload_safe(
                '{"api_key": "sk-1234567890abcdef", "model": "gpt-4"}',
                memory_id="mem_secret",
            )

    def test_rejects_raw_source_content(self):
        raw_text = "\n".join(f"field_{i}: value_{i}" for i in range(40))
        with pytest.raises(AssertionError, match="looks like raw source"):
            boundaries.assert_memory_payload_safe(raw_text, memory_id="mem_raw")

    def test_accepts_memory_with_source_reference(self):
        """Mentioning source summaries is fine, dumping raw source is not."""
        assert boundaries.assert_memory_payload_safe(
            "The user's last analysis focused on the onboarding checklist.",
            memory_id="mem_ref",
        ) is True


# ═══════════════════════════════════════════════════════════════════════
# RunStore guards
# ═══════════════════════════════════════════════════════════════════════


class TestRunRecordSafe:
    def test_accepts_normal_run_record(self):
        assert boundaries.assert_run_record_safe({
            "run_id": "run_001",
            "user_input_summary": "analyze uploaded file",
            "final_response_summary": "file analysis complete",
            "status": "success",
        }) is True

    def test_rejects_api_key_in_record(self):
        with pytest.raises(AssertionError, match="sensitive fields"):
            boundaries.assert_run_record_safe({
                "run_id": "run_001",
                "api_key": "sk-secret",
            })

    def test_rejects_password_in_record(self):
        with pytest.raises(AssertionError, match="sensitive fields"):
            boundaries.assert_run_record_safe({
                "run_id": "run_001",
                "settings": {"password": "admin123"},
            })

    def test_rejects_raw_source_field(self):
        with pytest.raises(AssertionError, match="sensitive fields"):
            boundaries.assert_run_record_safe({
                "run_id": "run_001",
                "source_raw": "customer_name,private_note\nalice,confidential",
            })

    def test_rejects_token_in_nested(self):
        with pytest.raises(AssertionError, match="sensitive fields"):
            boundaries.assert_run_record_safe({
                "run_id": "run_001",
                "metadata": {"auth": {"token": "bearer xyz"}},
            })

    def test_rejects_long_summary(self):
        with pytest.raises(AssertionError, match="exceeds max"):
            boundaries.assert_run_record_safe({
                "run_id": "run_001",
                "user_input_summary": "x" * 300,
            })

    def test_accepts_artifact_refs(self):
        """Artifact references are NOT sensitive — they are structural metadata."""
        assert boundaries.assert_run_record_safe({
            "run_id": "run_001",
            "artifact_refs": [{"artifact_id": "art_001", "file_id": "file_001"}],
        }) is True


# ═══════════════════════════════════════════════════════════════════════
# Integration tests — validate current code
# ═══════════════════════════════════════════════════════════════════════


class TestCurrentStorageIntegration:
    def test_artifact_store_uses_current_source_dirs(self):
        from artifacts.store import ALLOWED_SOURCE_DIRS
        assert "files/data" in ALLOWED_SOURCE_DIRS

    def test_filestore_backed_upload_paths(self):
        from storage.file_store import _LOGICAL_TYPE_TO_DIR
        result = _LOGICAL_TYPE_TO_DIR.get("user_upload", "")
        assert result == "files/data"


class TestRunStoreWriteSafety:
    """Verify run_store.py does not write sensitive fields."""

    def test_write_run_record_excludes_sensitive(self):
        """write_run_record serialized output must not include sensitive fields."""
        import inspect
        from storage import run_record_store as rs

        source = inspect.getsource(rs.write_run_record)

        # Verify the core safe fields are present in the record dict
        core_fields = [
            "run_id", "workspace_id", "session_id", "status",
            "user_input_summary", "final_response_summary",
            "redaction_applied", "created_at",
        ]
        for field in core_fields:
            assert field in source, f"Expected field '{field}' in run record serialization"

        # Verify no sensitive pattern assignments
        for sensitive in ("source_config", "raw_config", "api_key"):
            # These should not appear as record dict keys
            assert f'"{sensitive}"' not in source or 'get("' + sensitive in source, (
                f"Sensitive field '{sensitive}' should not be a direct key in run record"
            )

    def test_run_store_redacts_content(self):
        """Run records must apply redaction to summaries.

        redact_text uses regex SECRET_PATTERNS — it matches known secret
        patterns (password word, sk- keys with 20+ chars, api_key=, etc.).
        """
        from storage.redaction import redact_text

        # API key pattern: sk- followed by 20+ alphanumeric chars
        result = redact_text("API key: sk-abcdefghijklmnopqrstuvwxyz0123456789")
        assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in result
        assert "REDACTED_SECRET" in result or "sk-" not in result

        # Password pattern: password followed by space and non-space
        result = redact_text("password secret123")
        assert "secret123" not in result

        # Truncation is done separately via string slicing in run_store.py
        result = redact_text("safe text without secrets")
        assert result == "safe text without secrets"

    def test_run_store_redacts_local_absolute_paths(self):
        """Persisted diagnostics must not leak local filesystem paths."""
        from storage.redaction import redact_text, redact_value

        unix_text = 'File "/Users/zhangh01/.workbuddy/python/lib/json/__init__.py", line 1'
        win_text = r'File "C:\Users\zhangh01\AppData\Local\Temp\run.py", line 1'

        assert "/Users/" not in redact_text(unix_text)
        assert "C:\\Users\\" not in redact_text(win_text)

        redacted = redact_value({"events": [{"traceback": unix_text}], "error": win_text})
        serialized = str(redacted)
        assert "/Users/" not in serialized
        assert "C:\\Users\\" not in serialized
