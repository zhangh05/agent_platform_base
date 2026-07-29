# backend/api/memory.py
"""Governed memory API: lifecycle, retrieval, review, and deletion."""
from flask import request, jsonify


def _validated_ws_id(raw: str) -> str:
    """Validate and return workspace_id. Empty → 400."""
    if not raw or not raw.strip():
        return ""
    from storage.ids import validate_workspace_id
    return validate_workspace_id(raw.strip())


def _read_ws_id(raw: str):
    try:
        ws_id = _validated_ws_id(raw)
    except Exception:
        return "", "invalid_workspace_id"
    if not ws_id:
        return "", "workspace_id is required"
    return ws_id, ""


def handle_memory_status():
    """Return memory system status for the given workspace."""
    ws_id = request.args.get("workspace_id", "")
    ws_id, err = _read_ws_id(ws_id)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    try:
        from storage.memory_governance import MemoryStore
        store = MemoryStore()
        records = store.list_retrievable(ws_id)
        all_records = store.list_all(ws_id)
        status_counts: dict[str, int] = {}
        for record in all_records:
            status_counts[record.status] = status_counts.get(record.status, 0) + 1
        return jsonify({
            "ok": True,
            "enabled": True,
            "backend": "governed_context_store",
            "workspace_id": ws_id,
            "records": len(records),
            "policy": "layered_reflection",
            "status_counts": status_counts,
            "data_dir": f"workspaces/{ws_id}/memory/",
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


def handle_memory_write():
    """Write a memory record through MemoryWriteGate (governed)."""
    data = request.get_json(silent=True) or {}
    title = data.get("title", "")
    content = data.get("content", "")
    if not title and not content:
        return jsonify({"ok": False, "error": "title or content required"}), 400

    workspace_id = data.get("workspace_id", "")
    ws_id, err = _read_ws_id(workspace_id)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    source = data.get("source", "agent")
    try:
        confidence = max(0.0, min(float(data.get("confidence", 0.5)), 1.0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid_confidence"}), 400
    user_confirmed = data.get("user_confirmed") is True
    is_subagent = bool(data.get("is_subagent", False))

    try:
        from storage.memory_governance import MemoryRecord, MemoryWriteGate
        gate = MemoryWriteGate()

        # Build MemoryRecord for governance
        rec = MemoryRecord(
            workspace_id=ws_id,
            session_id=data.get("session_id", ""),
            task_id=data.get("task_id", ""),
            scope=data.get("scope", "workspace"),
            memory_type=data.get("memory_type", "knowledge_note"),
            status="active" if user_confirmed else "pending",
            source="user" if user_confirmed else ("subagent" if is_subagent else "agent_suggestion"),
            content=content[:2000],
            summary=title[:200],
            confidence=confidence,
            citations=data.get("citations", []),
            created_by="user" if user_confirmed else source,
            redacted=True,
        )
        result = gate.write(rec)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


def handle_memory_search():
    """Search memory records through MemoryStore (governed)."""
    data = request.get_json(silent=True) or {}
    query = data.get("query", "")
    workspace_id = data.get("workspace_id", "")
    ws_id, err = _read_ws_id(workspace_id)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    try:
        limit = max(1, min(int(data.get("limit", 10)), 100))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "invalid_limit"}), 400

    try:
        from storage.memory_governance import MemoryStore
        store = MemoryStore()
        records = store.search(ws_id, query, limit=limit)

        return jsonify({"ok": True, "results": records[:limit], "count": len(records[:limit])})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


def handle_memory_confirm():
    """Confirm a pending memory record."""
    data = request.get_json(silent=True) or {}
    ws_id, err = _read_ws_id(data.get("workspace_id", ""))
    memory_id = data.get("memory_id", "")
    if err:
        return jsonify({"ok": False, "error": err}), 400
    if not memory_id:
        return jsonify({"ok": False, "error": "memory_id required"}), 400

    try:
        from storage.memory_governance import confirm_memory
        result = confirm_memory(ws_id, memory_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


def handle_memory_reject():
    """Reject a pending memory record."""
    data = request.get_json(silent=True) or {}
    ws_id, err = _read_ws_id(data.get("workspace_id", ""))
    memory_id = data.get("memory_id", "")
    if err:
        return jsonify({"ok": False, "error": err}), 400
    if not memory_id:
        return jsonify({"ok": False, "error": "memory_id required"}), 400

    try:
        from storage.memory_governance import reject_memory
        result = reject_memory(ws_id, memory_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


def handle_memory_delete(memory_id):
    """Hard-delete a memory record — physically remove file and ContextStore index."""
    if request.args.get("confirm", "").lower() != "true":
        return jsonify({"ok": False, "error": "confirm_required"}), 400
    ws_id, err = _read_ws_id(request.args.get("workspace_id", ""))
    if err:
        return jsonify({"ok": False, "error": err}), 400
    try:
        from storage.memory_governance import MemoryStore
        store = MemoryStore()
        ok = store.delete_file(ws_id, memory_id)
        if not ok:
            return jsonify({"ok": False, "error": "memory_not_found"}), 404
        return jsonify({"ok": True, "deleted_count": 1})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


def handle_memory_batch_delete():
    """Hard-delete multiple memory records."""
    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return jsonify({"ok": False, "error": "confirm_required"}), 400
    ws_id, err = _read_ws_id(data.get("workspace_id", ""))
    if err:
        return jsonify({"ok": False, "error": err}), 400
    ids = data.get("memory_ids") or []
    if not ids or not isinstance(ids, list):
        return jsonify({"ok": False, "error": "memory_ids required (list)"}), 400

    from storage.memory_governance import MemoryStore
    store = MemoryStore()
    deleted = 0
    for mid in ids:
        if store.delete_file(ws_id, mid):
            deleted += 1
    return jsonify({"ok": True, "deleted_count": deleted, "requested": len(ids)})


def handle_memory_list():
    """List memory records."""
    ws_id, err = _read_ws_id(request.args.get("workspace_id", ""))
    if err:
        return jsonify({"ok": False, "error": err}), 400
    from storage.memory_governance import MemoryStore
    store = MemoryStore()
    include_deleted = str(request.args.get("include_deleted", "")).lower() in {"1", "true", "yes"}
    status_filter = str(request.args.get("status", "")).strip()
    session_id = str(request.args.get("session_id", "")).strip()
    try:
        limit = int(request.args.get("limit") or 100)
    except Exception:
        limit = 100
    limit = max(1, min(limit, 500))

    records = []
    for rec in store.list_all(ws_id):
        if status_filter and rec.status != status_filter:
            continue
        if session_id and rec.session_id != session_id:
            continue
        if not include_deleted and rec.status in {"rejected", "expired"}:
            continue
        payload = rec.to_dict()
        try:
            from core.tools.redaction import redact_tool_output
            payload = redact_tool_output(payload)
        except Exception:
            pass
        records.append(payload)
        if len(records) >= limit:
            break
    return jsonify({"ok": True, "records": records, "count": len(records)})
