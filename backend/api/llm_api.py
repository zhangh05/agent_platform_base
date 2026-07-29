# backend/api/llm_api.py
"""LLM API endpoints — status, test, config CRUD, per-provider management.

LLM configuration is GLOBAL — it does NOT vary by workspace or session.
Each provider has its own config file: config/providers/<provider>.json
Active provider is tracked in: config/providers/_active
"""

import logging

from flask import request, jsonify
from agent.llm.client import LLMClient
from agent.llm.settings import (
    load_llm_settings, save_llm_settings, delete_llm_settings,
    sanitize_llm_settings, validate_llm_settings,
    get_llm_setting_path,
)
from agent.llm.provider_store import (
    list_providers, load_provider_config, save_provider_config,
    get_active_provider, set_active_provider, delete_provider_config,
    PROVIDER_PRESETS,
)


def handle_llm_status():
    from agent.llm.runtime import get_llm_status
    status = get_llm_status() or {}
    settings = load_llm_settings()
    status["settings_file_exists"] = settings is not None
    status["enabled_by_ui"] = settings.get("enabled", False) if settings else None
    if settings:
        status["key_configured"] = bool(settings.get("api_key"))
        status["key_preview"] = sanitize_llm_settings(settings).get("key_preview")
        status["config_source"] = "ui_settings" if status["settings_file_exists"] else status.get("config_source", "default")
    return jsonify(status)


def handle_llm_config_get():
    # LLM config is global — reject any attempt to scope by workspace
    if request.args.get("workspace_id"):
        return jsonify({
            "ok": False,
            "error": "LLM config is global and does not accept workspace_id parameter. "
                     "Configuration applies to all workspaces."
        }), 400

    # Use per-provider active config
    try:
        active_id = get_active_provider()
        cfg = load_provider_config(active_id)
        result = sanitize_settings(cfg)
        result["provider"] = active_id
        result["is_active"] = True
        result["config_path"] = str(f"config/providers/{active_id}.json")
        result["global"] = True
        return jsonify(result)
    except Exception:
        logging.getLogger(__name__).warning("LLM config read failed", exc_info=True)

    return jsonify({
        "enabled": False, "provider": "disabled",
        "config_path": "config/providers/<provider>.json",
        "global": True,
        "note": "No UI settings saved yet. Use POST /api/agent/llm/config to configure. "
                "Configuration is global — one setting for all workspaces."
    })


def handle_llm_config_post():
    data = request.get_json(silent=True) or {}
    # LLM config is global — reject workspace_id
    if data.get("workspace_id"):
        return jsonify({
            "ok": False,
            "error": "LLM config is global and does not accept workspace_id. "
                     "Configuration applies to all workspaces."
        }), 400
    errors = validate_llm_settings(data)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    settings = save_llm_settings(data)
    result = sanitize_llm_settings(settings)
    result["config_path"] = get_llm_setting_path()
    result["global"] = True
    return jsonify({"ok": True, "config": result})


def handle_llm_config_delete():
    ok = delete_llm_settings()
    return jsonify({"ok": True, "deleted": ok})


def handle_llm_test():
    """Test one provider's real chat-completions transport.
    
    Accepts config overrides for testing draft values before saving:
      { message, base_url, model, api_key, provider }
    """
    data = request.get_json(silent=True) or {}
    message = data.get("message", "")

    overrides = {}
    for k in ("base_url", "model", "api_key", "provider"):
        if data.get(k):
            overrides[k] = data[k]
    client = LLMClient(overrides=overrides if overrides else None)
    output = client.probe(message or "Reply with OK.")
    try:
        from agent.llm.config import record_recent_failure, record_recent_success
        if output["ok"]:
            record_recent_success()
        elif output["error"]:
            record_recent_failure(output["error"], "provider_error")
    except Exception:
        logging.getLogger(__name__).warning("Cannot persist LLM probe status", exc_info=True)

    return jsonify({
        "ok": output["ok"],
        "provider": output["provider"],
        "model": output["model"],
        "llm_used": output["ok"],
        "config_source": client.provider_info().get("config_source", "ui_settings"),
        "policy_pass": True,
        "response": output["response"],
        "safe_to_show": True,
        "fallback_reason": output["error"],
        "warnings": [],
        "metadata": output["metadata"],
    })


# ═══════════════════════════════════════════════════
# Per-provider config endpoints (new)
# ═══════════════════════════════════════════════════


def _sanitize_provider(data: dict) -> dict:
    """Sanitize provider config for API response (mask API key)."""
    key = data.get("api_key", "")
    result = dict(data)
    result["key_configured"] = bool(key)
    result["key_preview"] = mask_key(key) if key else None
    result["api_key"] = None
    return result


def mask_key(key: str):
    if not key or len(key) < 8:
        return None
    return key[:4] + "****" + key[-4:]


def sanitize_settings(data: dict) -> dict:
    """Sanitize provider config for /config endpoint."""
    return _sanitize_provider(data)


def handle_providers_list():
    """GET /api/agent/llm/providers — list all provider configs."""
    providers = list_providers()
    return jsonify({
        "ok": True,
        "providers": providers,
        "active": get_active_provider(),
    })


def handle_provider_get(provider_id: str):
    """GET /api/agent/llm/providers/<id> — get one provider config."""
    if provider_id not in PROVIDER_PRESETS:
        return jsonify({"ok": False, "error": f"unknown provider: {provider_id}"}), 404
    cfg = load_provider_config(provider_id)
    result = _sanitize_provider(cfg)
    result["is_active"] = (provider_id == get_active_provider())
    return jsonify({"ok": True, "config": result})


def handle_provider_save(provider_id: str):
    """POST /api/agent/llm/providers/<id> — save one provider config."""
    if provider_id not in PROVIDER_PRESETS:
        return jsonify({"ok": False, "error": f"unknown provider: {provider_id}"}), 404

    data = request.get_json(silent=True) or {}

    errors = validate_llm_settings({**data, "provider": provider_id})
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    cfg = save_provider_config(provider_id, data)
    result = _sanitize_provider(cfg)
    result["is_active"] = (provider_id == get_active_provider())
    return jsonify({"ok": True, "config": result})


def handle_llm_activate():
    """POST /api/agent/llm/activate — activate a provider (save + switch)."""
    data = request.get_json(silent=True) or {}
    provider_id = data.get("provider", "")

    if not provider_id or provider_id not in PROVIDER_PRESETS:
        return jsonify({"ok": False, "error": f"invalid provider: {provider_id}"}), 400

    # Optionally save config fields before activating
    save_fields = {}
    for key in ("enabled", "base_url", "model", "temperature", "max_tokens",
                 "safe_mode", "api_key"):
        if key in data:
            save_fields[key] = data[key]
    if data.get("clear_api_key"):
        save_fields["clear_api_key"] = True

    if save_fields:
        save_provider_config(provider_id, save_fields)

    set_active_provider(provider_id)
    cfg = load_provider_config(provider_id)
    result = _sanitize_provider(cfg)
    result["is_active"] = True
    result["active_provider"] = provider_id

    return jsonify({
        "ok": True,
        "config": result,
        "active": provider_id,
        "message": f"Switched to {PROVIDER_PRESETS[provider_id]['label']}",
    })


def handle_provider_delete(provider_id: str):
    """DELETE /api/agent/llm/providers/<id> — reset provider to defaults."""
    if provider_id not in PROVIDER_PRESETS:
        return jsonify({"ok": False, "error": f"unknown provider: {provider_id}"}), 404
    ok = delete_provider_config(provider_id)
    return jsonify({"ok": True, "deleted": ok})
