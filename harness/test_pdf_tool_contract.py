"""PDF validation failures must retain the common tool-result contract."""

from core.tools.general_tools.pdf_tools import handle_pdf_extract_text
from core.tools.schemas import ToolInvocation


def test_non_pdf_returns_a_normal_tool_error(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    workspace = "ws_pdf_contract"
    target = tmp_path / workspace / "bad.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"not-a-pdf")

    result = handle_pdf_extract_text(ToolInvocation(
        tool_id="pdf.extract", workspace_id=workspace,
        arguments={"filepath": "bad.pdf"},
    ))
    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"] == "not a PDF file"
