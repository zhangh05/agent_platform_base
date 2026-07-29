# agent/runtime_status.py
"""Agent runtime status."""

def get_runtime_status() -> dict:
    """Report agent runtime status."""
    from agent.capabilities.catalog import list_enabled

    from agent.llm.runtime import get_llm_status
    llm_status = get_llm_status()

    # Supported intents.
    supported_intents = [
        "assistant_chat", "knowledge_query", "context_qa",
        "memory_search", "skill_query", "module_query",
        "data_analysis", "report_drafting",
    ]

    return {
        "runtime_engine": "SSOTRuntimeEngine",
        "llm_enabled": llm_status["enabled"],
        "llm_connected": llm_status["connected"],
        "llm_provider": llm_status["provider"],
        "llm_model": llm_status["model"],
        "llm_safe_mode": llm_status["safe_mode"],
        "llm_allowed_tasks": llm_status["allowed_tasks"],
        "llm_blocked_tasks": llm_status["blocked_tasks"],
        "llm_config_source": llm_status["config_source"],
        "llm_policy_red_lines": llm_status["red_lines"],
        "supported_intents": supported_intents,
        "enabled_capabilities": [item["capability_id"] for item in list_enabled()],
    }
