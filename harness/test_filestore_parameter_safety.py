"""Managed attachment parameter failures must not escape as handler exceptions."""

from core.tools.general_tools.filestore_tools import (
    handle_file_extract_document_image,
    handle_file_extract_document_images,
)
from core.tools.schemas import ToolInvocation


def test_document_image_invalid_indexes_are_normal_failures():
    inv = ToolInvocation(tool_id="workspace.file", workspace_id="ws_file_params", arguments={"file_id": "file-x", "image_index": "not-a-number"})
    result = handle_file_extract_document_image(inv)
    assert result["ok"] is False
    assert result["error"] == "invalid_image_index"

    batch = ToolInvocation(tool_id="workspace.file", workspace_id="ws_file_params", arguments={"file_id": "file-x", "start_index": "not-a-number"})
    result = handle_file_extract_document_images(batch)
    assert result["ok"] is False
    assert result["error"] == "invalid_image_batch_range"
