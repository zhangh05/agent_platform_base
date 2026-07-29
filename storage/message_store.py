"""SessionMessageStore — canonical persisted chat messages for a session.

This store writes complete user/assistant messages:
  - `workspaces/<ws>/sessions/<sid>/messages/<run_id>.user.json`
  - `workspaces/<ws>/sessions/<sid>/messages/<run_id>.assistant.json`
  - Content is redaction-safe (no keys, no full configs).
  - Content > ARTIFACT_THRESHOLD (50 KB) is written as an artifact
    instead, and the message carries an `artifact_ref`.

These records are the durable source consumed by UI and conversation history.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage.redaction import redact_text
from storage.ids import validate_workspace_id, validate_session_id
from storage.workspace_store import ensure_workspace

from storage.paths import get_workspace_root

def _ws_root() -> Path:
    return get_workspace_root()

# Content > this value is stored as an artifact, not inline.
ARTIFACT_THRESHOLD = 50_000  # characters, not bytes — 50K chars ≈ 150KB for CJK  # 50 KB

# Stable message_id shape. Pinned so the frontend dedup contract is
# invariant across reads.
USER_MSG_ID = "{run_id}:user"
ASSISTANT_MSG_ID = "{run_id}:assistant"


# NOTE: raises ValueError vs _safe_rid silently strips — P2-12
def _safe_run_id(run_id: str) -> str:
    """Return a run_id with only path-safe chars; raises if empty."""
    safe = re.sub(r'[^a-zA-Z0-9_:.-]', '', str(run_id))
    if safe != str(run_id) or not safe:
        raise ValueError(f"Invalid run_id: {run_id!r}")
    return safe


def _safe_rid(run_id: str) -> str:
    """Return a run_id stripped to path-safe chars."""
    return re.sub(r'[^a-zA-Z0-9_:.-]', '', str(run_id))


class SessionMessageStore:
    """Chat-history projection read/write API for a single session.

    Stateless; constructed per-call. The constructor validates
    `ws_id` and `session_id` to prevent path traversal.
    """

    def __init__(self, session_id: str, ws_id: str = "default"):
        self.ws_id = validate_workspace_id(ws_id)
        self.session_id = validate_session_id(session_id)
        ensure_workspace(self.ws_id)

    # ── Paths ──

    def _session_path(self) -> Path:
        safe = _safe_run_id(self.session_id)
        return _ws_root() / self.ws_id / "sessions" / f"{safe}.json"

    def _messages_dir(self) -> Path:
        """Directory holding independent full-message files."""
        safe = _safe_run_id(self.session_id)
        return _ws_root() / self.ws_id / "sessions" / safe / "messages"

    def _msg_path(self, run_id: str, role: str) -> Path:
        """File path for a single full message."""
        rid = _safe_rid(run_id)
        # ':' is part of the logical message_id contract but is forbidden in
        # Windows filenames. Keep storage naming platform-neutral and rebuild
        # the logical id from the record when reading.
        return self._messages_dir() / f"{rid}.{role}.json"

    # ── Write ──

    def write_message(self, run_id: str, role: str, content: str,
                      metadata: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Persist a FULL user or assistant message independently.

        Returns the message_id, or None if the content is empty.

        Content is redacted before writing. If content still exceeds
        ARTIFACT_THRESHOLD after redaction, it is saved as a redacted artifact
        and the message stores an `artifact_ref` instead. Large messages no
        longer bypass redaction.
        """
        if not content or not content.strip():
            return None
        if role == "assistant":
            content = _sanitize_assistant_content(content)
            if not content or not content.strip():
                return None

        rid = _safe_rid(run_id)
        safe_content = redact_text(str(content))

        msg_dir = self._messages_dir()
        msg_dir.mkdir(parents=True, exist_ok=True)

        meta = dict(metadata or {})
        meta.setdefault("run_id", rid)
        meta.setdefault("session_id", self.session_id)
        meta.setdefault("workspace_id", self.ws_id)
        meta.setdefault("redacted", True)

        # Large redacted content → artifact reference
        if len(safe_content.encode("utf-8", errors="replace")) > ARTIFACT_THRESHOLD:
            art_info = self._write_artifact(safe_content, role, rid)
            size = len(safe_content.encode("utf-8", errors="replace"))
            record = {
                "role": role,
                "run_id": rid,
                "session_id": self.session_id,
                "content": "",
                "artifact_ref": {
                    "artifact_id": art_info.get("artifact_id", ""),
                    "file_id": art_info.get("file_id", ""),
                    "artifact_type": art_info.get("artifact_type", "message_large_content"),
                    "size_bytes": size,
                    "redacted": True,
                },
                "metadata": meta,
            }
        else:
            record = {
                "role": role,
                "run_id": rid,
                "session_id": self.session_id,
                "content": safe_content,
                "metadata": meta,
            }

        msg_path = self._msg_path(rid, role)
        _atomic_write(msg_path, record)

        msg_id = f"{rid}:{role}"
        return msg_id

    # ── Read ──

    def exists(self) -> bool:
        """True if the session record exists on disk."""
        path = self._session_path()
        return path.is_file()

    def get_messages(self) -> List[Dict[str, Any]]:
        """Return the full chat-history read-model projection of the session."""
        return self._read_full_messages()

    def get_history_window(self, k: int = 8) -> List[Dict[str, Any]]:
        """Return up to k most recent messages (LLM context window).

        Messages are in chronological order (oldest first).
        """
        all_msgs = self.get_messages()
        if k <= 0 or len(all_msgs) <= k:
            return all_msgs
        return all_msgs[-k:]

    def get_message_count(self) -> int:
        return len(self.get_messages())

    # ── Full-message internals ──

    def _read_full_messages(self) -> List[Dict[str, Any]]:
        """Read messages from the `messages/` directory.

        Returns empty list if no full-message files exist.
        """
        msg_dir = self._messages_dir()
        if not msg_dir.is_dir():
            return []

        msgs: List[Dict[str, Any]] = []
        for f in sorted(msg_dir.glob("*.json")):
            try:
                record = json.loads(f.read_text(encoding="utf-8"))
                role = record.get("role", "")
                if role not in ("user", "assistant"):
                    continue

                record_run_id = str(record.get("run_id", ""))
                msg_id = f"{record_run_id}:{role}"

                m: Dict[str, Any] = {
                    "message_id": msg_id,
                    "session_id": record.get("session_id", self.session_id),
                    "role": role,
                    "content": _message_content(role, record.get("content", "")),
                    "created_at": record.get("metadata", {}).get("created_at", ""),
                    "run_id": record_run_id,
                }

                # Carry artifact_ref if present (large content)
                if record.get("artifact_ref"):
                    art = record["artifact_ref"]
                    m["artifact_ref"] = art
                    m["content"] = (
                        f"[内容过大 ({art.get('size_bytes', 0) // 1024} KB)，"
                        f"请通过制品 API 获取: artifact_id={art.get('artifact_id', '')}]"
                    )

                # Carry metadata for frontend rendering
                meta = record.get("metadata", {})
                if meta:
                    m["metadata"] = {
                        k: meta[k] for k in ("run_id", "intent", "status",
                                              "capability", "quality_summary",
                                              "manual_review_count", "trace_id",
                                              "llm_metadata", "redacted")
                        if k in meta
                    }

                msgs.append(m)
            except Exception:
                import logging
                logging.getLogger(__name__).warning("Cannot read message record %s", f, exc_info=True)
                continue

        msgs.sort(key=_message_sort_key)
        return msgs

    # ── Artifact storage for large content ──

    def _write_artifact(self, content: str, role: str, run_id: str) -> dict:
        """Write content > ARTIFACT_THRESHOLD as a managed file reference."""
        try:
            from storage.file_store import write_agent_output
            from storage.reference_index import add_reference

            title = f"Large message: {run_id}:{role}"
            rec = write_agent_output(
                workspace_id=self.ws_id,
                content=content,
                logical_type="message_large_content",
                file_kind="txt",
                title=title,
                sensitivity="internal",
                run_id=run_id,
                source="message_store",
                metadata={
                    "session_id": self.session_id,
                    "role": role,
                    "storage_managed": True,
                },
            )
            if rec:
                from storage.artifact_metadata_store import create_artifact_metadata

                artifact = create_artifact_metadata(
                    workspace_id=self.ws_id,
                    file_record=rec,
                    artifact_type="message_large_content",
                    title=title,
                    scope="session",
                    sensitivity="internal",
                    run_id=run_id,
                    session_id=self.session_id,
                    source="message_store",
                    metadata={
                        "session_id": self.session_id,
                        "role": role,
                        "storage_managed": True,
                    },
                )
                add_reference(self.ws_id, rec.file_id, "message",
                              f"{run_id}:{role}", "large_content",
                              metadata={"artifact_id": artifact["artifact_id"],
                                        "session_id": self.session_id})
                return {
                    "artifact_id": artifact["artifact_id"],
                    "file_id": rec.file_id,
                    "artifact_type": "message_large_content",
                    "title": title,
                }
        except Exception:
            pass
        return {}


def _message_sort_key(message: Dict[str, Any]) -> tuple:
    """Stable chronological chat ordering with user before assistant per run."""
    role_rank = 0 if message.get("role") == "user" else 1
    return (
        message.get("created_at") or "",
        message.get("run_id") or "",
        role_rank,
        message.get("message_id") or "",
    )


def _message_content(role: str, content: str) -> str:
    if role == "assistant":
        return _sanitize_assistant_content(content)
    return content


def _sanitize_assistant_content(content: str) -> str:
    """Strip provider reasoning from assistant text before display/context use.

    Fail-closed: if the sanitizer cannot be loaded or raises, return an
    empty string rather than the unsanitized content. Reasoning blocks
    may contain tool-call JSON or provider-internal data that must never
    leak to the user or back into the LLM context window.
    """
    if not content:
        return ""
    text = str(content)
    text = re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<reasoning\b[^>]*>.*?</reasoning>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"(?ism)^\s*(reasoning|思考过程)\s*[:：].*?(?=\n\s*(answer|回答|结论)\s*[:：]|\Z)", "", text)
    text = re.sub(r"(?i)</?(think|reasoning)\b[^>]*>", "", text)
    return text.strip()


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically with unique tmp + os.replace.

    The previous fixed `.tmp` path could be clobbered by concurrent writes.
    This function fail-closed: callers see an exception rather than silently
    losing persistence.
    """
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    _write_text_atomically(path, payload)


def _write_text_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def get_message_store(session_id: str = None, ws_id: str = "default") -> "SessionMessageStore":
    """Convenience factory for callers that need a SessionMessageStore instance."""
    return SessionMessageStore(session_id=session_id, ws_id=ws_id)
