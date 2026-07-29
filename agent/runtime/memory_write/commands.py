"""Deterministic handling for explicit user memory control commands."""

from __future__ import annotations

import hashlib
import re
from typing import Any


_FORGET = re.compile(r"(?:不要记住|别记住|忘掉|忘记|删除(?:这条|刚才的)?记忆|不再记得)\s*(.*)", re.I)
_REMEMBER = re.compile(
    r"(?:请记住|记住|以后(?:都|请|要|不要|别)?|下次(?:请|要|不要|别)?|"
    r"我希望你|我要求你|默认(?:要|使用)?|不要再|别再|always\b|never\b|please remember\b)",
    re.I,
)


def parse_memory_command(user_input: str) -> dict[str, Any] | None:
    """Return explicit remember/forget intent from user text only."""
    text = re.sub(r"\s+", " ", str(user_input or "")).strip()
    if not text:
        return None
    forget = _FORGET.search(text)
    if forget:
        return {
            "action": "forget",
            "query": (forget.group(1) or "").strip(" ，。.!！?")[:300],
            "reason": "explicit_user_forget_command",
        }
    if not _REMEMBER.search(text):
        return None
    # Avoid weak conversational phrases that are not durable instructions.
    if re.fullmatch(r"以后(?:再说|看看|讨论|处理)[。.!！?]?", text, re.I):
        return None
    return {
        "action": "remember",
        "content": text[:1000],
        "summary": text[:160],
        "memory_type": "core_rule",
        "memory_key": _rule_key(text),
        "reason": "explicit_user_memory_command",
    }


def apply_memory_command(
    command: dict[str, Any],
    *,
    workspace_id: str,
    session_id: str,
    task_id: str,
) -> dict[str, Any]:
    from storage.memory_governance import MemoryRecord, MemoryStore, MemoryWriteGate, expire_memory

    store = MemoryStore()
    if command.get("action") == "forget":
        query = str(command.get("query") or "").strip()
        candidates = store.search(workspace_id, query, limit=10) if query else list(reversed(store.list_all(workspace_id)))
        expired = []
        for item in candidates:
            memory_id = item.get("memory_id") if isinstance(item, dict) else getattr(item, "memory_id", "")
            record = store.get(workspace_id, str(memory_id or ""))
            if record is None or record.status != "active" or record.memory_type != "core_rule":
                continue
            if expire_memory(workspace_id, record.memory_id).get("ok"):
                expired.append(record.memory_id)
            if not query:
                break
        return {"ok": True, "action": "forget", "expired_memory_ids": expired}

    content = str(command.get("content") or "").strip()
    record = MemoryRecord(
        workspace_id=workspace_id,
        session_id=session_id,
        task_id=task_id,
        scope="workspace",
        memory_type="core_rule",
        status="active",
        source="user",
        content=content,
        summary=str(command.get("summary") or content)[:200],
        confidence=1.0,
        citations=[{"task_id": task_id, "source": "user_input"}],
        created_by="user",
        metadata={
            "memory_key": command.get("memory_key"),
            "authority": "explicit_user",
            "authority_rank": 100,
            "extraction_reason": command.get("reason"),
            "evidence_source": "user_input",
            "generation_origin": "user_memory_command",
        },
    )
    return MemoryWriteGate(store).write(record)


def _rule_key(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.lower())
    topics = (
        ("user.testing_policy", ("测试", "pytest", "test", "全量")),
        ("project.memory_policy", ("记忆", "memory")),
        ("project.baseline_authority", ("基线", "权威", "baseline")),
        ("user.git_policy", ("分支", "合并", "提交", "同步", "branch", "commit")),
        ("user.legacy_content_policy", ("旧代码", "旧内容", "兼容", "legacy")),
        ("user.response_style", ("回答", "回复", "简洁", "详细", "中文")),
    )
    for key, markers in topics:
        if any(marker in normalized for marker in markers):
            return key
    return "rule:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
