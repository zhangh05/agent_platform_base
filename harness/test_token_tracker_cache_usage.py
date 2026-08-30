from agent.runtime import token_tracker


def test_cache_usage_is_persisted_and_aggregated(monkeypatch):
    rows = []
    monkeypatch.setattr(token_tracker, "append_usage", lambda _workspace, row: rows.append(row))
    monkeypatch.setattr(token_tracker, "read_usage", lambda _workspace: list(rows))

    token_tracker.record_llm_call(
        workspace_id="ws-cache",
        input_tokens=100,
        output_tokens=20,
        cache_creation_input_tokens=30,
        cache_read_input_tokens=60,
        prompt_cache_strategy="openai_automatic",
        prompt_profile={"stable_prefix_fingerprint": "abc", "selected_skill": False},
    )
    usage = token_tracker.get_usage("ws-cache")

    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 20
    assert usage["cache_creation_input_tokens"] == 30
    assert usage["cache_read_input_tokens"] == 60
    assert usage["cache_hit_ratio"] == 0.6
    assert usage["prompt_cache_strategies"] == {"openai_automatic": 1}
    assert usage["latest_prompt_profile"]["stable_prefix_fingerprint"] == "abc"
