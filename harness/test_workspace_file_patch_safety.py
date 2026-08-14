"""The workspace patch tool must not silently apply stale hunks."""

from core.tools.general_tools.file_tools import handle_file_patch
from core.tools.schemas import ToolInvocation


def _inv(workspace_id: str, patch_text: str) -> ToolInvocation:
    return ToolInvocation(
        tool_id="workspace.file",
        workspace_id=workspace_id,
        arguments={"action": "patch", "filepath": "files/data/config.txt", "patch_text": patch_text},
    )


def test_patch_rejects_stale_context_without_modifying_file(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    target = tmp_path / "workspaces" / "test_ws" / "files" / "data" / "config.txt"
    target.parent.mkdir(parents=True)
    target.write_text("current=value\n", encoding="utf-8")

    result = handle_file_patch(_inv("test_ws", "@@ -1 +1 @@\n-old=value\n+new=value\n"))

    assert result["ok"] is False
    assert "context does not match" in result["error"]
    assert target.read_text(encoding="utf-8") == "current=value\n"


def test_patch_applies_when_context_matches(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    target = tmp_path / "workspaces" / "test_ws" / "files" / "data" / "config.txt"
    target.parent.mkdir(parents=True)
    target.write_text("old=value\n", encoding="utf-8")

    result = handle_file_patch(_inv("test_ws", "@@ -1 +1 @@\n-old=value\n+new=value\n"))

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "new=value\n"
