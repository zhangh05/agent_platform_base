"""
P2b fix coverage: ``agent.runtime.context_compactor.compact_tool_result_content``
must redact forbidden keys at the JSON object level — not by line-based
regex over the raw text.

Background (5-layer audit, v3.10):

  The previous implementation parsed the content with ``json.loads``
  only as a best-effort first step; when the parse succeeded it
  walked dict keys, but the *fallback* path was a line-based regex
  sweep that replaced the entire line with ``[REDACTED]`` whenever
  it saw a forbidden substring. For a single-line JSON input like

    {"username": "abc", "password": "secret123"}

  the regex path matched ``password`` and replaced the whole line
  with ``[REDACTED]``, destroying the JSON envelope.

  The new contract:

    * If the content parses as JSON → redact recursively at the
      object level, keep the structure intact, and ``json.dumps``
      the result.
    * If the content does NOT parse as JSON → fall back to the
      text-scrubbing path. The text-scrubbing path is now the
      *last* resort, not the silent default for the common case.

  These tests cover the JSON path: single-line, multi-line,
  nested, preserved keys, mixed forbidden / preserved.
"""

from __future__ import annotations

import json

import pytest

from agent.runtime.context_compactor import (
    FORBIDDEN_KEYS,
    _PRESERVE_KEYS,
    compact_tool_result_content,
)


# ── A: single-line JSON with forbidden key is structurally redacted ──


def test_single_line_json_redacts_password_keeps_envelope():
    """The classic case: a single-line JSON with a ``password`` key
    must be redacted as ``"password": "[REDACTED]"`` with the
    surrounding JSON envelope intact and ``json.loads`` round-trippable.
    """
    raw = '{"username": "abc", "password": "secret123"}'
    out = compact_tool_result_content(raw)
    parsed = json.loads(out)  # CRITICAL — must round-trip
    assert parsed["username"] == "abc"
    assert parsed["password"] == "[REDACTED]"


# ── B: nested JSON objects are redacted recursively ───────────────────


def test_nested_json_redacts_deep_password():
    raw = json.dumps({
        "ok": True,
        "data": {
            "user": "alice",
            "creds": {
                "password": "hunter2",
                "api_key": "sk-xxx",
            },
        },
    })
    out = compact_tool_result_content(raw)
    parsed = json.loads(out)
    assert parsed["ok"] is True
    assert parsed["data"]["user"] == "alice"
    assert parsed["data"]["creds"]["password"] == "[REDACTED]"
    assert parsed["data"]["creds"]["api_key"] == "[REDACTED]"


# ── C: preserved keys (host, hostname, subnet_mask) are not redacted ──


def test_preserved_keys_are_not_redacted():
    """Whitelisted keys like ``host``/``hostname``/``port`` must
    survive the redaction sweep even though ``host`` is a substring
    of ``hostname``."""
    raw = json.dumps({
        "host": "router-1",
        "hostname": "router-1.example.com",
        "port": 22,
        "subnet_mask": "255.255.255.0",
    })
    out = compact_tool_result_content(raw)
    parsed = json.loads(out)
    assert parsed["host"] == "router-1"
    assert parsed["hostname"] == "router-1.example.com"
    assert parsed["port"] == 22
    assert parsed["subnet_mask"] == "255.255.255.0"


# ── D: non-JSON text falls back to text scrubbing (line-based) ───────


def test_non_json_text_falls_back_to_text_scrub():
    """When the content does NOT parse as JSON (e.g. a plain-text
    log line), the legacy line-based scrubbing must still fire.
    The previous regression was that line-based scrubbing fired
    *also* for JSON inputs — that path is now strictly the fallback.
    """
    raw = "INFO: user logged in with password=hunter2 from 10.0.0.1"
    out = compact_tool_result_content(raw)
    assert "hunter2" not in out, (
        "text fallback must still redact the password value"
    )
    # text fallback may not be parseable as JSON — that's fine.
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_message_compaction_redacts_secret_lines_from_user_summary():
    from agent.runtime.context_compactor import compact_messages

    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "api_key=sk-secret\nplease remember this"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "current task"},
    ]
    compacted, meta = compact_messages(messages, keep_recent=1)
    raw = json.dumps(compacted, ensure_ascii=False)
    assert meta["compacted"] is True
    assert "sk-secret" not in raw
    assert "[REDACTED]" in raw


def test_progress_summary_redacts_secret_lines():
    from agent.runtime.context_compactor import CompactionStrategy, compact_messages

    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "password=hunter2\n继续排查"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "current task"},
    ]
    compacted, meta = compact_messages(
        messages,
        keep_recent=1,
        strategy=CompactionStrategy.LLM_SUMMARY,
    )
    raw = json.dumps(compacted, ensure_ascii=False)
    assert meta["summary_message_created"] is True
    assert "hunter2" not in raw


# ── E: all keys in FORBIDDEN_KEYS are redacted ────────────────────────


@pytest.mark.parametrize("forbidden_key", sorted(FORBIDDEN_KEYS))
def test_every_forbidden_key_is_redacted_in_json(forbidden_key: str):
    """Regression guard: every entry in ``FORBIDDEN_KEYS`` must be
    rewritten to ``[REDACTED]`` in a JSON object — no key may be
    silently missed.
    """
    raw = json.dumps({forbidden_key: "secret-value"})
    out = compact_tool_result_content(raw)
    parsed = json.loads(out)
    assert parsed[forbidden_key] == "[REDACTED]", (
        f"FORBIDDEN_KEYS entry {forbidden_key!r} was not redacted. "
        f"Got: {out!r}"
    )
