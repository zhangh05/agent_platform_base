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


def test_successful_text_tool_results_share_one_canonical_evidence_ledger():
    from core.runtime_engine.evidence import evidence_manifest, evidence_summary, register_tool_evidence
    from core.runtime_engine.query_loop import StreamingToolResult

    extras = {}
    results = [
        StreamingToolResult(
            tool_name="network.operations.device.manage",
            call_id=f"call-{index}",
            ok=True,
            output={
                "status": "succeeded",
                "device_id": f"device-{index}",
                "facts": {"current_config": {"status": "collected"}},
                "output": {"display current-configuration": "mpls lsr-id 10.0.0.1\npeer 10.0.0.2 as-number 65001\n"},
            },
        )
        for index in range(6)
    ]

    ids = register_tool_evidence(extras, results, user_input="分析 MPLS VPN option C")

    assert len(ids) == 6
    assert evidence_summary(extras)["registered"] == 6
    assert evidence_summary(extras)["delivered"] == 6
    manifest = evidence_manifest(extras)
    assert len(manifest) == 6
    assert all(item["kind"] == "tool_result" for item in manifest)
    assert all(item["reference"]["kind"] == "tool_result" for item in manifest)


def test_large_text_evidence_projection_keeps_query_relevant_sections():
    from core.runtime_engine.evidence import evidence_manifest, register_tool_evidence
    from core.runtime_engine.query_loop import StreamingToolResult

    config = "\n".join(
        [f"interface GigabitEthernet0/0/{index}" for index in range(800)]
        + [
            "mpls lsr-id 10.0.0.1",
            "bgp 65000",
            "peer 10.0.0.2 as-number 65001",
            "ipv4-family labeled-unicast",
        ]
        + [f"description tail-{index}" for index in range(800)]
    )
    extras = {}
    register_tool_evidence(
        extras,
        [StreamingToolResult(
            tool_name="network.operations.device.manage",
            call_id="call-config",
            ok=True,
            output={"status": "succeeded", "output": {"display current-configuration": config}},
        )],
        user_input="分析 MPLS VPN BGP labeled-unicast",
    )

    rendered = str(evidence_manifest(extras)[0]["projection"])
    assert "mpls lsr-id" in rendered
    assert "ipv4-family labeled-unicast" in rendered
