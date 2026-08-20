"""Canonical location tool and generic batch contract tests."""

from __future__ import annotations

from agent.llm.schemas import LLMToolCall
from core.resolution.location_models import LocationCandidate, LocationResolution
from core.tools.schemas import ToolInvocation


def _resolution(query: str, *, ok: bool = True) -> LocationResolution:
    candidate = LocationCandidate(
        canonical_name=f"{query} canonical",
        latitude=10.0,
        longitude=20.0,
        provider="test_provider",
        country_code="ZZ",
        admin1="Test Region",
        place_type="city",
    ) if ok else None
    return LocationResolution(
        ok=ok,
        query=query,
        status="resolved" if ok else "location_ambiguous",
        resolved=candidate,
        candidates=(candidate,) if candidate else (),
        confidence=0.95 if ok else 0.5,
        provider_chain=("test_provider",),
    )


def test_canonical_location_tool_exposes_full_resolution_contract():
    from core.tools.canonical_registry import CANONICAL_REGISTRY

    entry = CANONICAL_REGISTRY["location.manage"]
    properties = entry.input_schema["properties"]
    assert properties["action"]["enum"] == ["resolve", "resolve_batch", "reverse"]
    assert properties["queries"]["maxItems"] == 20
    assert {"query", "queries", "latitude", "longitude", "country_code", "admin_hint"} <= set(properties)
    assert entry.execution_contract["batching"][0]["collection_arg"] == "queries"


def test_location_tool_is_visible_to_llm_and_capability_catalog():
    from agent.capabilities.catalog import get
    from core.tools.canonical_registry import to_openai_tools

    functions = {item["function"]["name"]: item["function"] for item in to_openai_tools()}
    location = functions["location__manage"]
    capability = get("location_resolution")

    assert "ambigu" in location["description"].lower()
    assert location["parameters"]["properties"]["action"]["enum"] == [
        "resolve", "resolve_batch", "reverse",
    ]
    assert capability is not None
    assert capability["recommended_tool_ids"] == ("location.manage",)


def test_location_resolve_handler_preserves_provider_evidence(monkeypatch):
    from core.tools.general_tools import location_tools

    monkeypatch.setattr(location_tools, "resolve_location", lambda query, **_kwargs: _resolution(query))
    result = location_tools.handle_location_manage(ToolInvocation(
        tool_id="location.manage",
        arguments={"action": "resolve", "query": "Any Place"},
        workspace_id="default",
    ))

    assert result["ok"] is True
    assert result["resolved"]["provider"] == "test_provider"
    assert result["resolved"]["admin1"] == "Test Region"
    assert result["confidence"] == 0.95


def test_location_batch_reports_exact_partial_coverage(monkeypatch):
    from core.tools.general_tools import location_tools

    monkeypatch.setattr(
        location_tools,
        "resolve_locations",
        lambda queries, **_kwargs: [_resolution(item, ok=item != "Ambiguous") for item in queries],
    )
    result = location_tools.handle_location_manage(ToolInvocation(
        tool_id="location.manage",
        arguments={"action": "resolve_batch", "queries": ["One", "Ambiguous", "Three"]},
        workspace_id="default",
    ))

    assert result["ok"] is True
    assert result["coverage_status"] == "partial"
    assert result["partial"] is True
    assert result["coverage"] == {
        "requested": ["One", "Ambiguous", "Three"],
        "resolved": ["One", "Three"],
        "unresolved": ["Ambiguous"],
        "requested_count": 3,
        "resolved_count": 2,
    }


def test_location_reverse_rejects_invalid_coordinate_before_provider(monkeypatch):
    from core.tools.general_tools import location_tools

    monkeypatch.setattr(
        location_tools,
        "reverse_location",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not call provider")),
    )
    result = location_tools.handle_location_manage(ToolInvocation(
        tool_id="location.manage",
        arguments={"action": "reverse", "latitude": 120, "longitude": 20},
    ))

    assert result["ok"] is False
    assert "outside the valid range" in result["error"]


def test_location_handler_rejects_malformed_batch_and_country_code():
    from core.tools.general_tools.location_tools import handle_location_manage

    bad_batch = handle_location_manage(ToolInvocation(
        tool_id="location.manage",
        arguments={"action": "resolve_batch", "queries": ["One", {"name": "Two"}]},
    ))
    bad_country = handle_location_manage(ToolInvocation(
        tool_id="location.manage",
        arguments={"action": "resolve", "query": "One", "country_code": "USA"},
    ))

    assert bad_batch["ok"] is False
    assert "must be a string" in bad_batch["error"]
    assert bad_country["ok"] is False
    assert "two-letter" in bad_country["error"]


def test_generic_compiler_batches_location_resolves_without_tool_specific_code():
    from agent.runtime.ssot_runtime import _build_ssot_runtime_tool_registry
    from core.runtime_engine.batch_compiler import compile_batchable_calls

    registry = _build_ssot_runtime_tool_registry(["location.manage"])
    calls = [
        LLMToolCall(
            id=f"place_{index}", name="location.manage",
            arguments={"action": "resolve", "query": f"Place {index}", "language": "en"},
        )
        for index in range(25)
    ]
    compiled, events = compile_batchable_calls(calls, registry)

    assert [len(call.arguments["queries"]) for call in compiled] == [20, 5]
    assert all(call.arguments["action"] == "resolve_batch" for call in compiled)
    assert sum(event["source_call_count"] for event in events) == 25


def test_location_actions_validate_through_ssot_semantic_validator():
    from core.runtime_engine.models import ExecutionNode
    from core.runtime_engine.semantic_validator import SemanticValidator

    valid = SemanticValidator().validate([
        ExecutionNode(
            id="resolve", tool="location.manage",
            args={"action": "resolve", "query": "Somewhere"},
        ),
        ExecutionNode(
            id="reverse", tool="location.manage",
            args={"action": "reverse", "latitude": 10.0, "longitude": 20.0},
        ),
    ])
    invalid = SemanticValidator().validate([
        ExecutionNode(id="missing", tool="location.manage", args={"action": "resolve"}),
    ])

    assert valid.valid is True
    assert invalid.valid is False
    assert any("query" in error.message for error in invalid.errors)
