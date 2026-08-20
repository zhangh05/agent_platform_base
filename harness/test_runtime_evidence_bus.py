import asyncio

from agent.llm.schemas import LLMToolCall


def _image_part(file_id: str, image_index: int = 1) -> dict:
    return {
        "kind": "image",
        "reference": {"kind": "managed_file", "file_id": file_id},
        "consumer": "llm",
        "coverage": {"image_index": image_index},
    }


def test_evidence_ledger_validates_deduplicates_and_acknowledges_delivery():
    from core.runtime_engine.evidence import (
        evidence_summary,
        mark_evidence_delivered,
        pending_llm_evidence,
        register_evidence_parts,
    )

    extras = {}
    first = register_evidence_parts(
        extras,
        [_image_part("file_image"), _image_part("file_image"), {"kind": "image"}],
        source_tool="workspace.file",
        source_call_id="call_1",
    )
    assert len(first) == 1
    assert evidence_summary(extras) == {
        "registered": 1,
        "pending": 1,
        "delivered": 0,
        "by_kind": {"image": 1},
        "delivered_by_kind": {},
        "rejected": 1,
    }
    mark_evidence_delivered(extras, first)
    assert pending_llm_evidence(extras) == []
    assert evidence_summary(extras)["delivered_by_kind"] == {"image": 1}


def test_batch_compiler_uses_tool_declared_contract_and_preserves_scope():
    from core.runtime_engine.batch_compiler import compile_batchable_calls

    registry = {"workspace.file": {"metadata": {"batching": [{
        "source_action": "extract_document_image",
        "target_action": "extract_document_images",
        "group_by": ["file_id"],
        "index_arg": "image_index",
        "start_arg": "start_index",
        "limit_arg": "limit",
        "max_batch_size": 8,
    }]}}}
    calls = [
        LLMToolCall(
            id=f"call_{index}",
            name="workspace.file",
            arguments={"action": "extract_document_image", "file_id": "file_doc", "image_index": index},
        )
        for index in range(1, 13)
    ]
    compiled, events = compile_batchable_calls(calls, registry)
    assert [call.arguments for call in compiled] == [
        {"action": "extract_document_images", "file_id": "file_doc", "start_index": 1, "limit": 8},
        {"action": "extract_document_images", "file_id": "file_doc", "start_index": 9, "limit": 4},
    ]
    assert [event["source_call_count"] for event in events] == [8, 4]

    non_contiguous = [calls[0], calls[2]]
    unchanged, events = compile_batchable_calls(non_contiguous, registry)
    assert unchanged == non_contiguous
    assert events == []

    interleaved = [calls[0], LLMToolCall(id="other", name="web.manage", arguments={"action": "search"}), calls[1]]
    unchanged, events = compile_batchable_calls(interleaved, registry)
    assert unchanged == interleaved
    assert events == []


def test_batch_compiler_collects_contiguous_scalar_arguments_in_bounded_chunks():
    from core.runtime_engine.batch_compiler import compile_batchable_calls

    registry = {"web.manage": {"metadata": {"batching": [{
        "source_action": "weather",
        "target_action": "weather_batch",
        "group_by": ["days"],
        "collect_arg": "location",
        "collection_arg": "locations",
        "max_batch_size": 3,
    }]}}}
    calls = [
        LLMToolCall(
            id=f"weather_{index}", name="web.manage",
            arguments={"action": "weather", "location": city, "days": 10},
        )
        for index, city in enumerate(["上海", "南京", "杭州", "合肥", "宁波"])
    ]

    compiled, events = compile_batchable_calls(calls, registry)

    assert [call.arguments for call in compiled] == [
        {"action": "weather_batch", "days": 10, "locations": ["上海", "南京", "杭州"]},
        {"action": "weather_batch", "days": 10, "locations": ["合肥", "宁波"]},
    ]
    assert [event["source_call_count"] for event in events] == [3, 2]

    interleaved = [calls[0], LLMToolCall(
        id="search", name="web.manage", arguments={"action": "search", "query": "天气"},
    ), calls[1]]
    unchanged, events = compile_batchable_calls(interleaved, registry)
    assert unchanged == interleaved
    assert events == []


def test_canonical_weather_contract_compiles_25_locations_to_three_calls():
    from agent.runtime.ssot_runtime import _build_ssot_runtime_tool_registry
    from core.runtime_engine.batch_compiler import compile_batchable_calls

    registry = _build_ssot_runtime_tool_registry(["web.manage"])
    calls = [
        LLMToolCall(
            id=f"city_{index}", name="web.manage",
            arguments={"action": "weather", "location": f"城市{index}", "days": 10},
        )
        for index in range(25)
    ]

    compiled, events = compile_batchable_calls(calls, registry)

    assert len(compiled) == 3
    assert [len(call.arguments["locations"]) for call in compiled] == [10, 10, 5]
    assert all(call.arguments["action"] == "weather_batch" for call in compiled)
    assert sum(event["source_call_count"] for event in events) == 25


def test_response_quality_rejects_denial_of_delivered_visual_evidence():
    from core.runtime_engine.response_quality import validate_response_quality

    issues = validate_response_quality(
        "我无法查看图片中的内容。",
        evidence={"delivered_by_kind": {"image": 3}},
    )
    assert [issue.code for issue in issues] == ["DELIVERED_EVIDENCE_DENIED"]


def test_filestore_image_output_uses_typed_evidence_only():
    from core.runtime_engine.evidence import managed_image_evidence

    output = {"evidence_parts": [managed_image_evidence("file_image", image_index=2)]}
    assert "vision_attachment" not in output
    assert output["evidence_parts"][0]["reference"] == {
        "kind": "managed_file",
        "file_id": "file_image",
    }


def test_semantic_validator_rejects_managed_file_id_as_workspace_path():
    from core.runtime_engine.models import ExecutionNode
    from core.runtime_engine.semantic_validator import SemanticValidator

    registry = {"workspace.file": {"metadata": {"reference_kinds": {
        "read_image": {"filepath": "workspace_path"},
    }}}}
    result = SemanticValidator(registry).validate([ExecutionNode(
        id="call_bad_ref",
        tool="workspace.file",
        args={"action": "read_image", "filepath": "file_ca25780d719247b8"},
    )])
    assert result.valid is False
    assert "ARG_REFERENCE_KIND_MISMATCH" in [error.code for error in result.errors]


def test_query_loop_delivers_pending_evidence_on_any_llm_scope_once():
    from agent.llm.schemas import LLMMessage, LLMResponse
    from core.runtime_engine.evidence import (
        pending_llm_evidence,
        register_evidence_parts,
    )
    from core.runtime_engine.models import SSOTRuntimeConfig, StatelessContext
    from core.runtime_engine.query_loop import QueryLoop

    extras = {}
    evidence_ids = register_evidence_parts(
        extras,
        [_image_part("file_ca25780d719247b8")],
        source_tool="workspace.file",
        source_call_id="call_extract",
    )
    captured = {}

    def fake_llm(**kwargs):
        captured.update(kwargs)
        return LLMResponse(
            content="看到了图片",
            metadata={"delivered_evidence_ids": evidence_ids},
        )

    loop = QueryLoop(
        SSOTRuntimeConfig(),
        {},
        object(),
        llm_invoke=fake_llm,
    )
    context = StatelessContext(
        workspace_id="default",
        session_id="session_1",
        request_id="request_1",
        user_input="分析图片",
        extras=extras,
    )
    response = asyncio.run(loop._call_llm(
        [
            LLMMessage(role="system", content="system"),
            LLMMessage(role="user", content="continuation"),
        ],
        context,
    ))
    assert response.content == "看到了图片"
    assert captured["extra"]["evidence_parts"][0]["evidence_id"] == evidence_ids[0]
    assert pending_llm_evidence(extras) == []


def test_evidence_ledger_rejects_inline_data_before_llm_delivery():
    from core.runtime_engine.evidence import (
        evidence_summary,
        pending_llm_evidence,
        register_evidence_parts,
    )

    secret = "sk-test-secret-abcdefghijklmnopqrstuvwxyz"
    extras = {}
    registered = register_evidence_parts(
        extras,
        [{
            "kind": "text",
            "reference": {"kind": "inline", "content": f"token={secret}"},
            "consumer": "llm",
        }],
        source_tool="web.manage",
        source_call_id="call_untrusted",
    )

    assert registered == []
    assert pending_llm_evidence(extras) == []
    assert secret not in str(extras)
    assert evidence_summary(extras)["rejected"] == 1
