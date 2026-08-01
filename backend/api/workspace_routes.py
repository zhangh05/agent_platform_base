# backend/api/workspace_routes.py
"""Workspace, Run, Report & Trace routes."""

from flask import jsonify, request
from storage.ids import validate_session_id, validate_workspace_id
from artifacts.store import sanitize_record


def _trace_event_type(event: dict) -> str:
    return str(event.get("event_type") or event.get("type") or event.get("name") or "").lower()


def _trace_event_level(event: dict) -> str:
    return str(event.get("level") or event.get("status") or "").lower()


def _trace_event_bag(event: dict) -> dict:
    for key in ("metadata", "payload"):
        value = event.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _trace_event_tool_id(event: dict) -> str:
    bag = _trace_event_bag(event)
    for source in (bag, event):
        for key in ("canonical_tool_id", "tool_id"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _trace_event_time(event: dict) -> str:
    value = event.get("occurred_at") or event.get("started_at") or event.get("timestamp") or ""
    return str(value) if value is not None else ""


def _safe_run_trace_summary(run: dict, trace: dict | None) -> dict:
    """Derive display-safe run counters from redacted trace events."""
    events = trace.get("events", []) if isinstance(trace, dict) else []
    if not isinstance(events, list):
        events = []

    tool_ids: set[str] = set()
    anonymous_tool_events = 0
    warning_count = 0
    error_count = 0
    timestamps: list[str] = []

    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = _trace_event_type(event)
        event_level = _trace_event_level(event)
        event_time = _trace_event_time(event)
        if event_time:
            timestamps.append(event_time)

        tool_id = _trace_event_tool_id(event)
        if "tool" in event_type or "approval" in event_type or tool_id:
            if tool_id:
                tool_ids.add(tool_id)
            else:
                anonymous_tool_events += 1
        if "warn" in event_type or event_level in {"warn", "warning"}:
            warning_count += 1
        if "error" in event_type or "fail" in event_type or event_level in {"err", "error"}:
            error_count += 1

    warnings = run.get("warnings", [])
    if isinstance(warnings, list):
        warning_count = max(warning_count, len(warnings))
    if run.get("error"):
        error_count = max(error_count, 1)

    visible_tools = run.get("visible_tools", [])
    if isinstance(visible_tools, list):
        for tool in visible_tools:
            if isinstance(tool, str) and tool.strip():
                tool_ids.add(tool.strip())

    selected_capabilities = run.get("selected_capabilities")
    if not isinstance(selected_capabilities, list):
        selected_capabilities = []

    tool_count = max(int(run.get("tool_call_count") or 0), len(tool_ids) or anonymous_tool_events)
    # v3.9.14 fix: started_at / finished_at fall back to ``run.created_at``
    # before trace timestamps. Trace event timestamps are produced AFTER
    # the run record is written (run_started event fires ~ms after the
    # run record is created), so they are a few ms off. Tests + frontend
    # both expect the run record's own ``created_at`` to be authoritative.
    return {
        "turn_id": run.get("turn_id") or run.get("run_id") or "",
        "started_at": (
            run.get("started_at")
            or run.get("created_at")
            or (timestamps[0] if timestamps else "")
        ),
        "finished_at": (
            run.get("finished_at")
            or run.get("updated_at")
            or (timestamps[-1] if timestamps else "")
        ),
        "selected_capabilities": selected_capabilities,
        "visible_tools": sorted(tool_ids),
        "tool_call_count": tool_count,
        "warning_count": max(int(run.get("warning_count") or 0), warning_count),
        "error_count": max(int(run.get("error_count") or 0), error_count),
        "event_count": len(events),
    }


def _invalid_ws():
    return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400


def _invalid_limit():
    return jsonify({"ok": False, "error": "invalid_limit"}), 400


def _validated_ws_id(raw="default"):
    try:
        return validate_workspace_id(raw), None
    except ValueError:
        return None, _invalid_ws()


def _validated_session_id(raw):
    try:
        return validate_session_id(raw), None
    except ValueError:
        return None, (jsonify({"ok": False, "error": "invalid_session_id"}), 400)


def _confirmed():
    data = request.get_json(silent=True) or {} if request.is_json else {}
    raw = request.args.get("confirm", data.get("confirm", False))
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _validated_limit(default=100, max_value=500):
    from backend.core.params import parse_limit
    try:
        return parse_limit(request.args, default=default, max_value=max_value), None
    except ValueError:
        return None, _invalid_limit()


def register_workspace_routes(app):
    """Register workspace, run, report, and trace API routes."""

    # ── Workspace ──
    @app.route("/api/workspaces")
    def api_workspaces_list():
        from storage.workspace_store import list_workspaces
        workspaces = list_workspaces()
        try:
            from flask import session
            from backend.core.auth import _request_has_valid_api_token
            from backend.core.identity import identity_enabled, has_role
            if identity_enabled() and not _request_has_valid_api_token() and not has_role(str(session.get("agent_platform_role") or "viewer"), "admin"):
                allowed = set(session.get("agent_platform_workspaces") or [])
                workspaces = [item for item in workspaces if item.get("workspace_id") in allowed]
        except Exception:
            workspaces = []
        return jsonify({"workspaces": workspaces})

    @app.route("/api/workspaces", methods=["POST"])
    def api_workspace_create():
        data = request.get_json(silent=True) or {}
        ws_id = data.get("workspace_id", "")
        if not ws_id:
            return jsonify({"ok": False, "error": "workspace_id required"}), 400
        from storage.workspace_store import ensure_workspace
        from storage.ids import validate_workspace_id
        try:
            ws_id = validate_workspace_id(ws_id)
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400
        ensure_workspace(ws_id)
        from storage.workspace_store import get_workspace_state
        state = get_workspace_state(ws_id)
        return jsonify({"ok": True, "workspace": state})

    @app.route("/api/workspaces/<ws_id>/state")
    def api_workspace_state(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from storage.workspace_store import get_workspace_state
        return jsonify({"ok": True, "workspace": get_workspace_state(ws_id)})

    @app.route("/api/workspaces/<ws_id>/settings", methods=["PUT"])
    def api_workspace_settings_update(ws_id):
        """Update workspace-level settings."""
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        data = request.get_json(silent=True) or {}

        allowed_keys = {"memory_enabled"}
        patch = {k: v for k, v in data.items() if k in allowed_keys}

        if not patch:
            return jsonify({"ok": False, "error": "no_valid_settings", "message": "Allowed keys: memory_enabled"}), 400

        if "memory_enabled" in patch:
            patch["memory_enabled"] = patch["memory_enabled"] is not False

        from storage.workspace_store import update_workspace_state
        state = update_workspace_state(ws_id, patch)
        return jsonify({"ok": True, "workspace": state})

    @app.route("/api/workspaces/<ws_id>", methods=["DELETE"])
    def api_workspace_delete(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        if not _confirmed():
            return jsonify({"ok": False, "error": "confirm_required"}), 400
        from storage.workspace_store import delete_workspace
        return jsonify(delete_workspace(ws_id))

    @app.route("/api/workspaces/batch-delete", methods=["POST"])
    def api_workspace_batch_delete():
        data = request.get_json(silent=True) or {}
        ws_ids = data.get("workspace_ids", [])
        if not isinstance(ws_ids, list) or len(ws_ids) == 0:
            return jsonify({"ok": False, "error": "workspace_ids must be a non-empty list"}), 400
        if not _confirmed():
            return jsonify({"ok": False, "error": "confirm_required"}), 400
        from storage.workspace_store import batch_delete_workspaces
        return jsonify(batch_delete_workspaces(ws_ids))

    @app.route("/api/workspaces/<ws_id>/rename", methods=["POST"])
    def api_workspace_rename(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        data = request.get_json(silent=True) or {}
        new_id = data.get("new_workspace_id", "")
        if not new_id:
            return jsonify({"ok": False, "error": "new_workspace_id required"}), 400
        from storage.workspace_store import rename_workspace
        new_id_validated, err2 = _validated_ws_id(new_id)
        if err2:
            return err2
        return jsonify(rename_workspace(ws_id, new_id_validated))

    # ── Runs ──
    @app.route("/api/runs/recent")
    def api_runs_recent():
        """Recent runs — safe summaries, no full config.

        Query params:
          workspace_id  (default: "default")
          limit         (default: 10, max: 100)
          session_id    (optional: exact session scope for sidebar)
          session_status (default: "active", set to "" for all sessions)
        """
        ws_id = request.args.get("workspace_id", "")
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        limit, err = _validated_limit(default=10, max_value=100)
        if err:
            return err
        session_id = request.args.get("session_id", "").strip()
        if session_id:
            session_id, err = _validated_session_id(session_id)
            if err:
                return err

        from storage.run_record_store import list_runs, run_sort_key
        from storage.session_store import get_session, list_sessions

        # Fetch more runs than needed to account for filtering
        raw_runs = list_runs(ws_id, limit=limit * 5)
        runs_sorted = sorted(raw_runs, key=run_sort_key, reverse=True) if raw_runs else []

        # session_status="" means no filter (all sessions for RuntimeAudit)
        session_status = request.args.get("session_status", "active")
        if session_id:
            session = get_session(session_id, ws_id)
            if not session or (session_status and session.get("status") != session_status):
                recent = []
                session_titles = {}
            else:
                recent = [r for r in runs_sorted if r.get("session_id", "") == session_id][:limit]
                session_titles = {session_id: session.get("title", "")}
        elif session_status == "":
            # No filtering — return runs from all sessions
            recent = runs_sorted[:limit]
            session_titles: dict = {}
        else:
            # Filter: only show runs from sessions with the given status
            # (default "active" — matches sidebar which only shows active sessions)
            active_sessions = list_sessions(ws_id, status=session_status)
            active_session_ids = {s["session_id"] for s in active_sessions if s.get("session_id")}

            recent = []
            for r in runs_sorted:
                sid = r.get("session_id", "")
                # Include run if: no session_id, or session matches filter
                if not sid or sid in active_session_ids:
                    recent.append(r)
                    if len(recent) >= limit:
                        break

            # Build session_id → title lookup
            session_titles = {s.get("session_id", ""): s.get("title", "") for s in active_sessions if s.get("session_id")}

        safe_recent = []
        # Whitelist of safe fields for public run history (never expose secrets, configs, or prompts)
        _SAFE_RUN_KEYS = frozenset({
            "run_id", "workspace_id", "session_id", "intent",
            "selected_capability", "status", "error",
            "warnings", "quality_summary", "elapsed_ms", "created_at",
            "node_timings", "trace_id", "user_input_summary", "final_response",
            "turn_id", "started_at", "finished_at", "selected_capabilities",
            "visible_tools", "tool_call_count", "warning_count", "error_count",
            "tool_decision", "no_tool_reason",
            # Expose `ok` so the frontend can render badges from the current
            # AgentResult projection as well as the persisted status.
            "ok",
        })
        from observability.store import get_trace
        from core.tools.redaction import redact_tool_output
        for r in recent:
            safe_run = {k: v for k, v in r.items() if k in _SAFE_RUN_KEYS}
            safe_run = redact_tool_output(safe_run)
            rid = safe_run.get("run_id") or safe_run.get("turn_id")
            trace = get_trace(rid, ws_id) if rid else None
            safe_run.update(_safe_run_trace_summary(r, trace))
            if trace and trace.get("trace_id") and not safe_run.get("trace_id"):
                safe_run["trace_id"] = trace.get("trace_id")
            # Attach session title so the frontend can show run→session association
            safe_run["session_title"] = session_titles.get(r.get("session_id", ""), "")
            safe_recent.append(safe_run)
        return jsonify({"runs": safe_recent, "count": len(safe_recent)})

    @app.route("/api/runs/<run_id>")
    def api_run_detail(run_id):
        ws_id = request.args.get("workspace_id", "")
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from storage.run_record_store import get_run
        result = get_run(run_id, ws_id)
        if not result:
            return jsonify({"ok": False, "error": "run not found"}), 404
        return jsonify(result)

    @app.route("/api/workspaces/<ws_id>/runs")
    def api_workspace_runs(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        limit, err = _validated_limit(default=100, max_value=500)
        if err:
            return err
        from storage.run_record_store import list_runs
        return jsonify({"runs": list_runs(ws_id, limit=limit)})

    @app.route("/api/workspaces/<ws_id>/history")
    def api_workspace_history(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        limit, err = _validated_limit(default=100, max_value=500)
        if err:
            return err
        from storage.run_record_store import list_runs, run_sort_key
        runs = sorted(list_runs(ws_id, limit=limit), key=run_sort_key, reverse=True)
        return jsonify({"workspace_id": ws_id, "runs": runs, "count": len(runs)})

    @app.route("/api/workspaces/<ws_id>/runs/<run_id>")
    def api_workspace_run(ws_id, run_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from storage.run_record_store import get_run
        result = get_run(run_id, ws_id)
        if not result:
            return jsonify({"ok": False, "error": "run not found"}), 404
        return jsonify(result)

    # ── Trace (Observability) ──
    @app.route("/api/workspaces/<ws_id>/runs/<run_id>/trace")
    def api_workspace_trace(ws_id, run_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from observability.store import get_trace
        trace = get_trace(run_id, ws_id)
        if not trace:
            return jsonify({"ok": False, "error": "trace not found"}), 404
        return jsonify({
            "ok": True,
            "trace": trace,
            "events": trace.get("events", []),
            "run_id": trace.get("run_id", run_id),
        })

    @app.route("/api/workspaces/<ws_id>/traces")
    def api_workspace_traces(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from observability.store import list_traces
        return jsonify({"traces": list_traces(ws_id)})


    # ── Reports / Export ──
    @app.route("/api/reports/create", methods=["POST"])
    def api_report_create():
        data = request.get_json(silent=True) or {}
        workspace_id, err = _validated_ws_id(data.get("workspace_id", ""))
        if err:
            return err
        from core.reports.schemas import ReportRequest
        from core.reports.service import create_report as svc_create_report
        req = ReportRequest(
            workspace_id=workspace_id,
            run_id=data.get("run_id", ""),
            report_type=data.get("report_type", "generic"),
            title=data.get("title", ""),
            format=data.get("format", "markdown"),
            sensitivity=data.get("sensitivity", "internal"),
            content=data.get("content", ""),
        )
        result = svc_create_report(req)
        return jsonify(result.as_dict())

    @app.route("/api/workspaces/<ws_id>/runs/<run_id>/report", methods=["POST"])
    def api_workspace_run_report(ws_id, run_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        data = request.get_json(silent=True) or {}
        from core.reports.schemas import ReportRequest
        from core.reports.service import create_report
        result = create_report(
            ReportRequest(
                workspace_id=ws_id,
                run_id=run_id,
                report_type=data.get("report_type", "generic"),
                title=data.get("title", "Run report"),
                format=data.get("format", "markdown"),
                content=data.get("content", ""),
            ),
            data.get("agent_result", {}),
        )
        return jsonify(result.as_dict())

    @app.route("/api/workspaces/<ws_id>/reports")
    def api_workspace_reports(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from artifacts.store import list_artifacts
        arts = list_artifacts(ws_id, artifact_type="report")
        return jsonify({"reports": arts})

    @app.route("/api/workspaces/<ws_id>/reports/<artifact_id>/content")
    def api_report_content(ws_id, artifact_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from artifacts.store import read_artifact_content
        allow = request.args.get("allow_sensitive", "0") == "1"
        content = read_artifact_content(ws_id, artifact_id, allow_sensitive=allow)
        if content is None:
            return jsonify({"ok": False, "error": "content not accessible"}), 403
        return jsonify({"ok": True, "content": content})
