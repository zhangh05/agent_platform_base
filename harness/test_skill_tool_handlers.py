# Skill tool handler contracts.
"""Tests for capability-first skill tool handlers."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.tools.general_tools.skill_tools import (
    handle_skill_list,
    handle_skill_load,
    handle_skill_find,
    handle_skill_inspect,
)
from core.tools.schemas import ToolInvocation


def _inv(tool_id, args=None):
    return ToolInvocation(tool_id=tool_id, arguments=args or {}, workspace_id="default")


def test_skill_list_returns_manifests_not_filesystem_prompts():
    out = handle_skill_list(_inv("skill.manage"))
    assert out.get("ok")
    results = out.get("results", [])
    assert results
    first = results[0]
    assert "skill_id" in first
    assert "capability_ids" in first
    assert "tool_ids" in first
    assert "skill_prompt" not in first


def test_skill_load_returns_capability_contract():
    out = handle_skill_load(_inv("skill.manage", {"skill_name": "workspace_read"}))
    assert out.get("ok")
    assert "workspace_read" in out.get("capability_ids", [])
    assert "workspace.file" in out.get("tool_ids", [])
    assert "skill_prompt" not in out


def test_skill_find_searches_manifests():
    out = handle_skill_find(_inv("skill.manage", {"query": "workspace"}))
    assert out.get("ok")
    results = out.get("results", [])
    ids = {r["skill_id"] for r in results}
    assert "workspace_read" in ids


def test_skill_inspect_returns_manifest_not_content():
    out = handle_skill_inspect(_inv("skill.manage", {"skill_name": "workspace_read"}))
    assert out.get("ok")
    assert "skill_id" in out
    assert "capability_ids" in out
    assert "content" not in out  # no SKILL.md content
    assert "skill_prompt" not in out
