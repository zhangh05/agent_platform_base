"""Layered memory architecture contracts."""

import json
from typing import get_args

import pytest

from storage.memory_governance import (
    MemoryRecord,
    MemorySource,
    MemoryStore,
    MemoryType,
    MemoryWriteGate,
    confirm_memory,
    expire_memory,
    reject_memory,
)


@pytest.fixture
def isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    try:
        import core.context.context_store as context_store
        context_store._stores.clear()
    except Exception:
        pass
    return tmp_path


class TestLayeredSchema:
    def test_only_current_long_term_layers_are_supported(self):
        types = set(get_args(MemoryType))
        assert types == {
            "core_rule", "semantic_fact", "episodic_case", "procedural_rule",
            "profile", "knowledge_note",
        }
        assert "device_state" not in types
        assert "operational_fact" not in types

    def test_runtime_sources_remain_explicit(self):
        assert {"subagent", "user", "manual_confirm", "agent_suggestion"}.issubset(
            set(get_args(MemorySource))
        )

    def test_old_memory_type_is_rejected(self, isolated_memory):
        result = MemoryWriteGate().write(MemoryRecord(
            workspace_id="ws_old_type",
            memory_type="device_state",
            content="PE1 GE0/0 is down",
        ))
        assert result["ok"] is False
        assert result["error"] == "invalid_memory_type"

    def test_old_disk_record_cannot_be_retrieved(self, isolated_memory):
        record = MemoryRecord(
            workspace_id="ws_old_disk",
            memory_type="operational_fact",
            status="active",
            content="legacy fact",
        )
        MemoryStore()._save(record)
        assert MemoryStore().list_retrievable("ws_old_disk") == []


class TestMemoryStatus:
    def test_status_uses_workspace_memory_setting(self, isolated_memory):
        from flask import Flask

        from backend.api.memory import handle_memory_status
        from storage.workspace_store import ensure_workspace, update_workspace_state

        ensure_workspace("ws_memory_status")
        update_workspace_state("ws_memory_status", {"memory_enabled": False})

        app = Flask(__name__)
        with app.test_request_context("/api/memory/status?workspace_id=ws_memory_status"):
            response = handle_memory_status()

        assert response.get_json()["enabled"] is False


class TestAuthorityGate:
    def test_explicit_user_core_rule_is_active(self, isolated_memory):
        record = MemoryRecord(
            workspace_id="ws_user_rule",
            memory_type="core_rule",
            source="user",
            status="active",
            confidence=1.0,
            content="只在开始时运行一次全量测试，单项失败后运行对应测试。",
            summary="测试执行规则",
            metadata={"memory_key": "user.testing_policy", "authority": "explicit_user", "authority_rank": 100},
        )
        result = MemoryWriteGate().write(record)
        assert result["ok"] is True
        assert result["status"] == "active"

    def test_verified_reflection_can_activate_semantic_fact(self, isolated_memory):
        record = MemoryRecord(
            workspace_id="ws_verified_fact",
            memory_type="semantic_fact",
            source="agent_suggestion",
            status="pending",
            confidence=0.9,
            content="PE1 uses loopback 2.2.2.9 as its BGP router ID.",
            summary="PE1 BGP router ID",
            citations=[{"event_id": "mex-123"}],
            metadata={
                "memory_key": "device.PE1.bgp_router_id",
                "authority": "verified_tool",
                "authority_rank": 70,
                "llm_score": 4,
                "llm_keep": True,
            },
        )
        result = MemoryWriteGate().write(record)
        assert result["status"] == "active"

    def test_unsupported_agent_inference_stays_pending(self, isolated_memory):
        record = MemoryRecord(
            workspace_id="ws_inference",
            memory_type="procedural_rule",
            source="agent_suggestion",
            content="When BGP flaps, inspect interface counters first.",
            summary="BGP flap diagnostic order",
            metadata={
                "memory_key": "bgp.flap.diagnostic_order",
                "authority": "agent_inference",
                "authority_rank": 30,
                "llm_score": 5,
                "llm_keep": True,
            },
        )
        result = MemoryWriteGate().write(record)
        assert result["ok"] is True
        assert result["status"] == "pending"

    def test_verified_agent_procedure_stays_pending_for_human_confirmation(self, isolated_memory):
        record = MemoryRecord(
            workspace_id="ws_procedure_confirmation",
            memory_type="procedural_rule",
            source="agent_suggestion",
            content="For DOCX images, use the managed attachment id.",
            summary="DOCX image extraction procedure",
            metadata={
                "memory_key": "file_analysis.docx_images",
                "authority": "verified_tool",
                "authority_rank": 70,
                "llm_score": 5,
                "llm_keep": True,
            },
        )
        assert MemoryWriteGate().write(record)["status"] == "pending"

    def test_subagent_never_activates(self, isolated_memory):
        record = MemoryRecord(
            workspace_id="ws_subagent",
            memory_type="procedural_rule",
            source="subagent",
            created_by="subagent",
            content="Check AS number before resetting a BGP session.",
            summary="BGP AS check",
            metadata={"authority": "verified_tool", "llm_score": 5, "llm_keep": True},
        )
        assert MemoryWriteGate().write(record)["status"] == "pending"

    def test_secret_is_rejected_before_redaction(self, isolated_memory):
        record = MemoryRecord(
            workspace_id="ws_secret",
            memory_type="knowledge_note",
            source="user",
            content="api_key=sk-abcdefghijklmnopqrstuvwxyz123456",
            summary="provider key",
        )
        result = MemoryWriteGate().write(record)
        assert result["ok"] is False
        assert result["rejected"] is True
        assert MemoryStore().get(record.workspace_id, record.memory_id) is None


class TestStructuredVersioning:
    def test_same_rule_key_supersedes_previous_user_rule(self, isolated_memory):
        gate = MemoryWriteGate()
        store = MemoryStore()
        old = MemoryRecord(
            workspace_id="ws_supersede",
            memory_type="core_rule",
            source="user",
            status="active",
            confidence=1.0,
            content="每次修改后都跑全量测试。",
            summary="旧测试规则",
            metadata={"memory_key": "user.testing_policy", "authority": "explicit_user"},
        )
        assert gate.write(old)["status"] == "active"
        new = MemoryRecord(
            workspace_id="ws_supersede",
            memory_type="core_rule",
            source="user",
            status="active",
            confidence=1.0,
            content="全量测试只跑一次，失败后只跑对应测试。",
            summary="新测试规则",
            metadata={"memory_key": "user.testing_policy", "authority": "explicit_user"},
        )
        result = gate.write(new)
        assert result["status"] == "active"
        assert store.get("ws_supersede", old.memory_id).status == "expired"
        assert store.get("ws_supersede", new.memory_id).metadata["supersedes_memory_id"] == old.memory_id

    def test_similar_text_with_different_keys_is_not_a_conflict(self, isolated_memory):
        gate = MemoryWriteGate()
        first = MemoryRecord(
            workspace_id="ws_keys",
            memory_type="semantic_fact",
            source="manual_confirm",
            status="active",
            content="PE1 uses router ID 2.2.2.9.",
            summary="PE1 router ID",
            metadata={"memory_key": "device.PE1.router_id"},
        )
        second = MemoryRecord(
            workspace_id="ws_keys",
            memory_type="semantic_fact",
            source="manual_confirm",
            status="active",
            content="PE2 uses router ID 2.2.2.10.",
            summary="PE2 router ID",
            metadata={"memory_key": "device.PE2.router_id"},
        )
        assert gate.write(first)["ok"] is True
        result = gate.write(second)
        assert result["status"] == "active"
        assert result.get("conflict") is False


class TestExplicitCommands:
    def test_remember_reads_user_text_only(self):
        from agent.runtime.memory_write.commands import parse_memory_command

        assert parse_memory_command("好的") is None
        command = parse_memory_command("以后全量测试只跑一次，失败后只跑相关测试。")
        assert command["action"] == "remember"
        assert command["memory_type"] == "core_rule"
        assert command["memory_key"] == "user.testing_policy"

    def test_conversational_future_phrase_is_not_memory(self):
        from agent.runtime.memory_write.commands import parse_memory_command

        assert parse_memory_command("以后再说") is None

    def test_forget_is_control_action_not_new_memory(self):
        from agent.runtime.memory_write.commands import parse_memory_command

        command = parse_memory_command("忘掉之前的测试规则")
        assert command == {
            "action": "forget",
            "query": "之前的测试规则",
            "reason": "explicit_user_forget_command",
        }

    def test_apply_remember_then_forget(self, isolated_memory):
        from agent.runtime.memory_write.commands import apply_memory_command, parse_memory_command

        created = apply_memory_command(
            parse_memory_command("以后全量测试只跑一次。"),
            workspace_id="ws_command",
            session_id="session-command",
            task_id="turn-command-1",
        )
        assert created["status"] == "active"
        forgotten = apply_memory_command(
            parse_memory_command("忘掉测试规则"),
            workspace_id="ws_command",
            session_id="session-command",
            task_id="turn-command-2",
        )
        assert forgotten["expired_memory_ids"] == [created["memory_id"]]


class TestExperienceJournal:
    def test_assistant_wording_cannot_create_user_rule(self, isolated_memory):
        from agent.runtime.ssot_runtime import _record_experience_and_maybe_reflect

        _record_experience_and_maybe_reflect(
            workspace_id="ws_assistant_only",
            session_id="session-assistant-only",
            task_id="turn-assistant-only",
            user_input="好的",
            assistant_response="以后应该优先检查接口状态。",
            tool_calls=[],
            task_ok=True,
        )
        assert MemoryStore().list_all("ws_assistant_only") == []

    def test_append_then_mark_processed_is_durable(self, isolated_memory):
        from agent.runtime.memory_write.event_log import (
            append_experience,
            mark_experiences_processed,
            pending_experiences,
        )

        event = append_experience(
            workspace_id="ws_journal",
            session_id="session-journal",
            task_id="turn-1",
            user_input="检查工作区文件",
            assistant_response="检查完成",
            tool_calls=[{"tool_id": "workspace.file", "ok": True, "summary": "file exists"}],
            task_ok=True,
        )
        pending = pending_experiences("ws_journal", "session-journal")
        assert [row["event_id"] for row in pending] == [event["event_id"]]
        assert pending[0]["tool_calls"][0]["summary"] == "file exists"
        mark_experiences_processed("ws_journal", "session-journal", [event["event_id"]])
        assert pending_experiences("ws_journal", "session-journal") == []

    def test_reflection_boundary_is_task_or_four_turns(self):
        from agent.runtime.memory_write.consolidator import should_consolidate

        assert should_consolidate([{"tool_calls": []}] * 3) is False
        assert should_consolidate([{"tool_calls": []}] * 4) is True
        assert should_consolidate([{"tool_calls": [{"ok": True}]}]) is True


class TestConsolidation:
    def test_parser_rejects_transient_runtime_state(self):
        from agent.runtime.memory_write.consolidator import _parse_operations

        result = _parse_operations(json.dumps([{
            "action": "create",
            "memory_type": "runtime_state",
            "content": "temporary task queue is busy",
            "summary": "queue busy",
            "score": 5,
            "confidence": 1.0,
            "evidence_event_ids": ["mex-1"],
        }]))
        assert result == []

    def test_one_batch_makes_one_llm_call(self, monkeypatch):
        from agent.llm.schemas import LLMResponse
        from agent.runtime.memory_write.consolidator import _reflect

        calls = []

        def fake_llm(**kwargs):
            calls.append(kwargs["task"])
            return LLMResponse(content="[]")

        monkeypatch.setattr("agent.llm.runtime.invoke_llm", fake_llm)
        monkeypatch.setattr("prompts.loader.render_prompt", lambda *args, **kwargs: type("P", (), {"text": "reflect"})())
        assert _reflect([{"event_id": "mex-1", "user_input": "hello"}], []) == []
        assert calls == ["memory_consolidation"]

    def test_reflection_uses_the_real_invoke_llm_contract(self, monkeypatch):
        """Prevent mocks with **kwargs from hiding production signature drift."""
        from agent.llm.schemas import LLMResponse
        from agent.runtime.memory_write.consolidator import _reflect

        captured = {}

        def strict_llm(
            task,
            messages=None,
            tools=None,
            state_or_context=None,
            safe_context=None,
            user_input="",
            extra=None,
            config_override=None,
        ):
            captured.update({
                "task": task,
                "messages": messages,
                "extra": extra,
                "config_override": config_override,
            })
            return LLMResponse(content="[]")

        monkeypatch.setattr("agent.llm.runtime.invoke_llm", strict_llm)
        monkeypatch.setattr("prompts.loader.render_prompt", lambda *args, **kwargs: type("P", (), {"text": "reflect"})())

        assert _reflect([{"event_id": "mex-1", "user_input": "hello"}], []) == []
        assert captured["task"] == "memory_consolidation"
        assert captured["config_override"] == {"temperature": 0.0, "max_tokens": 6000}
        assert captured["extra"]["stream_to_user"] is False
        assert captured["extra"]["request_metadata"] == {
            "memory_stage": "task_reflection",
            "event_count": 1,
        }

    def test_parser_strips_provider_reasoning_before_json(self):
        from agent.runtime.memory_write.consolidator import _parse_operations

        response = "<think>internal analysis [not output]</think>\n[]"
        assert _parse_operations(response) == []

    def test_provider_failure_keeps_experiences_pending(self, isolated_memory, monkeypatch):
        from agent.llm.schemas import LLMResponse
        from agent.runtime.memory_write.consolidator import consolidate_experiences
        from agent.runtime.memory_write.event_log import append_experience, pending_experiences

        append_experience(
            workspace_id="ws_retry",
            session_id="session-retry",
            task_id="turn-retry",
            user_input="检查文件",
            assistant_response="暂时无法分析",
            tool_calls=[{"tool_id": "workspace.file", "ok": False, "summary": "timeout"}],
            task_ok=False,
        )
        monkeypatch.setattr(
            "agent.llm.runtime.invoke_llm",
            lambda **kwargs: LLMResponse(error="provider unavailable"),
        )
        result = consolidate_experiences(
            workspace_id="ws_retry",
            session_id="session-retry",
            task_id="turn-retry",
        )
        assert result["status"] == "retry_pending"
        assert len(pending_experiences("ws_retry", "session-retry")) == 1


class TestLifecycle:
    def test_confirm_reject_and_expire(self, isolated_memory):
        store = MemoryStore()
        pending = MemoryRecord(
            workspace_id="ws_lifecycle",
            memory_type="procedural_rule",
            source="agent_suggestion",
            content="Inspect interface errors before resetting BGP.",
            summary="BGP diagnostic order",
            metadata={"llm_score": 3, "llm_keep": True, "authority": "agent_inference"},
        )
        assert MemoryWriteGate(store).write(pending)["status"] == "pending"
        assert confirm_memory("ws_lifecycle", pending.memory_id)["status"] == "active"
        assert reject_memory("ws_lifecycle", pending.memory_id)["status"] == "rejected"
        assert expire_memory("ws_lifecycle", pending.memory_id)["status"] == "expired"
