# context/resolver.py
"""Context Ref Resolver — resolves context_ref strings to ContextRef objects."""

from core.context.schemas import ContextRef


def resolve_context_ref(workspace_id: str, raw: str = "", payload: dict = None,
                        ui_context: dict = None) -> ContextRef:
    """Resolve context reference string to a ContextRef."""
    if not raw:
        return ContextRef(ref_type="none")

    ref = ContextRef(raw_ref=raw)

    try:
        if raw == "last_result":
            ref.ref_type = "last_result"
            ref.resolved = _check_has_last_result(workspace_id)

        elif raw == "last_run":
            ref.ref_type = "last_run"
            ref.ref_id = _get_last_run_id(workspace_id)
            ref.resolved = bool(ref.ref_id)

        elif raw == "last_job":
            ref.ref_type = "last_job"
            ref.ref_id = _get_last_job_id(workspace_id)
            ref.resolved = bool(ref.ref_id)

        elif raw == "last_report":
            ref.ref_type = "last_report"
            ref.ref_id = _get_last_report_id(workspace_id)
            ref.resolved = bool(ref.ref_id)

        elif raw == "last_artifact":
            ref.ref_type = "last_artifact"
            ref.resolved = _check_has_artifacts(workspace_id)

        elif raw.startswith("artifact:"):
            ref.ref_type = "artifact"
            ref.ref_id = raw.split(":", 1)[1]
            ref.resolved = _check_artifact_exists(workspace_id, ref.ref_id)

        elif raw.startswith("run:"):
            ref.ref_type = "run"
            ref.ref_id = raw.split(":", 1)[1]
            ref.resolved = _check_run_exists(workspace_id, ref.ref_id)

        elif raw.startswith("job:"):
            ref.ref_type = "job"
            ref.ref_id = raw.split(":", 1)[1]
            ref.resolved = _check_job_exists(workspace_id, ref.ref_id)

        elif raw.startswith("report:"):
            ref.ref_type = "report"
            ref.ref_id = raw.split(":", 1)[1]
            ref.resolved = _check_report_exists(workspace_id, ref.ref_id)

        elif raw == "current_workspace":
            ref.ref_type = "current_workspace"
            ref.resolved = True

        elif raw == "current_topology":
            ref.ref_type = "current_topology"
            ref.resolved = False

        elif raw == "selected_artifact":
            ref.ref_type = "selected_artifact"
            aid = (ui_context or {}).get("selected_artifact_id", "")
            if aid:
                ref.ref_id = aid
                ref.resolved = _check_artifact_exists(workspace_id, aid)
            else:
                ref.resolution_error = "no selected artifact in ui_context"

        else:
            ref.ref_type = "explicit"
            ref.raw_ref = raw

    except Exception as e:
        ref.resolution_error = str(e)[:200]

    return ref


# ── Helpers ──

def _get_ws_state(ws_id):
    try:
        from storage.workspace_store import get_workspace_state
        return get_workspace_state(ws_id)
    except Exception:
        return {}

def _check_has_last_result(ws_id):
    s = _get_ws_state(ws_id)
    return bool(s.get("last_intent"))

def _get_last_run_id(ws_id):
    return _get_ws_state(ws_id).get("last_run_id", "")

def _get_last_job_id(ws_id):
    s = _get_ws_state(ws_id)
    return s.get("job_stats", {}).get("last_job_id", "")

def _get_last_report_id(ws_id):
    s = _get_ws_state(ws_id)
    reports = s.get("last_report_artifacts", [])
    return reports[-1] if reports else ""

def _check_has_artifacts(ws_id):
    try:
        from artifacts.store import list_artifacts
        arts = list_artifacts(ws_id, limit=1)
        return len(arts) > 0
    except Exception:
        return False

def _check_artifact_exists(ws_id, art_id):
    try:
        from artifacts.store import get_artifact
        return get_artifact(ws_id, art_id) is not None
    except Exception:
        return False

def _check_run_exists(ws_id, run_id):
    try:
        from storage.run_record_store import get_run
        return bool(get_run(run_id, ws_id))
    except Exception:
        return False

def _check_job_exists(ws_id, job_id):
    try:
        from jobs.store import get_job
        return get_job(ws_id, job_id) is not None
    except Exception:
        return False

def _check_report_exists(ws_id, art_id):
    return _check_artifact_exists(ws_id, art_id)
