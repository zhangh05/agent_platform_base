"""Validation for chat attachment references shared by HTTP and WebSocket.

Attachments are always workspace-owned FileStore records.  Client messages
carry only file ids; image bytes are resolved immediately before the model call
and are never persisted in a chat message or run record.
"""

from __future__ import annotations

from typing import Any

MAX_CHAT_ATTACHMENTS = 8
MAX_VISION_IMAGES = 4
MAX_VISION_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


def normalize_chat_attachments(workspace_id: str, raw: Any) -> list[dict[str, Any]]:
    """Return safe attachment metadata or raise ``ValueError``.

    A reference must point to an active managed file in the current workspace.
    The returned shape is deliberately small so it is safe to persist with the
    turn and cannot be used to inject a filesystem path or a data URL.
    """
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > MAX_CHAT_ATTACHMENTS:
        raise ValueError("invalid_attachment_list")

    from storage.file_store import get_file_record

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    vision_count = 0
    for item in raw:
        file_id = str(item.get("file_id") if isinstance(item, dict) else item or "").strip()
        if not file_id or file_id in seen:
            raise ValueError("invalid_attachment_id")
        seen.add(file_id)
        record = get_file_record(workspace_id, file_id)
        if not record or record.get("lifecycle", "active") != "active":
            raise ValueError("attachment_not_found")
        mime_type = str(record.get("mime_type") or "").lower()
        is_image = bool(record.get("binary")) and mime_type in IMAGE_MIME_TYPES
        if is_image:
            vision_count += 1
            if vision_count > MAX_VISION_IMAGES:
                raise ValueError("too_many_images")
            if int(record.get("size_bytes") or 0) > MAX_VISION_IMAGE_BYTES:
                raise ValueError("image_too_large")
        result.append({
            "file_id": file_id,
            "name": str(record.get("original_name") or "附件")[:160],
            "mime_type": mime_type,
            "size_bytes": int(record.get("size_bytes") or 0),
            "kind": "image" if is_image else "file",
        })
    return result
