# context/builder.py
"""Context builder — full pipeline: resolve -> load -> select -> compress -> assemble.

v3.1.0: Simplified. Content is always dict from UnifiedRetriever.
"""

from core.context.schemas import (ContextBundle, ExecutionContext, SafeLLMContext,
                              ContextBudget)
from core.context.resolver import resolve_context_ref
from core.context.loader import load_context_items
from core.context.selector import select_context_items
from core.context.compressor import compress_context_items


def build_context_bundle(workspace_id: str, user_input: str = "",
                         intent: str = "", capability_id: str = "",
                         payload: dict = None, context_ref: str = "",
                         ui_context: dict = None, budget: ContextBudget = None,
                         run_id: str = "", trace_id: str = "", job_id: str = "",
                         state_context: dict = None) -> ContextBundle:
    budget = budget or ContextBudget()
    warnings = []

    # 1. Resolve ref
    ref = resolve_context_ref(workspace_id, context_ref, payload, ui_context)

    # 2. Load raw items
    raw_items = load_context_items(
        workspace_id=workspace_id, context_ref=ref, intent=intent,
        payload=payload, capability_id=capability_id,
        user_input=user_input,
    )

    # 3. Select
    selected, sel_warnings = select_context_items(raw_items, intent, capability_id, budget)
    warnings.extend(sel_warnings)

    # 4. Compress (schema-driven stripping)
    compressed, budget, comp_warnings = compress_context_items(selected, budget, mode="safe_llm")
    warnings.extend(comp_warnings)

    # 5. Build execution context
    exec_ctx = ExecutionContext(
        workspace_id=workspace_id, run_id=run_id, job_id=job_id,
        trace_id=trace_id, capability_id=capability_id, intent=intent,
        payload_refs=list(payload.keys()) if payload else [],
        source_config_artifact_id=payload.get("artifact_id", "") if payload else "",
        selected_artifact_id=ref.ref_id if ref.ref_type == "artifact" else "",
    )

    # 6. Build safe LLM context — slim: request, workspace, artifacts, jobs only
    safe = SafeLLMContext(
        workspace_id=workspace_id, intent=intent, user_input=user_input,
        context_ref=ref,
        artifact_refs=[i.content for i in compressed if i.item_type == "artifact_summary"][:10],
        warnings=list(warnings),
    )

    # Load workspace state
    try:
        from storage.workspace_store import get_workspace_state
        ws = get_workspace_state(workspace_id)
        safe.last_result_summary = ws.get("last_result_summary", "")
        safe.job_summary = ws.get("job_stats", {})
    except Exception:
        pass

    bundle = ContextBundle(
        workspace_id=workspace_id, run_id=run_id, job_id=job_id,
        trace_id=trace_id, intent=intent, capability_id=capability_id,
        user_input=user_input, context_ref=ref,
        raw_items=[r.as_dict() for r in raw_items],
        selected_items=[s.as_dict() for s in selected],
        compressed_items=[c.as_dict() for c in compressed],
        execution_context=exec_ctx, safe_llm_context=safe,
        budget=budget, warnings=warnings,
    )

    return bundle
