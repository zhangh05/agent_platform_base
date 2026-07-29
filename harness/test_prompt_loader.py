"""Strict prompt registry lookup contract."""

from __future__ import annotations

import pytest

from prompts.loader import (
    PromptNotFoundError,
    get_prompt_by_task,
)


# ── A: get_prompt_by_task raises on missing task ───────────────────────


def test_get_prompt_by_task_raises_on_missing():
    """Missing tasks fail explicitly instead of selecting another prompt."""
    with pytest.raises(PromptNotFoundError) as excinfo:
        get_prompt_by_task("__nonexistent_task_audit_p1__")

    # The exception carries the missing task name for diagnostics.
    assert "__nonexistent_task_audit_p1__" in str(excinfo.value)


def test_get_prompt_by_task_returns_correct_spec_when_present():
    assert get_prompt_by_task("assistant_chat").task == "assistant_chat"
