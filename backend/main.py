# backend/main.py
"""
联智中枢 — unified backend entry point.

Start:
    python3 backend/main.py --port 8011
or:
    python -m backend.main --port 8011
"""

import os
import sys
from pathlib import Path

# Ensure backend package is importable
_APP_ROOT = Path(__file__).resolve().parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

from flask import Flask, jsonify, request

from backend.api.version import get_version
from backend.api.llm_api import (
    handle_llm_status, handle_llm_test,
    handle_llm_config_get, handle_llm_config_post, handle_llm_config_delete,
    handle_providers_list, handle_provider_get, handle_provider_save,
    handle_provider_delete, handle_llm_activate,
)
from backend.api.capability_routes import handle_capabilities
from backend.api.memory import handle_memory_status, handle_memory_write, handle_memory_search, handle_memory_confirm, handle_memory_delete, handle_memory_list, handle_memory_batch_delete
from backend.api.session_routes import (
    handle_session_create, handle_session_list,
    handle_session_detail, handle_session_update,
    handle_session_archive,
    handle_session_restore,
    handle_session_delete_permanently,
    handle_session_messages, handle_session_default,
)
from backend.api.artifact_routes import register_artifact_routes
from backend.api.job_routes import register_job_routes
from backend.api.runtime_routes import register_runtime_routes
from backend.api.context_routes import register_context_routes
from backend.api.workspace_routes import register_workspace_routes
from backend.api.knowledge_routes import register_knowledge_routes
from backend.api.review_routes import register_review_routes
from backend.api.workspace_status_routes import register_workspace_status_routes
from backend.api.state_routes import register_state_routes
from backend.api.storage_routes import register_storage_routes
from backend.api.identity_routes import register_identity_routes
from backend.api.extension_routes import register_extensions
from backend.api.admin_routes import register_admin_routes
from backend.api.workflow_routes import register_workflow_routes
from backend.core.settings import UNIFIED_PORT, API_MODE, BUILD_COMMIT
from backend.core.rate_limit import rate_limit_middleware
from storage.ids import validate_workspace_id


def _invalid_workspace_response():
    return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400


def _validated_workspace_id(raw=""):
    try:
        if not raw:
            return None, _invalid_workspace_response()
        return validate_workspace_id(raw), None
    except ValueError:
        return None, _invalid_workspace_response()


def _startup_reconciliation_result_key(principal: str, workspace_id: str) -> str:
    """Keep startup recovery evidence distinct for each storage principal."""
    owner = str(principal or "").strip() or "<maintenance>"
    return f"{owner}:{validate_workspace_id(workspace_id)}"


def create_app():
    app = Flask(__name__, static_folder=None)
    app.config["PORT"] = UNIFIED_PORT
    from backend.core.identity import ensure_identity_storage_ids, identity_enabled
    if identity_enabled():
        ensure_identity_storage_ids()
    from agent.runtime.memory_hooks import install_memory_governance_hooks
    install_memory_governance_hooks()
    from observability.metrics import install_http_metrics
    install_http_metrics(app)

    # ── CORS: allow configured workbench origins (Vite / LAN access) ──
    def _allowed_cors_origin():
        origin = request.headers.get("Origin")
        if not origin:
            return ""
        try:
            from backend.core.auth import is_allowed_browser_origin
            if is_allowed_browser_origin(origin, request.host):
                return origin
        except Exception:
            return ""
        return ""

    @app.before_request
    def _cors_preflight():
        if request.method == "OPTIONS":
            resp = app.make_default_options_response()
            origin = _allowed_cors_origin()
            if origin:
                resp.headers["Access-Control-Allow-Origin"] = origin
                resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
            resp.headers["Access-Control-Max-Age"] = "86400"
            return resp

    @app.after_request
    def _cors_after(resp):
        origin = _allowed_cors_origin()
        if origin:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp

    # ── Rate limiting (before all routes) ──
    rate_limit_middleware(app)

    # ── Health ──
    @app.route("/api/health")
    def api_health():
        from backend.core.responses import ok_response
        from agent.capabilities import catalog as _catalog
        body, _ = ok_response({
            "status": "ok",
            "api_mode": API_MODE,
            "capabilities_loaded": len(_catalog.list_all()),
        })
        return jsonify(body)

    @app.route("/api/ready")
    def api_ready():
        from core.runtime.production import production_readiness
        report = production_readiness()
        return jsonify(report), 200 if report["ready"] else 503

    # ── Browser login ──
    @app.route("/api/auth/status")
    def api_auth_status():
        from backend.core.auth import handle_auth_status
        return handle_auth_status()

    @app.route("/api/auth/login", methods=["POST"])
    def api_auth_login():
        from backend.core.auth import handle_auth_login
        return handle_auth_login()

    @app.route("/api/auth/logout", methods=["POST"])
    def api_auth_logout():
        from backend.core.auth import handle_auth_logout
        return handle_auth_logout()

    from backend.core.oidc import register_oidc_routes
    register_oidc_routes(app)

    # ── Version ──
    @app.route("/api/version")
    def api_version():
        return get_version()

    # ── Agent —唯一主入口 ──
    @app.route("/api/agent/message", methods=["POST"])
    def api_agent_message():
        """POST /api/agent/message — v2.1.1 unified entry point."""
        from backend.api.agent_routes import agent_message
        return agent_message()

    @app.route("/api/agent/status")
    def api_agent_status():
        from backend.api.agent_status import handle_agent_status
        return handle_agent_status()

    # ── Sessions ──
    @app.route("/api/sessions", methods=["POST"])
    def api_sessions_create():
        return handle_session_create()

    @app.route("/api/sessions")
    def api_sessions_list():
        return handle_session_list()

    @app.route("/api/sessions/default")
    def api_session_default():
        return handle_session_default()

    @app.route("/api/sessions/<session_id>")
    def api_session_detail(session_id):
        return handle_session_detail(session_id)

    @app.route("/api/sessions/<session_id>", methods=["PUT"])
    def api_session_update(session_id):
        return handle_session_update(session_id)

    @app.route("/api/sessions/<session_id>/archive", methods=["POST"])
    def api_session_archive(session_id):
        return handle_session_archive(session_id)

    @app.route("/api/sessions/<session_id>/restore", methods=["POST"])
    def api_session_restore(session_id):
        return handle_session_restore(session_id)

    @app.route("/api/sessions/<session_id>", methods=["DELETE"])
    def api_session_delete_permanently(session_id):
        return handle_session_delete_permanently(session_id)

    @app.route("/api/sessions/<session_id>/messages")
    def api_session_messages(session_id):
        return handle_session_messages(session_id)

    # ── LLM ──
    @app.route("/api/agent/llm/status")
    def api_agent_llm_status():
        return handle_llm_status()

    @app.route("/api/agent/llm/test", methods=["POST"])
    def api_agent_llm_test():
        return handle_llm_test()

    @app.route("/api/agent/llm/config")
    def api_agent_llm_config_get():
        return handle_llm_config_get()

    @app.route("/api/agent/llm/config", methods=["POST"])
    def api_agent_llm_config_post():
        return handle_llm_config_post()

    @app.route("/api/agent/llm/config", methods=["DELETE"])
    def api_agent_llm_config_delete():
        return handle_llm_config_delete()

    # ── LLM Providers (per-provider configs) ──

    @app.route("/api/agent/llm/providers")
    def api_llm_providers_list():
        return handle_providers_list()

    @app.route("/api/agent/llm/providers/<provider_id>")
    def api_llm_provider_get(provider_id):
        return handle_provider_get(provider_id)

    @app.route("/api/agent/llm/providers/<provider_id>", methods=["POST"])
    def api_llm_provider_save(provider_id):
        return handle_provider_save(provider_id)

    @app.route("/api/agent/llm/providers/<provider_id>", methods=["DELETE"])
    def api_llm_provider_delete(provider_id):
        return handle_provider_delete(provider_id)

    @app.route("/api/agent/llm/activate", methods=["POST"])
    def api_llm_activate():
        return handle_llm_activate()

    # ── Capabilities ──
    @app.route("/api/capabilities")
    def api_capabilities():
        return handle_capabilities()

    # ── Memory ──
    @app.route("/api/memory/status")
    def api_memory_status():
        return handle_memory_status()

    @app.route("/api/memory/write", methods=["POST"])
    def api_memory_write():
        return handle_memory_write()

    @app.route("/api/memory/search", methods=["POST"])
    def api_memory_search():
        return handle_memory_search()

    @app.route("/api/memory/list")
    def api_memory_list():
        return handle_memory_list()

    @app.route("/api/memory/confirm", methods=["POST"])
    def api_memory_confirm():
        return handle_memory_confirm()

    @app.route("/api/memory/reject", methods=["POST"])
    def api_memory_reject():
        from backend.api.memory import handle_memory_reject
        return handle_memory_reject()

    @app.route("/api/memory/<memory_id>", methods=["DELETE"])
    def api_memory_delete(memory_id):
        return handle_memory_delete(memory_id)

    @app.route("/api/memory/batch-delete", methods=["POST"])
    def api_memory_batch_delete():
        return handle_memory_batch_delete()

    # ── Sub-route registrations ──
    register_runtime_routes(app)      # /api/runtime/*, /api/workspaces/<ws>/selfcheck, retention, archive
    register_workspace_routes(app)    # /api/workspaces, /api/runs/*, /api/*/trace, /api/*/reports
    register_artifact_routes(app)     # /api/workspaces/<ws>/artifacts/*
    register_job_routes(app)          # /api/jobs/*
    register_context_routes(app)      # /api/context/*, /api/prompts/*, /api/harness/*
    register_knowledge_routes(app)    # /api/knowledge/* (sources, search, chunks)
    register_storage_routes(app)      # /api/storage/* (managed files and change events)
    register_review_routes(app)       # /api/review-items/*, /api/workspaces/<ws>/review-items
    register_workspace_status_routes(app)  # /api/workspaces/<ws>/status, /storage/health
    register_state_routes(app)     # /api/runtime/tasks/* (Phase 2 Durable State)
    register_extensions(app)       # /api/extensions + namespaced extension routes
    register_admin_routes(app)     # /api/admin/production and verified backups
    register_identity_routes(app)  # /api/identity users, organizations, memberships
    register_workflow_routes(app)  # /api/workflows cross-extension DAG orchestration

    # Reconcile durable jobs/subagent tasks that were running when a previous
    # backend process stopped. Domain modules can register their own startup
    # reconciliation hooks in an extension layer.
    import threading as _threading
    from storage.time_utils import now_iso as _startup_now_iso

    _backend_started_at = _startup_now_iso()

    def _startup_reconcile_async() -> None:
        try:
            from jobs.store import reconcile_running_jobs
            reconciled_jobs = reconcile_running_jobs(
                finished_at=_startup_now_iso(),
                started_before=_backend_started_at,
            )
            if reconciled_jobs:
                import logging as _job_log
                _job_log.getLogger(__name__).warning(
                    "[job startup] marked interrupted jobs failed: %s",
                    reconciled_jobs,
                )
        except Exception as exc:
            import logging as _job_log
            _job_log.getLogger(__name__).warning(
                "[job startup] reconcile failed: %s", exc, exc_info=True,
            )
        try:
            from extensions.network_operations.service import reconcile_interrupted_inspections
            reconciled_inspections = reconcile_interrupted_inspections()
            if reconciled_inspections:
                import logging as _inspection_log
                _inspection_log.getLogger(__name__).warning(
                    "[inspection startup] marked interrupted tasks terminal: %s",
                    reconciled_inspections,
                )
        except Exception as exc:
            import logging as _inspection_log
            _inspection_log.getLogger(__name__).warning(
                "[inspection startup] reconcile failed: %s", exc, exc_info=True,
            )
        try:
            from agent.runtime.durable.subagent import reconcile_subagent_tasks
            reconciled = reconcile_subagent_tasks(started_before=_backend_started_at)
            if reconciled:
                import logging as _subagent_log
                _subagent_log.getLogger(__name__).warning(
                    "[subagent startup] marked interrupted tasks failed: %s",
                    reconciled,
                )
        except Exception as exc:
            import logging as _subagent_log
            _subagent_log.getLogger(__name__).warning(
                "[subagent startup] reconcile failed: %s", exc,
                exc_info=True,
            )
        try:
            from agent.runtime.task_state import reconcile_active_task_states
            from backend.core.identity import get_user
            from storage.principal import known_storage_principals, storage_principal
            from storage.workspace_store import list_workspace_ids
            reconciled_task_states = {}
            all_workspace_ids = list_workspace_ids(include_system=False)
            for principal in known_storage_principals() or [""]:
                identity = get_user(principal)
                workspace_ids = list(identity.get("workspace_ids") or []) if isinstance(identity, dict) else all_workspace_ids
                with storage_principal(principal):
                    for workspace_id in sorted(set(workspace_ids)):
                        result_key = _startup_reconciliation_result_key(principal, workspace_id)
                        reconciled_task_states[result_key] = reconcile_active_task_states(
                            workspace_id,
                            started_before=_backend_started_at,
                        )
            if any(int(value.get("interrupted") or 0) for value in reconciled_task_states.values()):
                import logging as _task_state_log
                _task_state_log.getLogger(__name__).warning(
                    "[task state startup] marked in-flight tasks interrupted: %s",
                    reconciled_task_states,
                )
        except Exception as exc:
            import logging as _task_state_log
            _task_state_log.getLogger(__name__).warning(
                "[task state startup] reconcile failed: %s", exc, exc_info=True,
            )
        try:
            from agent.runtime.continuation_reconciler import (
                reconcile_all_workspaces,
                start_continuation_reconciler,
            )
            reconciled_continuations = reconcile_all_workspaces()
            start_continuation_reconciler()
            if reconciled_continuations:
                import logging as _continuation_log
                _continuation_log.getLogger(__name__).info(
                    "[continuation startup] reconciliation complete: %s",
                    reconciled_continuations,
                )
        except Exception as exc:
            import logging as _continuation_log
            _continuation_log.getLogger(__name__).warning(
                "[continuation startup] reconcile failed: %s", exc,
                exc_info=True,
            )

    _recon_t = _threading.Thread(
        target=_startup_reconcile_async,
        name="startup-reconcile",
        daemon=True,
    )
    _recon_t.start()

    # ── WebSocket routes (real-time streaming) ──
    from backend.ws.agent_ws import register_ws_routes
    register_ws_routes(app)

    # ── Tool approval routes ──
    from backend.api.approval_routes import register_approval_routes
    register_approval_routes(app)

    # ── Usage endpoint ──
    @app.route("/api/agent/usage")
    def api_agent_usage():
        from flask import request, jsonify
        from agent.runtime.token_tracker import get_usage
        raw_ws = request.args.get("workspace_id", "")
        if not raw_ws:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        try:
            from storage.ids import validate_workspace_id
            ws_id = validate_workspace_id(raw_ws)
        except Exception:
            return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400
        sid = request.args.get("session_id", "")
        return jsonify(get_usage(ws_id, sid))

    # ── Auth middleware (after all routes registered) ──
    from backend.core.auth import register_auth_middleware
    register_auth_middleware(app)

    # ── Backend-only root ──
    # The UI is served exclusively by the Vite frontend on 5273. Keeping
    # 8011 API-only avoids split browser storage between two origins
    # (8011 and 5273), which made session/local UI state look unsynced.
    @app.route("/")
    def backend_root():
        return jsonify({
            "ok": True,
            "service": "lzcore_backend",
            "api_base": "/api",
            "frontend_url": os.environ.get(
                "LZCORE_FRONTEND_DEV_URL",
                "http://127.0.0.1:5273",
            ),
        })

    return app


app = create_app()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="联智中枢 — Unified Backend")
    parser.add_argument("--port", type=int, default=UNIFIED_PORT, help="Port to listen on (default: 8011)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind to")
    args = parser.parse_args()

    port = args.port
    from backend.core.auth import validate_network_listener
    validate_network_listener(args.host)
    # Python execution policy must see the actual listener even when backend.main is started directly.
    os.environ["LZCORE_RUNTIME_BIND_HOST"] = args.host
    app.config["PORT"] = port

    print(f"联智中枢 running on http://{args.host}:{port}")
    print(f"  API mode: {API_MODE}")
    print(f"  Build: {BUILD_COMMIT}")

    # Signal handler: keep a predictable shutdown hook for extensions.
    import signal
    def _graceful_shutdown(signum, frame):
        print("Shutting down gracefully...")
        import sys
        sys.exit(0)

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    app.run(host=args.host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
