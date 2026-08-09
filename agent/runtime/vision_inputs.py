"""Turn-scoped conversion of validated image attachments to LLM content.

The function is deliberately invoked at the provider boundary.  This keeps
base64 image data out of browser payloads, metadata, traces, session history,
and persisted run records.
"""

from __future__ import annotations

import base64
from typing import Any


def build_vision_content(attachments: Any, workspace_id: str) -> tuple[list[dict], list[str]]:
    """Build OpenAI-compatible ``image_url`` parts from validated references."""
    if not isinstance(attachments, list) or not workspace_id:
        return [], []
    from backend.core.chat_attachments import IMAGE_MIME_TYPES, MAX_VISION_IMAGE_BYTES
    from storage.file_store import get_file_record, resolve_file_path

    parts: list[dict] = []
    warnings: list[str] = []
    for item in attachments:
        if not isinstance(item, dict) or item.get("kind") != "image":
            continue
        file_id = str(item.get("file_id") or "")
        try:
            record = get_file_record(workspace_id, file_id)
            mime_type = str((record or {}).get("mime_type") or "").lower()
            if not record or record.get("lifecycle", "active") != "active" or mime_type not in IMAGE_MIME_TYPES:
                warnings.append(f"附件 {file_id} 已不可用，未发送给模型。")
                continue
            if int(record.get("size_bytes") or 0) > MAX_VISION_IMAGE_BYTES:
                warnings.append(f"图片 {record.get('original_name') or file_id} 超过 5 MB，未发送给模型。")
                continue
            data = resolve_file_path(workspace_id, file_id).read_bytes()
            if len(data) > MAX_VISION_IMAGE_BYTES:
                warnings.append(f"图片 {record.get('original_name') or file_id} 超过 5 MB，未发送给模型。")
                continue
            encoded = base64.b64encode(data).decode("ascii")
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
            })
        except (OSError, ValueError):
            warnings.append(f"附件 {file_id} 读取失败，未发送给模型。")
    return parts, warnings
