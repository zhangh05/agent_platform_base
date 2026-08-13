from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _template(name: str) -> str:
    return (ROOT / "prompts" / "templates" / name).read_text(encoding="utf-8")


def test_runtime_prompt_has_scene_aware_response_policy():
    from core.runtime_engine.prompt_contract import CAPABILITY_PLAYBOOKS, RUNTIME_SYSTEM_PROMPT

    assert "Adaptive response mode" in RUNTIME_SYSTEM_PROMPT
    assert "Simple fact" in RUNTIME_SYSTEM_PROMPT
    assert "Correction, objection, or short follow-up" in RUNTIME_SYSTEM_PROMPT
    assert "Tool-backed result" in RUNTIME_SYSTEM_PROMPT
    assert "Failure, blocker, partial, or zero-result" in RUNTIME_SYSTEM_PROMPT
    assert "recorded configuration, observed live" in CAPABILITY_PLAYBOOKS["structured_operations"]
    assert "Avoid rigid section templates" in RUNTIME_SYSTEM_PROMPT


def test_prompt_templates_prefer_adaptive_shape_over_rigid_reports():
    templates = {
        "assistant_chat.md": "choose the lightest useful",
        "response_compose.md": "Choose an adaptive response shape",
        "result_summarize.md": "Choose the lightest useful shape",
        "job_failure_explain.md": "Choose the lightest useful shape",
        "context_qa.md": "Choose the lightest useful response shape",
    }
    for filename, phrase in templates.items():
        text = _template(filename)
        assert phrase in text
        assert "Preserve exact technical notation" in text


def test_job_failure_prompt_no_longer_forces_five_section_output():
    text = _template("job_failure_explain.md")

    assert "For a simple or obvious failure, answer in a short paragraph" in text
    assert "Provide:\n1. Failure summary" not in text
