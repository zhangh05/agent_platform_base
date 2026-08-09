"""Artifact reads must accurately identify previews versus full content."""

from types import SimpleNamespace

from core.tools.general_tools.artifact_tools import handle_artifact_read_content_safe
from core.tools.schemas import ToolInvocation


def test_artifact_preview_is_not_claimed_as_complete(monkeypatch):
    artifact = SimpleNamespace(sensitivity="internal", artifact_type="knowledge_doc", title="long")
    monkeypatch.setattr("artifacts.store.get_artifact", lambda *_args: artifact)
    monkeypatch.setattr("artifacts.store.read_artifact_content", lambda *_args, **_kwargs: "x" * 2001)
    result = handle_artifact_read_content_safe(ToolInvocation(
        tool_id="workspace.artifact", workspace_id="ws_artifact", arguments={"artifact_id": "art-x"},
    ))
    assert result["ok"] is True
    assert result["content_complete"] is False
    assert result["truncated"] is True
