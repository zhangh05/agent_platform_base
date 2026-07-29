# context/loader.py
"""Context loader — loads raw ContextItems from all sources.

v4.0.0: Memory and knowledge retrieval moved to runtime/memory and
runtime/knowledge modules. Loader handles workspace/artifact/job/report only.
"""

from core.context.schemas import ContextItem, ContextRef
import logging
_LOG = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    """CJK-aware token estimation for context items."""
    if not text:
        return 0
    s = str(text)
    cjk = sum(1 for c in s if _is_cjk(c))
    non_cjk = len(s) - cjk
    return max(1, cjk + non_cjk // 4)


def _is_cjk(c: str) -> bool:
    cp = ord(c)
    return (
        0x4E00 <= cp <= 0x9FFF or    # CJK Unified Ideographs
        0x3400 <= cp <= 0x4DBF or    # CJK Extension A
        0x3000 <= cp <= 0x303F or    # CJK Symbols and Punctuation
        0x31C0 <= cp <= 0x31EF or    # CJK Strokes
        0x3200 <= cp <= 0x32FF or    # Enclosed CJK Letters
        0x3300 <= cp <= 0x33FF or    # CJK Compatibility
        0xFE30 <= cp <= 0xFE4F or    # CJK Compatibility Forms
        0xFF00 <= cp <= 0xFFEF       # Halfwidth and Fullwidth Forms
    )


def load_context_items(workspace_id: str, context_ref=None, intent: str = "",
                       payload: dict = None, capability_id: str = "",
                       user_input: str = "",
                       include_memory=True, include_workspace=True,
                       include_artifacts=True, include_jobs=True,
                       include_reports=True, include_trace=True,
                       include_knowledge=True) -> list:
    items = []

    # 1. Request item (P0)
    msg = user_input or (payload or {}).get("message", "")
    items.append(ContextItem(
        item_type="request", source="request", priority=0,
        title="User request", summary=msg[:200],
        content={"intent": intent, "payload_keys": list((payload or {}).keys())},
        sensitivity="internal", scope="request",
        token_estimate=_estimate_tokens(msg),
    ))

    # 2. Explicit context_ref item (P1)
    if context_ref and hasattr(context_ref, 'resolved') and context_ref.resolved:
        items.append(ContextItem(
            item_type=f"ref:{context_ref.ref_type}", source="request", priority=10,
            title=f"Context: {context_ref.ref_type}", summary=f"Resolved ref: {context_ref.ref_id}",
            source_id=context_ref.ref_id, sensitivity="internal", scope="request",
        ))

    # 3. Workspace state (P3)
    if include_workspace:
        try:
            from storage.workspace_store import get_workspace_state
            ws = get_workspace_state(workspace_id)
            safe_ws = {k: v for k, v in ws.items()
                       if k not in ("raw_source", "raw_output", "raw_payload")
                       and "path" not in k.lower()}
            items.append(ContextItem(
                item_type="workspace_state", source="workspace", priority=30,
                title="Workspace state", summary=ws.get("last_result_summary", "")[:200],
                content=safe_ws, sensitivity="internal", scope="workspace",
                token_estimate=_estimate_tokens(safe_ws),
            ))
        except Exception:
            _LOG.warning("context.loader: silent exception", exc_info=True)

    # 6. Artifact refs (P4 or P2 if explicit)
    if include_artifacts:
        try:
            from artifacts.store import list_artifacts
            arts = list_artifacts(workspace_id, limit=10)
            for a in arts:
                priority = 20 if context_ref and context_ref.ref_id == a.get("artifact_id") else 40
                items.append(ContextItem(
                    item_type="artifact_summary", source="artifact", priority=priority,
                    title=a.get("title", ""), summary=a.get("summary", "")[:200],
                    content={"artifact_id": a.get("artifact_id"), "artifact_type": a.get("artifact_type"),
                             "sensitivity": a.get("sensitivity"), "scope": a.get("scope")},
                    sensitivity=a.get("sensitivity", "internal"), scope="workspace",
                    source_id=a.get("artifact_id", ""),
                    token_estimate=_estimate_tokens(a),
                ))
        except Exception:
            _LOG.warning("context.loader: silent exception", exc_info=True)

    # 7. Job summary (P4 or P2)
    if include_jobs:
        try:
            from jobs.store import list_jobs
            jobs = list_jobs(ws_id=workspace_id, limit=5)
            for j in jobs:
                priority = 20 if context_ref and context_ref.ref_id == j.get("job_id") else 40
                items.append(ContextItem(
                    item_type="job_summary", source="job", priority=priority,
                    title=j.get("title", ""), summary=f"Status: {j.get('status','')}",
                    content={"job_id": j.get("job_id"), "job_type": j.get("job_type"),
                             "status": j.get("status"), "progress": j.get("progress", {})},
                    sensitivity="internal", scope="workspace",
                    source_id=j.get("job_id", ""),
                    token_estimate=_estimate_tokens(j),
                ))
        except Exception:
            _LOG.warning("context.loader: silent exception", exc_info=True)

    # 8. Report summary (P4)
    if include_reports:
        try:
            from artifacts.store import list_artifacts
            reports = list_artifacts(workspace_id, artifact_type="report", limit=5)
            for r in reports:
                items.append(ContextItem(
                    item_type="report_summary", source="report", priority=40,
                    title=r.get("title", ""), summary=r.get("summary", "")[:200],
                    content={"artifact_id": r.get("artifact_id"), "format": r.get("metadata", {}).get("format", "")},
                    sensitivity=r.get("sensitivity", "internal"), scope="workspace",
                    source_id=r.get("artifact_id", ""),
                    token_estimate=_estimate_tokens(r),
                ))
        except Exception:
            _LOG.warning("context.loader: silent exception", exc_info=True)

    return items
