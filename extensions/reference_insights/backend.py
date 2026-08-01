"""Reference extension proving the complete platform contribution path."""

from flask import jsonify, request


def summarize_text(invocation):
    text = str((invocation.arguments or {}).get("text") or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    words = text.split()
    preview = text[:120] + ("…" if len(text) > 120 else "")
    return {
        "ok": True,
        "summary": preview or "未提供文本",
        "workspace_id": invocation.workspace_id,
        "metrics": {
            "characters": len(text),
            "words": len(words),
            "non_empty_lines": len(lines),
        },
    }


def register_routes(app):
    @app.route("/api/extensions/reference.insights/status")
    def reference_insights_status():
        workspace_id = request.args.get("workspace_id", "").strip()
        if not workspace_id:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        return jsonify({
            "ok": True,
            "extension_id": "reference.insights",
            "workspace_id": workspace_id,
            "status": "ready",
            "tool_id": "reference.insights.summarize",
        })


def register():
    return {
        "tools": [{
            "tool_id": "reference.insights.summarize",
            "name": "文本摘要统计",
            "description": "在当前工作区中生成确定性的文本概览和基础统计。",
            "category": "text",
            "risk_level": "low",
            "permission_action": "read",
            "handler": summarize_text,
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 20000}},
                "required": ["text"]
            }
        }],
        "register_routes": register_routes,
    }
