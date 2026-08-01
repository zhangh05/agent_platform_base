# agent/llm/runtime.py
"""LLM Runtime — unified invocation entry point (invoke_llm) + safe_generate wrapper.

Design:
- invoke_llm() is the SINGLE entry point that calls provider.generate().
- safe_generate() is the public API that wraps invoke_llm() and returns SafeLLMOutput.
- composer / LLMClient / orchestrator ALL go through invoke_llm(), never call generate() directly.
- Policy checks are NON-BLOCKING: results recorded in metadata/warnings only.
"""

import re
from typing import List
from agent.state import AgentState
from agent.llm.schemas import LLMRequest, LLMMessage, SafeLLMOutput, PolicyDecision, LLMResponse
import logging



logger = logging.getLogger(__name__)

def invoke_llm(
    task: str,
    messages: List[LLMMessage] = None,
    tools: List[dict] = None,
    state_or_context=None,
    safe_context: dict = None,
    user_input: str = "",
    extra: dict = None,
    config_override: dict = None,
) -> LLMResponse:
    """Unified LLM invocation entry point — THE ONLY place that calls generate().

    All LLM calls (composer, orchestrator, LLMClient) MUST go through this function.
    provider.generate() is ONLY called from this function.

    Args:
        task: Prompt task name (used for prompt rendering if messages not provided)
        messages: Pre-built messages (if provided, skip prompt rendering)
        tools: OpenAI-format tool definitions (for function calling)
        state_or_context: AgentState or dict
        safe_context: Safe context dict for prompt rendering
        user_input: User input (used for prompt rendering)
        extra: Extra context for prompt rendering

    Returns:
        LLMResponse from provider
    """
    # ── Resolve config ──
    from agent.llm.config import resolve_provider_config
    cfg = resolve_provider_config()
    if config_override:
        cfg = {**cfg, **config_override}
    try:
        from agent.llm.router import resolve_model_route
        cfg = resolve_model_route(task, cfg)
    except Exception:
        logger.warning("model route resolution failed; using active provider", exc_info=True)

    if not cfg.get("enabled") or cfg.get("provider_type") == "disabled":
        return LLMResponse(
            error="LLM is disabled.",
            metadata={
                "error_type": "disabled_by_user",
                "http_status": None,
                "error_detail": "LLM disabled by effective configuration",
            },
        )

    # ── Build messages if not provided ──
    if messages is None:
        try:
            messages = _build_prompt_messages(
                task, state_or_context, safe_context, user_input, extra
            )
        except Exception as exc:
            logger.exception("prompt rendering failed for task=%s", task)
            return LLMResponse(
                error=f"prompt_runtime_error:{type(exc).__name__}",
                metadata={
                    "error_type": "prompt_runtime_error",
                    "error_detail": type(exc).__name__,
                    "retryable": False,
                },
            )

    # ── Build request ──
    # Always enable streaming — _api_generate_stream() handles tool calls correctly
    # by accumulating them across chunks while pushing content tokens in real-time.
    # v3.11 (stream scope): the caller controls whether tokens reach the user
    # channel via extra["stream_to_user"]. Default: False (internal only).
    use_stream = True
    caller_metadata = (extra or {}).get("request_metadata", {})
    if not isinstance(caller_metadata, dict):
        caller_metadata = {}
    req_metadata = {
        **{
            str(key)[:80]: value
            for key, value in caller_metadata.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        },
        "stream_to_user": bool((extra or {}).get("stream_to_user", False)),
        "stream_scope": str((extra or {}).get("stream_scope", "internal")),
    }
    req = LLMRequest(
        task=task,
        messages=messages,
        safe_context=safe_context or {},
        model=cfg.get("model", ""),
        temperature=cfg.get("temperature", 0.2),
        max_tokens=cfg.get("max_tokens", 4096),
        tools=tools,
        stream=use_stream,
        metadata=req_metadata,
    )

    # ── Call provider with retry (ONLY place that calls generate()) ──
    from agent.llm.provider import generate, ERROR_TYPE_PROVIDER_TIMEOUT, ERROR_TYPE_PROVIDER_UNKNOWN
    import time

    max_retries = 3
    for attempt in range(max_retries + 1):
        try:
            resp = generate(req, cfg)
        except (OSError, TimeoutError, ConnectionError) as e:
            # Transport-level errors: retry if attempts remain.
            error_msg = _redact(str(e))
            if attempt < max_retries:
                wait = min(2 ** attempt * 0.5, 8)
                time.sleep(wait)
                continue
            return LLMResponse(
                error=error_msg,
                metadata={
                    "error_type": ERROR_TYPE_PROVIDER_TIMEOUT,
                    "error_detail": error_msg[:200],
                    "http_status": None,
                    "retryable": True,
                    "retries_exhausted": True,
                },
            )

        # Check for provider errors that should trigger retry
        if resp.error:
            error_lower = resp.error.lower()
            retryable = any(k in error_lower for k in (
                "rate_limit", "rate limit", "overload", "429", "503",
                "timeout", "timed out",
            ))
            if attempt < max_retries and retryable:
                wait = 2.0 ** attempt
                time.sleep(wait)
                continue
            # Non-retryable or exhausted
            error_type = resp.metadata.get("error_type", ERROR_TYPE_PROVIDER_UNKNOWN) if resp.metadata else ERROR_TYPE_PROVIDER_UNKNOWN
            return LLMResponse(
                error=resp.error,
                metadata={
                    "error_type": error_type,
                    "error_detail": (resp.metadata or {}).get("error_detail", resp.error[:200]),
                    "http_status": (resp.metadata or {}).get("http_status"),
                    "retryable": retryable,
                    **({"retries_exhausted": True} if attempt >= max_retries else {}),
                },
            )

        break  # Success

    finish_reason = str(resp.finish_reason or "").lower()
    if finish_reason in {"length", "max_tokens", "content_length"}:
        resp.metadata = {
            **(resp.metadata or {}),
            "output_truncated": True,
            "truncation_reason": "length",
        }
    elif finish_reason == "stream_truncated":
        resp.metadata = {
            **(resp.metadata or {}),
            "output_truncated": True,
            "truncation_reason": "timeout",
        }
    return resp


def safe_generate(
    task: str,
    state_or_context=None,
    context_bundle=None,
    safe_context=None,
    user_input: str = "",
    extra: dict = None,
    messages: List[LLMMessage] = None,
    tools: List[dict] = None,
    config_override: dict = None,
) -> SafeLLMOutput:
    """Safe LLM generation — policy failures NEVER block the provider call.

    Wraps invoke_llm() and returns SafeLLMOutput with policy metadata.
    """
    # ── Resolve state ──
    state = None
    if isinstance(state_or_context, AgentState):
        state = state_or_context
        ctx = state.context or {}
        if context_bundle is None:
            context_bundle = ctx.get("context_bundle", {})
        if safe_context is None:
            safe_context = ctx.get("safe_llm_context", {})
        user_input = user_input or state.user_input

    safe_ctx = safe_context or {}

    from agent.llm.config import resolve_provider_config
    cfg = resolve_provider_config()
    if config_override:
        cfg = {**cfg, **config_override}

    # ── Prompt Runtime (primary path) ──
    prompt_runtime_used = True
    prompt_id = ""
    prompt_version = ""
    prompt_policy_pass = True
    injection_detected = False

    # Policy result containers (all non-blocking)
    prompt_input_ok = True
    prompt_input_issues = []
    prompt_text_ok = True
    prompt_text_issues = []
    request_policy_ok = True
    request_policy_violations = []
    output_policy_ok = True
    output_policy_issues = []
    response_policy_ok = True
    response_policy_violations = []

    try:
        from prompts.loader import get_prompt_by_task
        from prompts.renderer import render_prompt
        from prompts.policy import check_prompt_input, check_prompt_text, check_prompt_output, detect_prompt_injection

        # ── Injection detection (non-blocking, record only) ──
        try:
            inj_result = detect_prompt_injection(user_input)
            injection_detected = inj_result.injection_detected
            if inj_result.injection_detected:
                prompt_input_issues.append({"rule": "injection_detected", "warnings": inj_result.warnings})
        except Exception:
            logger.debug("safe_generate: <pass>", exc_info=True)

        spec = get_prompt_by_task(task)
        prompt_id = spec.prompt_id
        prompt_version = spec.version

        # ── Input policy (NON-BLOCKING) ──
        try:
            inp_result = check_prompt_input(spec, safe_ctx)
            if not inp_result.ok:
                prompt_input_ok = False
                prompt_input_issues.extend(inp_result.issues)
                prompt_policy_pass = False
        except Exception:
            logger.debug("safe_generate: <pass>", exc_info=True)

        # Render
        citations = safe_ctx.get("citations", []) if isinstance(safe_ctx, dict) else []
        rendered = render_prompt(task, safe_ctx, user_input, citations, extra)

        # ── Text policy (NON-BLOCKING) ──
        try:
            txt_result = check_prompt_text(rendered.text, spec)
            if not txt_result.ok:
                prompt_text_ok = False
                prompt_text_issues.extend(txt_result.issues)
                prompt_policy_pass = False
        except Exception:
            logger.debug("safe_generate: <pass>", exc_info=True)

        messages = messages or _messages_from_rendered_prompt(rendered.text, user_input, task)

    except Exception as exc:
        logger.exception("safe_generate prompt rendering failed for task=%s", task)
        return SafeLLMOutput(
            summary="",
            answer="",
            safe_to_show=False,
            llm_used=False,
            fallback_reason="prompt_runtime_error",
            warnings=[f"prompt_runtime_error:{type(exc).__name__}"],
            policy_decision=PolicyDecision(
                allowed=False,
                reason="prompt_runtime_error",
            ),
            metadata={
                "prompt_runtime_used": True,
                "prompt_id": prompt_id,
                "prompt_version": prompt_version,
                "provider_called": False,
                "error_type": "prompt_runtime_error",
                "error_detail": type(exc).__name__,
            },
        )

    # ── Request policy (NON-BLOCKING) ──
    try:
        from agent.llm.policy import check_request
        policy_req = check_request(LLMRequest(
            task=task,
            messages=messages,
            safe_context=safe_ctx,
            model=cfg.get("model", ""),
            temperature=cfg.get("temperature", 0.2),
            max_tokens=cfg.get("max_tokens", 4096),
            tools=tools,
        ), state)
        if not policy_req.allowed:
            request_policy_ok = False
            request_policy_violations = policy_req.violations
    except Exception:
        logger.debug("safe_generate: <pass>", exc_info=True)

    # ── ALWAYS call provider via unified entry point ──
    resp = invoke_llm(
        task=task,
        messages=messages,
        tools=tools,
        state_or_context=state,
        safe_context=safe_ctx,
        user_input=user_input,
        extra=extra,
        config_override=cfg,
    )

    if resp.error:
        provider_meta = resp.metadata or {}
        redacted = _redact(resp.error)
        base_meta = _build_metadata(
            prompt_runtime_used, prompt_id, prompt_version, prompt_policy_pass,
            injection_detected, prompt_input_ok, prompt_input_issues,
            prompt_text_ok, prompt_text_issues,
            request_policy_ok, request_policy_violations,
            output_policy_ok, output_policy_issues,
            response_policy_ok, response_policy_violations,
            provider_called=True, output_accepted=False,
        )
        # Merge provider diagnostics into metadata
        base_meta["provider_error_type"] = provider_meta.get("error_type")
        base_meta["http_status"] = provider_meta.get("http_status")
        base_meta["provider_error_message"] = provider_meta.get("error_detail")
        base_meta["raw_error_redacted"] = redacted[:200]
        return SafeLLMOutput(
            answer=f"Provider unavailable: {redacted}",
            llm_used=False,
            fallback_reason=f"provider_unavailable: {redacted}",
            warnings=[f"provider_error: {redacted}"],
            metadata=base_meta,
        )

    cleaned_content, reasoning_stripped = _sanitize_provider_output(resp.content)
    resp.content = cleaned_content

    # ── Output policy (NON-BLOCKING) ──
    try:
        from prompts.policy import check_prompt_output
        out_result = check_prompt_output(None, resp.content,
                                           safe_ctx.get("citations", []) if isinstance(safe_ctx, dict) else [])
        if not out_result.ok:
            output_policy_ok = False
            output_policy_issues = out_result.issues
            prompt_policy_pass = False
    except Exception:
        logger.debug("safe_generate: <pass>", exc_info=True)

    # ── Response policy (NON-BLOCKING) ──
    try:
        from agent.llm.policy import check_response
        policy_resp = check_response(resp, state)
        if not policy_resp.allowed:
            response_policy_ok = False
            response_policy_violations = policy_resp.violations
    except Exception:
        logger.debug("safe_generate: <pass>", exc_info=True)

    # ── Build warnings from all policy failures ──
    warnings = []
    if not prompt_input_ok:
        warnings.append(f"prompt_input_policy_failed: {prompt_input_issues}")
    if not prompt_text_ok:
        warnings.append(f"prompt_text_policy_failed: {prompt_text_issues}")
    if not request_policy_ok:
        warnings.extend(request_policy_violations)
    if not output_policy_ok:
        warnings.append(f"output_policy_failed: {output_policy_issues}")
    if not response_policy_ok:
        warnings.extend(response_policy_violations)

    # safe_to_show = hint to UI (true if all policies pass), but answer is ALWAYS returned
    safe_to_show = prompt_policy_pass and request_policy_ok and output_policy_ok and response_policy_ok

    return SafeLLMOutput(
        summary=resp.content, answer=resp.content, safe_to_show=safe_to_show,
        llm_used=True,
        policy_decision=PolicyDecision(allowed=True, reason="non_blocking_mode"),
        warnings=warnings,
        metadata=_build_metadata(
            prompt_runtime_used, prompt_id, prompt_version, prompt_policy_pass,
            injection_detected, prompt_input_ok, prompt_input_issues,
            prompt_text_ok, prompt_text_issues,
            request_policy_ok, request_policy_violations,
            output_policy_ok, output_policy_issues,
            response_policy_ok, response_policy_violations,
            provider_called=True, output_accepted=True,
            reasoning_stripped=reasoning_stripped,
        ),
    )


def _build_prompt_messages(
    task: str,
    state_or_context=None,
    safe_context: dict = None,
    user_input: str = "",
    extra: dict = None,
) -> List[LLMMessage]:
    """Build messages by rendering prompt (used when messages not provided to invoke_llm())."""
    from prompts.loader import get_prompt_by_task
    from prompts.renderer import render_prompt

    get_prompt_by_task(task)
    citations = (
        (safe_context or {}).get("citations", [])
        if isinstance(safe_context, dict) else []
    )
    rendered = render_prompt(task, safe_context or {}, user_input, citations, extra)
    return _messages_from_rendered_prompt(rendered.text, user_input, task)


def _build_metadata(
    prompt_runtime_used, prompt_id, prompt_version, prompt_policy_pass,
    injection_detected,
    prompt_input_ok, prompt_input_issues,
    prompt_text_ok, prompt_text_issues,
    request_policy_ok, request_policy_violations,
    output_policy_ok, output_policy_issues,
    response_policy_ok, response_policy_violations,
    provider_called, output_accepted,
    reasoning_stripped=False,
) -> dict:
    """Build metadata dict with all policy results."""
    return {
        "prompt_runtime_used": prompt_runtime_used,
        "prompt_id": prompt_id,
        "prompt_version": prompt_version,
        "prompt_policy_pass": prompt_policy_pass,
        "prompt_injection_detected": injection_detected,
        "prompt_input_ok": prompt_input_ok,
        "prompt_input_issues": prompt_input_issues,
        "prompt_text_ok": prompt_text_ok,
        "prompt_text_issues": prompt_text_issues,
        "request_policy_ok": request_policy_ok,
        "request_policy_violations": request_policy_violations,
        "output_policy_ok": output_policy_ok,
        "output_policy_issues": output_policy_issues,
        "response_policy_ok": response_policy_ok,
        "response_policy_violations": response_policy_violations,
        "rendered_prompt_used": True,
        "provider_called": provider_called,
        "output_accepted": output_accepted,
        "reasoning_stripped": reasoning_stripped,
    }


def _messages_from_rendered_prompt(rendered_text: str, user_input: str, task: str) -> List[LLMMessage]:
    """Use the rendered template as the authoritative system instruction."""
    user_content = (user_input or "").strip() or f"Task: {task}"
    return [
        LLMMessage(role="system", content=rendered_text),
        LLMMessage(role="user", content=user_content),
    ]


def _redact(msg: str) -> str:
    for kw in ["Authorization", "Bearer", "api_key", "key", "password", "secret"]:
        if kw.lower() in msg.lower():
            return "[REDACTED]"
    return msg[:200]


def sanitize_provider_output(content: str) -> tuple[str, bool]:
    """Remove provider reasoning markup. Public API."""
    text = content or ""
    original = text
    text = re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<reasoning\b[^>]*>.*?</reasoning>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"(?ism)^\s*(reasoning|思考过程)\s*[:：].*?(?=\n\s*(answer|回答|结论)\s*[:：]|\Z)", "", text)
    text = re.sub(r"(?i)</?(think|reasoning)\b[^>]*>", "", text)
    return text.strip(), text != original


_sanitize_provider_output = sanitize_provider_output


def get_llm_status() -> dict:
    from agent.llm.config import get_llm_status as _gs
    return _gs()
