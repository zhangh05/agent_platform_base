"""Layered memory tool handlers using the governed write path."""

from core.tools.schemas import ToolInvocation

from core.tools.general_tools.shared import _error_inv, _ok
from storage.ids import validate_workspace_id
from agent.runtime.utils import now_iso


def _caller_workspace(inv: ToolInvocation) -> str:
    """Extract validated workspace_id from caller context. No default fallback."""
    requested = str(inv.arguments.get("workspace_id") or "").strip()
    caller = str(inv.workspace_id or "").strip()
    if caller and requested and caller != requested:
        raise ValueError(f"workspace_id mismatch: caller={caller!r}, requested={requested!r}")
    workspace_id = caller or requested or ""
    if not workspace_id:
        raise ValueError("workspace_id is required — no default fallback")
    validate_workspace_id(workspace_id)
    return workspace_id


def _get_store(ws_id: str):
    from storage.memory_governance import MemoryStore
    return MemoryStore()


def _via_gate(title: str, content: str, ws_id: str, source: str = "llm_tool",
              memory_type: str = "knowledge_note", scope: str = "workspace",
              session_id: str = "", task_id: str = "",
              citations: list = None, tags: list = None) -> dict:
    """Write memory through MemoryWriteGate. Gate decides status."""
    from storage.memory_governance import MemoryRecord, MemoryWriteGate
    rec = MemoryRecord(
        workspace_id=ws_id, session_id=session_id, task_id=task_id,
        scope=scope, memory_type=memory_type,
        status="pending",  # Gate decides final status
        source="subagent" if source == "subagent" else "agent_suggestion",
        content=content[:2000], summary=title[:200],
        confidence=0.5,  # Neutral default; gate adjusts via _auto_confirm
        citations=citations or [], created_by=source,
        redacted=True,
    )
    gate = MemoryWriteGate()
    return gate.write(rec)


def handle_memory_search(inv: ToolInvocation) -> dict:
    """Search stored memories by keyword. Auto-injection happens at session start."""
    query = (inv.arguments.get("query") or "").strip()
    try:
        ws = _caller_workspace(inv)
        store = _get_store(ws)
        # Try store-level search first, fall back to list+filter
        try:
            results = store.search(ws, query, limit=10)
        except (AttributeError, NotImplementedError):
            results = store.list_retrievable(ws, limit=30)
            if query:
                q = query.lower()
                results = [r for r in results if q in (r.get("content", "") + r.get("summary", "")).lower()]
        safe = [{
            "memory_id": r.get("memory_id", ""),
            "title": r.get("title", ""),
            "summary": r.get("summary", "")[:200],
            "content": r.get("content", "")[:300],
            "status": r.get("status", ""),
            "memory_type": r.get("memory_type", ""),
        } for r in results[:10]]
        return _ok(inv, "", {
            "results": safe, "count": len(safe),
            "_hint": f"找到 {len(safe)} 条相关记忆。记忆在会话启动时自动注入，search 用于精确查询。",
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_memory_create(inv: ToolInvocation) -> dict:
    """Create a memory. All tool-created memories go through gate as pending."""
    args = inv.arguments
    title = str(args.get("title", "")).strip()
    content = str(args.get("content", "")).strip()
    if not content:
        return _error_inv(inv, "content is required")
    if not title:
        title = content[:80]
    try:
        ws = _caller_workspace(inv)
        sid = str(args.get("session_id", ""))
        is_sub = bool(args.get("is_subagent", False))
        source = "subagent" if is_sub else "llm_tool"
        result = _via_gate(
            title=title, content=content, ws_id=ws,
            source=source,
            memory_type=str(args.get("memory_type", "knowledge_note")),
            scope=str(args.get("scope", "workspace")),
            session_id=sid,
            tags=list(args.get("tags") or []),
        )
        memory_id = result.get("memory_id", "")
        status = result.get("status", "pending")
        if not memory_id:
            return _error_inv(inv, "memory write blocked by policy")
        if result.get("rejected"):
            reason = result.get("error", result.get("summary", "unknown"))
            return _ok(inv, "", {
                "ok": False, "memory_id": memory_id, "status": "rejected",
                "_hint": f"记忆被门控拒绝：{reason}。不要包含密码/密钥/API Key，确保内容有价值。",
            })
        return _ok(inv, "", {
            "memory_id": memory_id, "status": status,
            "_hint": (
                f"记忆已记录（{status}状态）。"
                + ("待用户确认后生效。" if status == "pending" else "")
            ),
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_memory_review(inv: ToolInvocation) -> dict:
    """Review pending memories — those waiting for user confirmation."""
    limit = int((inv.arguments.get("limit") or 10))
    try:
        ws = _caller_workspace(inv)
        store = _get_store(ws)
        all_recs = store.list_all(ws)
        pending = [
            r for r in all_recs
            if getattr(r, "status", "") == "pending"
        ]
        pending.sort(key=lambda r: getattr(r, "created_at", ""), reverse=True)
        items = [{
            "memory_id": getattr(r, "memory_id", ""),
            "title": getattr(r, "title", "") or getattr(r, "summary", ""),
            "content": (getattr(r, "content", "") or "")[:200],
            "confidence": getattr(r, "confidence", 0.5),
            "source": getattr(r, "source", ""),
            "memory_type": getattr(r, "memory_type", ""),
            "created_at": getattr(r, "created_at", ""),
        } for r in pending[:limit]]
        return _ok(inv, "", {
            "ok": True, "items": items, "total_pending": len(pending),
            "returned": len(items),
            "_hint": (
                f"有 {len(pending)} 条待确认记忆。"
                + (f" 已返回 {len(items)} 条。" if len(pending) > limit else "")
                + " 高 confidence 的建议更可靠。用 confirm 激活，delete 移除。"
            ) if items else "没有待确认的记忆。",
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_memory_list(inv: ToolInvocation) -> dict:
    args = inv.arguments
    try:
        ws = _caller_workspace(inv)
        store = _get_store(ws)
        results = store.list_retrievable(ws, limit=int(args.get("limit", 20)))
        session_filter = args.get("session_id", "")
        summaries = []
        for r in results:
            if session_filter and r.get("session_id", "") != session_filter:
                continue
            summaries.append({
                "memory_id": r.get("memory_id", ""),
                "title": r.get("title", ""),
                "summary": r.get("summary", "")[:200],
                "status": r.get("status", ""),
                "memory_type": r.get("memory_type", ""),
                "scope": r.get("scope", ""),
                "created_at": r.get("created_at", ""),
                "tags": (r.get("tags") or [])[:5],
            })
        return _ok(inv, "", {
            "results": summaries, "count": len(summaries),
            "_hint": f"列出 {len(summaries)} 条记忆。用 confirm 激活 pending 状态记忆。",
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_memory_confirm(inv: ToolInvocation) -> dict:
    """Confirm a pending memory (typically from frontend approval UI)."""
    args = inv.arguments
    memory_id = str(args.get("memory_id", "")).strip()
    if not memory_id:
        return _error_inv(inv, "memory_id is required")
    try:
        ws = _caller_workspace(inv)
        from storage.memory_governance import confirm_memory
        result = confirm_memory(ws, memory_id)
        return _ok(inv, "", {"memory_id": memory_id, **result})
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_memory_get_profile(inv: ToolInvocation) -> dict:
    try:
        ws = _caller_workspace(inv)
        store = _get_store(ws)
        results = store.list_retrievable(ws, memory_type="profile", limit=1)
        if not results:
            return _ok(inv, "No profile found", {
                "explicit_preferences": {},
                "inferred_preferences": {},
                "tool_usage_stats": {},
                "updated_at": "",
                "warnings": ["tool_returned_no_payload"],
            })
        data = results[0]
        return _ok(inv, "Profile loaded.", {
            "explicit_preferences": data.get("explicit_preferences", {}),
            "inferred_preferences": data.get("inferred_preferences", {}),
            "tool_usage_stats": data.get("tool_usage_stats", {}),
            "updated_at": data.get("updated_at", ""),
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_memory_set_profile(inv: ToolInvocation) -> dict:
    field = str(inv.arguments.get("field", "")).strip()
    value = inv.arguments.get("value")
    merge = bool(inv.arguments.get("merge", True))
    if not field:
        return _error_inv(inv, "field is required")
    try:
        ws = _caller_workspace(inv)
        from storage.memory_governance import MemoryRecord, MemoryWriteGate
        existing = {}
        store = _get_store(ws)
        results = store.list_retrievable(ws, memory_type="profile", limit=1)
        if results:
            existing = results[0]
        profile = existing if existing else {"explicit_preferences": {}, "inferred_preferences": {}, "updated_at": ""}
        if merge and isinstance(profile.get("explicit_preferences"), dict):
            profile["explicit_preferences"][field] = value
        else:
            profile["explicit_preferences"] = {field: value}
        profile["updated_at"] = now_iso()

        rec = MemoryRecord(
            workspace_id=ws, scope="workspace",
            memory_type="profile", status="active",
            source="user", content=str(profile)[:2000],
            summary=f"Profile updated: {field}",
            confidence=1.0, created_by="user", redacted=True,
        )
        gate = MemoryWriteGate()
        gate.write(rec)
        return _ok(inv, "", {"field": field, "saved": True})
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_memory_search_merged(inv: ToolInvocation) -> dict:
    """Merged handler for memory.manage(action=search|list)."""
    if inv.arguments.get("query", "").strip():
        return handle_memory_search(inv)
    return handle_memory_list(inv)


def handle_memory_profile_merged(inv: ToolInvocation) -> dict:
    """Merged handler for memory.manage(action=profile_get|profile_set)."""
    if inv.arguments.get("field", "").strip():
        return handle_memory_set_profile(inv)
    return handle_memory_get_profile(inv)


def handle_memory_update(inv: ToolInvocation) -> dict:
    """Propose a governed memory update.

    Active facts are never overwritten by an LLM tool call. Updating an active
    memory creates a reviewable replacement candidate linked to the original.
    """
    args = inv.arguments
    memory_id = str(args.get("memory_id", "")).strip()
    content = str(args.get("content", "")).strip()
    if not memory_id:
        return _error_inv(inv, "memory_id is required")
    if not content:
        return _error_inv(inv, "content is required")
    try:
        ws = _caller_workspace(inv)
        store = _get_store(ws)
        rec = store.get(ws, memory_id)
        if not rec:
            return _error_inv(inv, f"memory_id not found: {memory_id}")
        from storage.memory_governance import MemoryRecord, MemoryWriteGate
        replacing_active = rec.status == "active"
        proposal = MemoryRecord(
            workspace_id=ws,
            session_id=rec.session_id,
            task_id=rec.task_id,
            scope=rec.scope,
            memory_type=rec.memory_type,
            status="pending",
            source="agent_suggestion",
            source_ref=rec.memory_id,
            content=content[:2000],
            summary=rec.summary or content[:200],
            confidence=rec.confidence,
            citations=list(rec.citations or []),
            created_by="llm_tool",
            metadata={**dict(rec.metadata or {}), "supersedes_memory_id": rec.memory_id},
        )
        if not replacing_active:
            proposal.memory_id = rec.memory_id
        result = MemoryWriteGate(store).write(proposal)
        if not result.get("ok"):
            return _error_inv(inv, str(result.get("error") or "memory update rejected")[:200])
        result_memory_id = str(result.get("memory_id") or proposal.memory_id)
        duplicate = bool(result.get("duplicate"))
        return _ok(inv, "", {
            "memory_id": result_memory_id,
            "supersedes_memory_id": memory_id,
            "status": result.get("status"),
            "updated": not duplicate and not replacing_active,
            "duplicate": duplicate,
            "_hint": (
                "内容与现有记忆重复，未创建新版本。"
                if duplicate else
                "更新已进入记忆门控。" + ("需确认后替换原记忆。" if replacing_active else "")
            ),
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_memory_delete_soft(inv: ToolInvocation) -> dict:
    args = inv.arguments
    memory_id = str(args.get("memory_id", "")).strip()
    if not memory_id:
        return _error_inv(inv, "memory_id is required")
    try:
        ws = _caller_workspace(inv)
        from storage.memory_governance import reject_memory
        result = reject_memory(ws, memory_id)
        return _ok(inv, "", {
            "memory_id": memory_id, "deleted": True, **result,
            "_hint": "记忆已软删除。",
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])
