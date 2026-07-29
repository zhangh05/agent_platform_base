# harness/test_llm_settings_runtime_wiring.py
"""LLM provider-store runtime wiring tests."""

import json


def _isolate_provider_store(monkeypatch, tmp_path):
    import agent.llm.provider_store as store

    providers = tmp_path / "config" / "providers"
    monkeypatch.setattr(store, "PROVIDERS_DIR", providers)
    monkeypatch.setattr(store, "ACTIVE_FILE", providers / "_active")
    return providers


class TestLLMProviderSettings:
    def test_resolve_uses_active_provider(self, monkeypatch, tmp_path):
        providers = _isolate_provider_store(monkeypatch, tmp_path)
        providers.mkdir(parents=True)
        (providers / "minimax.json").write_text(json.dumps({
            "enabled": True,
            "provider": "minimax",
            "base_url": "https://api.minimaxi.com/v1",
            "model": "MiniMax-M3",
            "api_key": "sk-test12345678",
        }))
        (providers / "_active").write_text("minimax")

        from agent.llm.config import resolve_provider_config
        cfg = resolve_provider_config()

        assert cfg["enabled"] is True
        assert cfg["provider"] == "minimax"
        assert cfg["config_source"] == "ui_settings"
        assert cfg["model"] == "MiniMax-M3"

    def test_env_fallback_when_no_active_provider(self, monkeypatch, tmp_path):
        _isolate_provider_store(monkeypatch, tmp_path)
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-env-key123456")
        monkeypatch.setenv("AGENT_PLATFORM_LLM_ENABLED", "true")

        from agent.llm.config import resolve_provider_config
        cfg = resolve_provider_config()

        assert cfg["config_source"] in ("env", "file", "default")
        assert cfg["key_loaded"] is True

    def test_save_settings_writes_provider_config(self, monkeypatch, tmp_path):
        providers = _isolate_provider_store(monkeypatch, tmp_path)
        from agent.llm.settings import load_llm_settings, save_llm_settings

        saved = save_llm_settings({
            "enabled": True,
            "provider": "minimax",
            "api_key": "sk-test12345678",
            "model": "",
        })

        assert saved["model"] == "MiniMax-M3"
        assert (providers / "minimax.json").is_file()
        assert (providers / "_active").read_text() == "minimax"
        assert load_llm_settings()["model"] == "MiniMax-M3"

    def test_effective_config_has_key_source(self, monkeypatch, tmp_path):
        _isolate_provider_store(monkeypatch, tmp_path)
        from agent.llm.settings import resolve_effective_llm_config, save_llm_settings

        save_llm_settings({
            "enabled": True,
            "provider": "minimax",
            "model": "MiniMax-M3",
            "api_key": "sk-test12345678",
        })

        cfg = resolve_effective_llm_config()
        assert cfg["key_source"] == "ui_settings"


class TestKeyResolverMask:
    def test_key_resolver_mask(self):
        from agent.llm.key_resolver import mask_secret
        result = mask_secret("sk-test-key-12345678")
        assert "****" in result
        assert result.startswith("sk-t")

    def test_key_resolver_empty(self):
        from agent.llm.key_resolver import mask_secret
        assert mask_secret("") == ""
        assert mask_secret(None) == ""


class TestMiniMaxM3NoResidue:
    def test_no_m1_in_settings_default(self):
        from agent.llm.settings import resolve_effective_llm_config
        cfg = resolve_effective_llm_config()
        model = cfg.get("model", "")
        if model and "M1" in model:
            assert model == "MiniMax-M3"

    def test_no_m1_in_config_default(self):
        from agent.llm.config import load_llm_config
        cfg = load_llm_config()
        for name, provider in cfg.get("providers", {}).items():
            model = provider.get("model", "")
            if model == "MiniMax-M1":
                raise AssertionError(f"Provider {name} has MiniMax-M1 as model")
