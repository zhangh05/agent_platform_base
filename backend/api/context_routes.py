# backend/api/context_routes.py
"""Context, Prompt & Harness routes — context resolution, prompt management."""

from flask import jsonify, request
from storage.ids import validate_workspace_id


def _invalid_ws():
    return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400


def _validated_ws_id(raw="default"):
    try:
        return validate_workspace_id(raw), None
    except ValueError:
        return None, _invalid_ws()


def register_context_routes(app):
    """Register context, prompt, and harness API routes on the Flask app."""

    @app.route("/api/context/status")
    def api_context_status():
        return jsonify({
            "context_runtime_enabled": True,
            "supported_refs": ["last_result", "last_run", "last_job", "last_report",
                               "last_artifact", "artifact:<id>", "run:<id>", "job:<id>",
                               "report:<id>", "current_workspace"],
            "default_budget": {"max_items": 30, "max_chars": 12000},
        })

    @app.route("/api/context/resolve", methods=["POST"])
    def api_context_resolve():
        data = request.get_json(silent=True) or {}
        workspace_id, err = _validated_ws_id(data.get("workspace_id", ""))
        if err:
            return err
        from core.context.resolver import resolve_context_ref
        ref = resolve_context_ref(
            workspace_id,
            data.get("context_ref", ""),
            data.get("payload"),
        )
        if ref is None:
            return jsonify({"ok": False, "error": "context_ref not found"}), 404
        return jsonify(ref.as_dict())

    @app.route("/api/context/build", methods=["POST"])
    def api_context_build():
        data = request.get_json(silent=True) or {}
        workspace_id, err = _validated_ws_id(data.get("workspace_id", ""))
        if err:
            return err
        from core.context.builder import build_context_bundle
        bundle = build_context_bundle(
            workspace_id=workspace_id,
            user_input=data.get("message", ""),
            intent=data.get("intent", ""),
            context_ref=data.get("context_ref", ""),
            payload=data.get("payload"),
        )
        if bundle is None:
            return jsonify({"ok": False, "error": "failed to build context bundle"}), 500
        return jsonify(bundle.as_dict())

    @app.route("/api/prompts")
    def api_prompts():
        from prompts.loader import list_prompts
        return jsonify({"prompts": list_prompts()})

    @app.route("/api/prompts/<prompt_id>")
    def api_prompt_detail(prompt_id):
        from prompts.loader import get_prompt
        spec = get_prompt(prompt_id)
        if not spec:
            return jsonify({"ok": False, "error": "prompt not found"}), 404
        return jsonify(spec.as_dict())

    @app.route("/api/prompts/render", methods=["POST"])
    def api_prompts_render():
        data = request.get_json(silent=True) or {}
        workspace_id, err = _validated_ws_id(data.get("workspace_id", ""))
        if err:
            return err
        from prompts.loader import render_prompt
        from core.context.builder import build_context_bundle
        bundle = build_context_bundle(
            workspace_id=workspace_id,
            user_input=data.get("message", ""),
            intent=data.get("intent", ""),
            context_ref=data.get("context_ref", ""),
        )
        safe = bundle.safe_llm_context.as_dict() if bundle.safe_llm_context else {}
        result = render_prompt(
            task=data.get("task", "response_compose"),
            safe_context=safe, user_input=data.get("message", ""),
        )
        if result is None:
            return jsonify({"ok": False, "error": "failed to render prompt"}), 500
        return jsonify(result.as_dict())

    @app.route("/api/harness/status")
    def api_harness_status():
        return jsonify({
            "golden_cases": 4, "scenarios": 4,
            "prompt_cases": 3, "coverage_matrix_exists": True,
            "status": "ready",
        })
