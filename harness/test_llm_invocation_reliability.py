# harness/test_llm_invocation_reliability.py
"""LLM Invocation Reliability — policy never blocks provider call.

目标（对应 6 个测试）:
1. prompt_input_policy_fail_does_not_block_provider_call
2. prompt_text_policy_fail_does_not_block_provider_call
3. output_policy_fail_returns_answer_with_warning
4. response_policy_fail_returns_answer_with_warning
5. provider_error_returns_redacted_real_error
6. disabled_or_missing_key_still_fails_normally
"""

import pytest
from unittest.mock import MagicMock, patch


def _make_safe_context_with_secret():
    return {
        "workspace_id": "default",
        "session_id": "s1",
        "raw_payload": "dummy",
        "secret": "do-not-leak",
    }


def _mock_prompt_runtime(prompt_id="p1", version="v1", rendered_text="rendered prompt"):
    """Returns a list of active patch context managers for prompt runtime (all OK)."""
    return [
        patch("prompts.loader.get_prompt_by_task",
               return_value=MagicMock(prompt_id=prompt_id, version=version)),
        patch("prompts.renderer.render_prompt",
               return_value=MagicMock(text=rendered_text)),
    ]


def _mock_policy_all_ok():
    """Patch all policy checks to PASS (ok=True)."""
    return [
        patch("prompts.policy.detect_prompt_injection",
               return_value=MagicMock(injection_detected=False, warnings=[])),
        patch("prompts.policy.check_prompt_input",
               return_value=MagicMock(ok=True, issues=[])),
        patch("prompts.policy.check_prompt_text",
               return_value=MagicMock(ok=True, issues=[])),
        patch("prompts.policy.check_prompt_output",
               return_value=MagicMock(ok=True, issues=[])),
        patch("agent.llm.policy.check_request",
               return_value=MagicMock(allowed=True, violations=[])),
        patch("agent.llm.policy.check_response",
               return_value=MagicMock(allowed=True, violations=[])),
    ]


# ────────────────────────────────────────────────────────────
# Test 1 & 2: prompt policy fail → still calls provider
# ────────────────────────────────────────────────────────────

class TestPromptInputPolicyFailDoesNotBlockProviderCall:
    """prompt_input_policy_fail_does_not_block_provider_call"""

    def test_prompt_input_policy_fail_still_calls_provider(self):
        from agent.llm.schemas import LLMResponse

        mock_resp = LLMResponse(
            content="LLM answer despite policy failure",
            provider="mock", model="mock",
        )

        with patch("prompts.loader.get_prompt_by_task",
                   return_value=MagicMock(prompt_id="p1", version="v1")):
            with patch("prompts.renderer.render_prompt",
                       return_value=MagicMock(text="rendered")):
                with patch("prompts.policy.detect_prompt_injection",
                           return_value=MagicMock(injection_detected=False, warnings=[])):
                    with patch("prompts.policy.check_prompt_input",
                               return_value=MagicMock(ok=False,
                                                       issues=[{"rule": "input_block"}])):
                        with patch("prompts.policy.check_prompt_text",
                                   return_value=MagicMock(ok=True, issues=[])):
                            with patch("agent.llm.provider.generate",
                                       return_value=mock_resp) as mock_gen:
                                with patch("agent.llm.policy.check_request",
                                           return_value=MagicMock(allowed=True,
                                                                     violations=[])):
                                    from agent.llm.runtime import safe_generate
                                    output = safe_generate(
                                        "result_summarize",
                                        safe_context=_make_safe_context_with_secret(),
                                    )

                                    mock_gen.assert_called_once()
                                    assert output.llm_used is True
                                    assert "despite policy failure" in output.answer

    def test_prompt_input_policy_fail_recorded_in_metadata(self):
        from agent.llm.schemas import LLMResponse

        mock_resp = LLMResponse(content="answer", provider="mock", model="mock")

        with patch("prompts.loader.get_prompt_by_task",
                   return_value=MagicMock(prompt_id="p1", version="v1")):
            with patch("prompts.renderer.render_prompt",
                       return_value=MagicMock(text="rendered")):
                with patch("prompts.policy.detect_prompt_injection",
                           return_value=MagicMock(injection_detected=False, warnings=[])):
                    with patch("prompts.policy.check_prompt_input",
                               return_value=MagicMock(ok=False,
                                                       issues=[{"rule": "test_rule"}])):
                        with patch("prompts.policy.check_prompt_text",
                                   return_value=MagicMock(ok=True, issues=[])):
                            with patch("agent.llm.provider.generate",
                                       return_value=mock_resp):
                                with patch("agent.llm.policy.check_request",
                                           return_value=MagicMock(allowed=True,
                                                                     violations=[])):
                                    from agent.llm.runtime import safe_generate
                                    output = safe_generate("result_summarize",
                                                          safe_context={})

                                    meta = output.metadata or {}
                                    warns = output.warnings or []
                                    has_policy_info = (
                                        not meta.get("prompt_input_ok", True)
                                        or any("policy" in str(w).lower()
                                               for w in warns)
                                    )
                                    assert has_policy_info, \
                                        "prompt_input policy failure not recorded"


class TestPromptTextPolicyFailDoesNotBlockProviderCall:
    """prompt_text_policy_fail_does_not_block_provider_call"""

    def test_prompt_text_policy_fail_still_calls_provider(self):
        from agent.llm.schemas import LLMResponse

        mock_resp = LLMResponse(
            content="answer despite text policy fail",
            provider="mock", model="mock",
        )

        with patch("prompts.loader.get_prompt_by_task",
                   return_value=MagicMock(prompt_id="p1", version="v1")):
            with patch("prompts.renderer.render_prompt",
                       return_value=MagicMock(text="rendered")):
                with patch("prompts.policy.detect_prompt_injection",
                           return_value=MagicMock(injection_detected=False, warnings=[])):
                    with patch("prompts.policy.check_prompt_input",
                               return_value=MagicMock(ok=True, issues=[])):
                        with patch("prompts.policy.check_prompt_text",
                                   return_value=MagicMock(ok=False,
                                                           issues=[{"rule": "text_block"}])):
                            with patch("agent.llm.provider.generate",
                                       return_value=mock_resp) as mock_gen:
                                with patch("agent.llm.policy.check_request",
                                           return_value=MagicMock(allowed=True,
                                                                     violations=[])):
                                    from agent.llm.runtime import safe_generate
                                    output = safe_generate("result_summarize",
                                                          safe_context={})

                                    mock_gen.assert_called_once()
                                    assert output.llm_used is True
                                    assert "despite text policy fail" in output.answer


# ────────────────────────────────────────────────────────────
# Test 3 & 4: output/response policy fail → answer + warning
# ────────────────────────────────────────────────────────────

class TestOutputPolicyFailReturnsAnswerWithWarning:
    """output_policy_fail_returns_answer_with_warning"""

    def test_output_policy_fail_still_returns_answer(self):
        from agent.llm.schemas import LLMResponse

        mock_resp = LLMResponse(
            content="answer with output policy issue",
            provider="mock", model="mock",
        )

        with patch("prompts.loader.get_prompt_by_task",
                   return_value=MagicMock(prompt_id="p1", version="v1")):
            with patch("prompts.renderer.render_prompt",
                       return_value=MagicMock(text="rendered")):
                with patch("prompts.policy.check_prompt_output",
                           return_value=MagicMock(ok=False,
                                                   issues=[{"rule": "output_block"}])):
                    with patch("agent.llm.provider.generate",
                               return_value=mock_resp):
                        with patch("agent.llm.policy.check_request",
                                   return_value=MagicMock(allowed=True,
                                                           violations=[])):
                            with patch("agent.llm.policy.check_response",
                                       return_value=MagicMock(allowed=True,
                                                               violations=[])):
                                from agent.llm.runtime import safe_generate
                                output = safe_generate("result_summarize",
                                                      safe_context={})

                                assert output.llm_used is True
                                assert "answer with output policy issue" in output.answer
                                assert len(output.warnings) > 0

    def test_output_policy_fail_metadata_has_flag(self):
        from agent.llm.schemas import LLMResponse

        mock_resp = LLMResponse(content="answer", provider="mock", model="mock")

        with patch("prompts.loader.get_prompt_by_task",
                   return_value=MagicMock(prompt_id="p1", version="v1")):
            with patch("prompts.renderer.render_prompt",
                       return_value=MagicMock(text="rendered")):
                with patch("prompts.policy.check_prompt_output",
                           return_value=MagicMock(ok=False, issues=[{"rule": "r"}])):
                    with patch("agent.llm.provider.generate",
                               return_value=mock_resp):
                        with patch("agent.llm.policy.check_request",
                                   return_value=MagicMock(allowed=True,
                                                           violations=[])):
                            with patch("agent.llm.policy.check_response",
                                       return_value=MagicMock(allowed=True,
                                                               violations=[])):
                                from agent.llm.runtime import safe_generate
                                output = safe_generate("result_summarize",
                                                      safe_context={})

                                meta = output.metadata or {}
                                assert meta.get("output_policy_ok") is False


class TestResponsePolicyFailReturnsAnswerWithWarning:
    """response_policy_fail_returns_answer_with_warning"""

    def test_response_policy_fail_still_returns_answer(self):
        from agent.llm.schemas import LLMResponse

        mock_resp = LLMResponse(
            content="answer with response policy issue",
            provider="mock", model="mock",
        )

        with patch("prompts.loader.get_prompt_by_task",
                   return_value=MagicMock(prompt_id="p1", version="v1")):
            with patch("prompts.renderer.render_prompt",
                       return_value=MagicMock(text="rendered")):
                with patch("agent.llm.policy.check_response",
                           return_value=MagicMock(allowed=False,
                                                   reason="resp_block",
                                                   violations=["v"])):
                    with patch("agent.llm.provider.generate",
                               return_value=mock_resp):
                        with patch("agent.llm.policy.check_request",
                                   return_value=MagicMock(allowed=True,
                                                           violations=[])):
                            from agent.llm.runtime import safe_generate
                            output = safe_generate("result_summarize",
                                                    safe_context={})

                            assert output.llm_used is True
                            assert "answer with response policy issue" in output.answer
                            assert len(output.warnings) > 0

    def test_response_policy_fail_metadata_has_flag(self):
        from agent.llm.schemas import LLMResponse

        mock_resp = LLMResponse(content="answer", provider="mock", model="mock")

        with patch("prompts.loader.get_prompt_by_task",
                   return_value=MagicMock(prompt_id="p1", version="v1")):
            with patch("prompts.renderer.render_prompt",
                       return_value=MagicMock(text="rendered")):
                with patch("agent.llm.policy.check_response",
                           return_value=MagicMock(allowed=False,
                                                   reason="r",
                                                   violations=["v"])):
                    with patch("agent.llm.provider.generate",
                               return_value=mock_resp):
                        with patch("agent.llm.policy.check_request",
                                   return_value=MagicMock(allowed=True,
                                                           violations=[])):
                            from agent.llm.runtime import safe_generate
                            output = safe_generate("result_summarize",
                                                    safe_context={})

                            meta = output.metadata or {}
                            assert meta.get("response_policy_ok") is False


# ────────────────────────────────────────────────────────────
# Test 5: provider error → real redacted error in answer
# ────────────────────────────────────────────────────────────

class TestProviderErrorReturnsRedactedRealError:
    """provider_error_returns_redacted_real_error"""

    def test_provider_exception_returns_redacted_error_in_answer(self):
        """When provider raises, answer must contain redacted real error."""
        with patch("agent.llm.provider.generate",
                   side_effect=ConnectionError(
                       "Connection refused: api.minimax.chat:443"
                   )):
            from agent.llm.runtime import safe_generate
            output = safe_generate("result_summarize", safe_context={})

            assert output.llm_used is False
            assert "Connection refused" in output.answer \
                or "Provider error" in output.answer
            assert "I apologize" not in output.answer
            assert "technical difficulties" not in output.answer

    def test_provider_response_error_returns_redacted_error_in_answer(self):
        from agent.llm.schemas import LLMResponse

        mock_resp = LLMResponse(
            content="", error="401 Client Error: Unauthorized",
            provider="mock", model="mock",
        )

        with patch("agent.llm.provider.generate", return_value=mock_resp):
            from agent.llm.runtime import safe_generate
            output = safe_generate("result_summarize", safe_context={})

            assert output.llm_used is False
            assert ("Unauthorized" in output.answer
                    or "401" in output.answer
                    or "Provider unavailable" in output.answer)
            assert "I apologize" not in output.answer

    def test_provider_error_redacts_secret(self):
        with patch("agent.llm.provider.generate",
                   side_effect=OSError(
                       "Authorization failed: Bearer sk-1234567890abcdef"
                   )):
            from agent.llm.runtime import safe_generate
            output = safe_generate("result_summarize", safe_context={})

            assert ("[REDACTED]" in output.answer
                    or "[REDACTED]" in output.fallback_reason)


# ────────────────────────────────────────────────────────────
# Test 6: disabled or missing key → still fails normally
# ────────────────────────────────────────────────────────────

class TestDisabledOrMissingKeyStillFailsNormally:
    """disabled_or_missing_key_still_fails_normally"""

    def test_disabled_provider_returns_disabled_message(self):
        with patch("agent.llm.config.resolve_provider_config") as mock_cfg:
            mock_cfg.return_value = {
                "enabled": False,
                "provider_type": "disabled",
                "provider": "disabled",
                "model": "",
            }
            from agent.llm.runtime import safe_generate
            output = safe_generate("result_summarize", safe_context={})

            assert output.llm_used is False
            assert ("disabled" in output.answer.lower()
                    or "disabled" in output.fallback_reason.lower())

    def test_missing_api_key_returns_error(self):
        from agent.llm.schemas import LLMResponse

        mock_resp = LLMResponse(
            content="", error="API key not configured",
            provider="mock", model="mock",
        )

        with patch("agent.llm.provider.generate", return_value=mock_resp):
            from agent.llm.runtime import safe_generate
            output = safe_generate("result_summarize", safe_context={})

            assert output.llm_used is False
            assert output.answer != ""
            assert ("API key" in output.answer
                    or "API key" in output.fallback_reason
                    or "Provider unavailable" in output.answer)

    def test_enabled_but_provider_type_disabled(self):
        with patch("agent.llm.config.resolve_provider_config") as mock_cfg:
            mock_cfg.return_value = {
                "enabled": True,
                "provider_type": "disabled",
                "provider": "disabled",
                "model": "",
            }
            from agent.llm.runtime import safe_generate
            output = safe_generate("result_summarize", safe_context={})

            assert output.llm_used is False
            assert ("disabled" in output.answer.lower()
                    or "disabled" in output.fallback_reason.lower())
