# agent/llm/provider.py
"""LLM provider — uses unified effective config (UI settings priority).

Error diagnostics: preserves HTTP status, error type, and non-sensitive details.
Only masks real tokens/Authorization/Bearer values.
"""

import json, logging, os, time, urllib.request, urllib.error
from typing import Optional
from agent.llm.schemas import LLMMessage, LLMRequest, LLMResponse, LLMToolCall
from agent.llm.key_resolver import mask_secret

_LOG = logging.getLogger(__name__)

# Error type constants (used in metadata and API responses)
ERROR_TYPE_MISSING_API_KEY = "missing_api_key"
ERROR_TYPE_DISABLED_BY_USER = "disabled_by_user"
ERROR_TYPE_PROVIDER_HTTP_400 = "provider_http_400"
ERROR_TYPE_PROVIDER_HTTP_401 = "provider_http_401"
ERROR_TYPE_PROVIDER_HTTP_403 = "provider_http_403"
ERROR_TYPE_PROVIDER_HTTP_404 = "provider_http_404"
ERROR_TYPE_PROVIDER_HTTP_429 = "provider_http_429"
ERROR_TYPE_PROVIDER_TIMEOUT = "provider_timeout"
ERROR_TYPE_PROVIDER_NETWORK_ERROR = "provider_network_error"
ERROR_TYPE_PROVIDER_SCHEMA_REJECTED = "provider_schema_rejected"
ERROR_TYPE_PROVIDER_UNKNOWN = "provider_unknown_error"


def get_provider_config() -> dict:
    """Get provider config via unified path (UI settings > env/file > default)."""
    from agent.llm.config import resolve_provider_config
    return resolve_provider_config()


def generate(req: LLMRequest, cfg: dict = None) -> LLMResponse:
    """Generate LLM response using unified effective config."""
    cfg = cfg or get_provider_config()
    if not isinstance(req.metadata.get("prompt_assembly"), dict):
        from agent.llm.prompt_assembly import build_prompt_profile
        req.metadata["prompt_assembly"] = build_prompt_profile(req, cfg)
    if not cfg.get("enabled") or cfg.get("provider_type") == "disabled":
        return LLMResponse(error="LLM disabled", metadata={"error_type": ERROR_TYPE_DISABLED_BY_USER})
    if cfg.get("provider_type") == "mock":
        response = _mock_generate(req, cfg)
    elif cfg.get("provider_type") == "anthropic_messages":
        response = _anthropic_messages_generate(req, cfg)
    else:
        response = _api_generate(req, cfg)
    return _finalize_provider_response(response, req, cfg)


def _finalize_provider_response(
    response: LLMResponse,
    req: LLMRequest,
    cfg: dict,
) -> LLMResponse:
    """Attach provider-neutral prompt/cache facts to every transport result."""
    from agent.llm.prompt_assembly import build_prompt_profile, normalize_usage

    profile = req.metadata.get("prompt_assembly")
    if not isinstance(profile, dict):
        profile = build_prompt_profile(req, cfg)
    response.usage = normalize_usage(response.usage)
    strategy = str(profile.get("strategy") or "unsupported")
    response.metadata = {
        **(response.metadata or {}),
        "prompt_assembly": profile,
        "prompt_cache_strategy": strategy,
        "prompt_cache_requested": bool(
            (response.metadata or {}).get("prompt_cache_requested")
            or strategy in {"anthropic_explicit", "openai_automatic"}
        ),
        "prompt_cache_fallback": bool(
            (response.metadata or {}).get("prompt_cache_fallback")
        ),
    }
    return response


def health(cfg: dict = None) -> dict:
    """Check provider health with multi-dimensional checks (concurrent).
    
    Three checks run in parallel via threading: base_url HEAD, /models GET,
    and chat/completions POST. Total worst-case time is reduced from ~40s
    (serial) to ~15s (max individual timeout).
    """
    import threading as _threading

    if cfg is None:
        cfg = get_provider_config()
    provider_type = cfg.get("provider_type", "disabled")
    has_key = bool(cfg.get("api_key"))
    result = {
        "configured": has_key or provider_type == "mock",
        "provider": cfg.get("provider", cfg.get("default_provider", "disabled")),
        "connected": False,
        "key_loaded": has_key,
        "base_url_reachable": False,
        "models_endpoint_ok": False,
        "chat_completion_ok": False,
        "chat_completion_endpoint_reachable": False,
        "model": cfg.get("model", ""),
        "last_error": None,
        "last_error_type": None,
        "http_status": None,
    }
    if not result["configured"] or provider_type == "disabled":
        result["last_error"] = "no_api_key"
        result["last_error_type"] = ERROR_TYPE_MISSING_API_KEY
        return result
    if provider_type == "mock":
        result["connected"] = True
        result["base_url_reachable"] = True
        result["models_endpoint_ok"] = True
        result["chat_completion_ok"] = True
        result["chat_completion_endpoint_reachable"] = True
        return result
    if not has_key:
        result["last_error"] = "no_api_key"
        result["last_error_type"] = ERROR_TYPE_MISSING_API_KEY
        return result

    _health_errors = []
    _health_lock = _threading.Lock()
    base = cfg.get("base_url", "").rstrip("/")
    api_key = cfg.get("api_key", "")

    def _check_base_url():
        try:
            ping_req = urllib.request.Request(
                base, headers={"Authorization": "Bearer " + api_key}
            )
            ping_req.get_method = lambda: "HEAD"
            with urllib.request.urlopen(ping_req, timeout=10) as resp:
                result["base_url_reachable"] = 200 <= resp.status < 400
        except urllib.error.HTTPError as e:
            result["base_url_reachable"] = True
            with _health_lock:
                if not result["last_error"]:
                    result["last_error"] = _redact_error_detail(str(e))
                    result["last_error_type"] = f"provider_http_{e.code}"
                    result["http_status"] = e.code
        except Exception:
            pass

    def _check_models():
        try:
            url = base + "/models"
            r = urllib.request.Request(
                url, headers={"Authorization": "Bearer " + api_key}
            )
            with urllib.request.urlopen(r, timeout=15) as resp:
                result["models_endpoint_ok"] = resp.status == 200
        except urllib.error.HTTPError as e:
            result["models_endpoint_ok"] = e.code == 200
            with _health_lock:
                if not result["last_error"]:
                    result["last_error"] = _redact_error_detail(str(e))
                    result["last_error_type"] = f"provider_http_{e.code}"
                    result["http_status"] = e.code
        except Exception as e:
            with _health_lock:
                if not result["last_error"]:
                    result["last_error"] = _redact_error_detail(str(e))
                    result["last_error_type"] = ERROR_TYPE_PROVIDER_NETWORK_ERROR

    def _check_chat():
        probe_cfg = {**cfg, "temperature": 0.0, "max_tokens": 16}
        probe = generate(LLMRequest(
            task="connection_probe",
            messages=[LLMMessage(role="user", content="Reply with OK.")],
            model=probe_cfg.get("model", ""),
            temperature=0.0,
            max_tokens=16,
            stream=True,
            metadata={"stream_to_user": False, "stream_scope": "health"},
        ), probe_cfg)
        chat_ok = not bool(probe.error)
        result["chat_completion_ok"] = chat_ok
        result["chat_completion_endpoint_reachable"] = chat_ok or bool(
            (probe.metadata or {}).get("http_status")
        )
        result["connected"] = chat_ok
        with _health_lock:
            if chat_ok:
                result["http_status"] = 200
                result["last_error"] = None
                result["last_error_type"] = None
            else:
                metadata = probe.metadata or {}
                result["last_error"] = _redact_error_detail(probe.error or "provider probe failed")
                result["last_error_type"] = metadata.get("error_type", ERROR_TYPE_PROVIDER_NETWORK_ERROR)
                result["http_status"] = metadata.get("http_status")

    # Run all three checks in parallel
    threads = [
        _threading.Thread(target=_check_base_url, daemon=True),
        _threading.Thread(target=_check_models, daemon=True),
        _threading.Thread(target=_check_chat, daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    # Chat completions is the only authoritative usability signal. Providers
    # may intentionally omit HEAD or /models; those advisory checks must not
    # leave a stale error after the same transport used by real turns worked.
    if result["chat_completion_ok"]:
        result["connected"] = True
        result["last_error"] = None
        result["last_error_type"] = None
        result["http_status"] = 200

    return result


def _mock_generate(req: LLMRequest, cfg: dict) -> LLMResponse:
    ctx = req.safe_context or {}
    if ctx.get("_mock_response_type") == "unsafe":
        return LLMResponse(
            content="I completed the requested update.",
            provider="mock", model="mock-unsafe",
        )
    return LLMResponse(
        content=f"Task completed. {ctx.get('output_line_count', 0)} output lines. "
                f"{ctx.get('manual_review_count', 0)} items need review.",
        provider="mock", model=cfg.get("model", "mock-safe"),
    )


def _to_openai_compatible_messages(messages: list[LLMMessage]) -> list[dict]:
    """Keep the reusable system kernel first and dynamic system facts after it."""
    from agent.llm.prompt_assembly import split_stable_system

    formatted: list[dict] = []
    for message in messages:
        if message.role != "system" or not isinstance(message.content, str):
            formatted.append(_format_message(message))
            continue
        stable, dynamic = split_stable_system(message.content)
        if stable:
            formatted.append({"role": "system", "content": stable})
        if dynamic:
            formatted.append({"role": "system", "content": dynamic})
    return formatted


def _is_official_openai(cfg: dict) -> bool:
    provider = str(cfg.get("provider") or cfg.get("default_provider") or "")
    base_url = str(cfg.get("base_url") or "").lower()
    return provider == "openai" and "api.openai.com" in base_url


def _apply_openai_cache_fields(body: dict, req: LLMRequest, cfg: dict) -> bool:
    """Apply only fields documented by the official OpenAI endpoint.

    Generic compatible gateways retain the stable prefix ordering but receive
    no OpenAI-private parameters.  This prevents a cache optimization from
    breaking DeepSeek, Ark, Ollama or a custom gateway.
    """
    if not _is_official_openai(cfg):
        return False
    profile = req.metadata.get("prompt_assembly")
    if not isinstance(profile, dict) or profile.get("strategy") != "openai_automatic":
        return False
    cache_key = str(profile.get("cache_key") or "")[:128]
    if not cache_key:
        return False
    body["prompt_cache_key"] = cache_key
    return True


def _without_optional_openai_fields(body: dict) -> dict:
    clean = json.loads(json.dumps(body))
    clean.pop("prompt_cache_key", None)
    clean.pop("prompt_cache_retention", None)
    clean.pop("prompt_cache_options", None)
    clean.pop("stream_options", None)
    return clean


def _optional_openai_fields_rejected(response: LLMResponse) -> bool:
    return int((response.metadata or {}).get("http_status") or 0) in {400, 422}


def _api_generate(req: LLMRequest, cfg: dict) -> LLMResponse:
    if not cfg.get("api_key"):
        return LLMResponse(
            error="API key not configured",
            metadata={"error_type": ERROR_TYPE_MISSING_API_KEY},
        )
    try:
        url = cfg.get("base_url", "https://api.minimaxi.com/v1").rstrip("/") + "/chat/completions"
        body_dict = {
            "model": cfg.get("model", req.model),
            "messages": _to_openai_compatible_messages(req.messages),
            "temperature": cfg.get("temperature", req.temperature),
            "max_tokens": cfg.get("max_tokens", req.max_tokens),
        }
        if req.tools:
            body_dict["tools"] = req.tools
            body_dict["tool_choice"] = "auto"

        cache_fields_added = _apply_openai_cache_fields(body_dict, req, cfg)

        # Streaming mode: use requests with stream=True
        if req.stream:
            body_dict["stream"] = True
            if _is_official_openai(cfg):
                body_dict["stream_options"] = {"include_usage": True}
            result = _api_generate_stream(url, body_dict, cfg, req)
            if cache_fields_added and _optional_openai_fields_rejected(result):
                result = _api_generate_stream(
                    url, _without_optional_openai_fields(body_dict), cfg, req
                )
                result.metadata = {
                    **(result.metadata or {}),
                    "prompt_cache_requested": True,
                    "prompt_cache_fallback": True,
                }
            return result

        # v3.2.1: log multimodal messages for vision debugging
        last = body_dict["messages"][-1] if body_dict["messages"] else {}
        cont = last.get("content", "")
        if isinstance(cont, list):
            types = [p.get("type","?") for p in cont]
            imgs = sum(1 for p in cont if p.get("type")=="image_url")
            _debug_log("[api] multimodal: %s -> %s image(s)", types, imgs)
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + cfg.get("api_key", ""),
        }
        cache_fallback = False

        def _send(active_body: dict) -> dict:
            body = json.dumps(active_body).encode()
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=cfg.get("timeout", 90)) as resp:
                return json.loads(resp.read().decode())

        try:
            d = _send(body_dict)
        except urllib.error.HTTPError as error:
            if not (cache_fields_added and error.code in {400, 422}):
                raise
            d = _send(_without_optional_openai_fields(body_dict))
            cache_fallback = True
        choice = d.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "") or ""
        tool_calls = _parse_message_tool_calls(message)
        return LLMResponse(
            content=content,
            provider=cfg.get("provider", cfg.get("default_provider", "")),
            model=d.get("model", ""),
            usage=d.get("usage"),
            finish_reason=choice.get("finish_reason", ""),
            raw=d,
            tool_calls=tool_calls,
            metadata={
                "prompt_cache_requested": cache_fields_added,
                "prompt_cache_fallback": cache_fallback,
            },
        )
    except urllib.error.HTTPError as e:
        # Extract HTTP status and error detail
        http_status = e.code
        error_detail = _read_error_body(e)
        error_type = f"provider_http_{http_status}"
        redacted = _redact_error_detail(error_detail)
        return LLMResponse(
            error=f"{error_type}: {redacted}",
            metadata={
                "error_type": error_type,
                "http_status": http_status,
                "error_detail": redacted[:200],
            },
        )
    except urllib.error.URLError as e:
        # Network error — classify timeout vs other network errors
        reason = str(e.reason) if hasattr(e, 'reason') else str(e)
        is_timeout = 'timeout' in reason.lower() or 'timed out' in reason.lower()
        error_type = ERROR_TYPE_PROVIDER_TIMEOUT if is_timeout else ERROR_TYPE_PROVIDER_NETWORK_ERROR
        redacted = _redact_error_detail(str(e))
        meta = {
            "error_type": error_type,
            "http_status": None,
            "error_detail": redacted[:200],
        }
        if is_timeout:
            meta["retryable"] = True
            meta["timeout_seconds"] = cfg.get("timeout", 90)
        return LLMResponse(
            error=f"{error_type}: {redacted}",
            metadata=meta,
        )
    except TimeoutError:
        error_type = ERROR_TYPE_PROVIDER_TIMEOUT
        timeout_s = cfg.get('timeout', 90)
        return LLMResponse(
            error=f"{error_type}: Request timed out after {timeout_s} seconds",
            metadata={
                "error_type": error_type,
                "http_status": None,
                "error_detail": f"timeout after {timeout_s}s",
                "retryable": True,
                "timeout_seconds": timeout_s,
            },
        )
    except (BrokenPipeError, ConnectionResetError) as e:
        # Server hung up during request — typically invalid auth or endpoint
        error_type = ERROR_TYPE_PROVIDER_NETWORK_ERROR
        redacted = _redact_error_detail(str(e))
        return LLMResponse(
            error=f"{error_type}: Connection terminated by server ({redacted})",
            metadata={
                "error_type": error_type,
                "http_status": None,
                "error_detail": redacted[:200],
                "retryable": True,
            },
        )
    except json.JSONDecodeError as e:
        error_type = ERROR_TYPE_PROVIDER_SCHEMA_REJECTED
        return LLMResponse(
            error=f"{error_type}: Invalid JSON response from provider",
            metadata={
                "error_type": error_type,
                "http_status": None,
                "error_detail": str(e)[:200],
            },
        )
    except Exception as e:
        error_type = ERROR_TYPE_PROVIDER_UNKNOWN
        redacted = _redact_error_detail(str(e))
        return LLMResponse(
            error=f"{error_type}: {redacted}",
            metadata={
                "error_type": error_type,
                "http_status": None,
                "error_detail": redacted[:200],
            },
        )


def _is_stream_cancelled(req: "LLMRequest") -> bool:
    """Read only the server-owned request cancellation callback.

    This lives beside provider I/O so a user stop prevents both further token
    projection and unnecessary stream consumption. Callback faults are fail-open
    to preserve provider availability; cancellation itself remains authoritative
    in QueryLoop after the call returns.
    """
    check = getattr(req, "cancel_check", None)
    if not callable(check):
        return False
    try:
        return bool(check())
    except (TypeError, ValueError, RuntimeError):
        return False


def _cancelled_stream_response(
    content_parts: list[str],
    cfg: dict,
    *,
    model: str = "",
    usage=None,
    finish_reason: str = "",
) -> "LLMResponse":
    return LLMResponse(
        content="".join(content_parts),
        provider=cfg.get("provider", cfg.get("default_provider", "")),
        model=model or cfg.get("model", ""),
        usage=usage,
        finish_reason=finish_reason or "cancelled",
        metadata={"stream_cancelled": True},
    )


def _api_generate_stream(url: str, body_dict: dict, cfg: dict, req: "LLMRequest") -> "LLMResponse":
    """Streaming LLM API call — yields tokens via StreamEmitter callback.

    Uses requests with stream=True to parse SSE (Server-Sent Events) chunks.
    Accumulates the full response while pushing tokens in real-time.
    """
    import requests as _requests
    from agent.runtime.stream_emitter import StreamEmitter

    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + cfg.get("api_key", ""),
        "Accept": "text/event-stream",
    }

    content_parts = []
    finish_reason = ""
    provider_model = ""
    usage = None
    tool_calls_accum: list[dict] = [{}]

    try:
        resp = _requests.post(
            url,
            json=body_dict,
            headers=headers,
            timeout=cfg.get("timeout", 120),
            stream=True,
        )

        if resp.status_code != 200:
            error_body = resp.text[:500]
            return LLMResponse(
                error=f"provider_http_{resp.status_code}: {error_body}",
                metadata={
                    "error_type": f"provider_http_{resp.status_code}",
                    "http_status": resp.status_code,
                    "error_detail": error_body[:200],
                },
            )

        # Force UTF-8 encoding to prevent Latin-1 decoding of Chinese characters
        # when the LLM API doesn't include charset=utf-8 in Content-Type
        resp.encoding = "utf-8"

        # Parse SSE stream
        raw_chunks = []
        raw_chunk_count = 0
        for line in resp.iter_lines(decode_unicode=True):
            if _is_stream_cancelled(req):
                resp.close()
                return _cancelled_stream_response(
                    content_parts, cfg, model=provider_model,
                    usage=usage, finish_reason=finish_reason,
                )
            if not line:
                continue
            # SSE permits the optional single space after the ``data:`` field
            # delimiter. Some OpenAI-compatible providers omit it; accepting
            # both forms prevents a valid stream from being silently discarded.
            if not line.startswith("data:"):
                continue

            data_str = line[5:].lstrip()  # Remove ``data:`` and optional space
            if data_str == "[DONE]":
                break

            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            # Keep only last 5 chunks for debug logging to prevent OOM on
            # very large responses (e.g. 100K+ token streaming).
            raw_chunks.append(chunk)
            if len(raw_chunks) > 5:
                raw_chunks.pop(0)
            raw_chunk_count += 1

            # OpenAI sends a usage-only terminal chunk when
            # stream_options.include_usage is enabled. Capture it before the
            # choices guard so cached-token counters are not discarded.
            if chunk.get("usage"):
                usage = chunk["usage"]
            if chunk.get("model"):
                provider_model = chunk["model"]

            choices = chunk.get("choices", [])
            if not choices:
                continue

            choice = choices[0]
            delta = choice.get("delta", {})
            finish_reason = choice.get("finish_reason", finish_reason)

            # Token content
            token = delta.get("content", "")
            if token:
                if _is_stream_cancelled(req):
                    resp.close()
                    return _cancelled_stream_response(
                        content_parts, cfg, model=provider_model,
                        usage=usage, finish_reason=finish_reason,
                    )
                content_parts.append(token)
                # v3.11 (stream scope): only push to the real-time
                # WebSocket token channel when the caller explicitly
                # opts in via stream_to_user.  Planner tokens, for
                # example, are accumulated but never surfaced to
                # the user.
                if req.metadata.get("stream_to_user"):
                    _push_stream_token(token)

            # Tool calls (accumulated across chunks)
            tc_list = delta.get("tool_calls")
            if tc_list:
                for tc in tc_list:
                    idx = tc.get("index", 0)
                    while len(tool_calls_accum) <= idx:
                        tool_calls_accum.append({})
                    tc_acc = tool_calls_accum[idx]
                    # name may be at top level or inside function.name
                    fn_name = tc.get("function", {}).get("name") or tc.get("name")
                    if fn_name:
                        tc_acc["name"] = fn_name
                        tc_acc["function"] = tc.get("function", {})
                        tc_acc["id"] = tc.get("id", tc_acc.get("id", ""))
                        tc_acc.setdefault("arguments", "")
                    if tc.get("function", {}).get("arguments"):
                        tc_acc["arguments"] = tc_acc.get("arguments", "") + tc["function"]["arguments"]

    except _requests.exceptions.Timeout:
        text = "".join(content_parts)
        return LLMResponse(
            content=text,
            error=None if text else "timeout",
            provider=cfg.get("provider", ""),
            model=provider_model,
            finish_reason=finish_reason or "stream_truncated",
            metadata={"stream_truncated": True, "error_detail": "stream timeout"},
        ) if text else LLMResponse(
            error="provider_timeout: stream timed out",
            metadata={"error_type": ERROR_TYPE_PROVIDER_TIMEOUT, "retryable": True},
        )
    except Exception as e:
        text = "".join(content_parts)
        error_type = ERROR_TYPE_PROVIDER_UNKNOWN
        return LLMResponse(
            content=text,
            error=None if text else f"{error_type}: {str(e)[:200]}",
            provider=cfg.get("provider", ""),
            model=provider_model,
            finish_reason=finish_reason,
            metadata={"stream_error": str(e)[:200]},
        ) if text else LLMResponse(
            error=f"{error_type}: {str(e)[:200]}",
            metadata={"error_type": error_type},
        )

    # Build final response
    content = "".join(content_parts)
    tool_calls = []
    for tc_acc in tool_calls_accum:
        if tc_acc.get("name"):
            try:
                args = json.loads(tc_acc.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(_ToolCallRaw(id=tc_acc.get("id", ""), name=tc_acc["name"], arguments=args))

    _debug_log("[stream] done: content_len=%s, tool_calls=%s, finish=%s", len(content), len(tool_calls), finish_reason)
    # Debug: dump last 3 raw chunks with actual values
    if raw_chunks:
        _debug_log("[stream] last_chunks (%s total):", len(raw_chunks))
        for i, rc in enumerate(raw_chunks[-3:]):
            choices = rc.get("choices", [{}])
            delta = choices[0].get("delta", {}) if choices else {}
            tc = delta.get("tool_calls")
            ct = delta.get("content")
            _debug_log(
                "[stream]   chunk[%s]: content=%s, tool_calls=%s, fin=%s",
                i,
                repr(ct)[:80],
                repr(tc)[:200],
                choices[0].get("finish_reason", "") if choices else "",
            )

    return LLMResponse(
        content=content,
        provider=cfg.get("provider", cfg.get("default_provider", "")),
        model=provider_model or cfg.get("model", req.model),
        usage=usage,
        finish_reason=finish_reason,
        tool_calls=tool_calls if not isinstance(tool_calls, list) else _fix_tool_calls_format(tool_calls),
    )


def _push_stream_token(token: str):
    """Push a streaming token via StreamEmitter realtime callback."""
    try:
        from agent.runtime.stream_emitter import StreamEmitter
        cb = StreamEmitter._get_realtime()
        if cb:
            cb({"type": "token", "content": token, "timestamp": time.time()})
    except Exception as e:
        _debug_log("[stream] push token error: %s", e)


def _debug_log(message: str, *args) -> None:
    """Debug logging must never affect provider success/failure."""
    try:
        _LOG.debug(message, *args)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass


# Simple internal class for stream-parsed tool calls
class _ToolCallRaw:
    def __init__(self, id="", name="", arguments=None):
        self.id = id
        self.name = name
        self.arguments = arguments or {}


def _fix_tool_calls_format(tool_calls):
    """Ensure tool calls are in LLMToolCall format."""
    result = []
    for tc in tool_calls:
        if hasattr(tc, 'name'):
            from agent.llm.schemas import LLMToolCall
            result.append(LLMToolCall(
                id=getattr(tc, 'id', ''),
                name=tc.name,
                arguments=getattr(tc, 'arguments', {}),
            ))
    return result


def _read_error_body(http_error: urllib.error.HTTPError) -> str:
    """Read error response body (for HTTPError with response body)."""
    try:
        body = http_error.read().decode("utf-8", errors="replace")
        d = json.loads(body)
        # OpenAI-compatible error format: {"error": {"message": "...", "type": "...", "code": ...}}
        err = d.get("error", {})
        if isinstance(err, dict):
            msg = err.get("message", "")
            if msg:
                return msg
        # Fallback: return raw body (truncated)
        return body[:500]
    except Exception:
        return str(http_error)


def _redact_error_detail(msg: str) -> str:
    """Redact sensitive data (tokens, Authorization) from error messages.

    Preserves non-sensitive error details (HTTP status, error type, etc.)
    Only masks: Authorization header values, Bearer tokens, API keys.
    """
    if not msg:
        return msg
    import re
    # Mask "Bearer <token>" → Bearer [REDACTED]
    msg = re.sub(r'Bearer\s+\S+', 'Bearer [REDACTED]', msg)
    # Mask "Authorization: <value>" → Authorization: [REDACTED]
    msg = re.sub(r'Authorization:\s*\S+', 'Authorization: [REDACTED]', msg)
    # Mask api_key/apikey/token assignments: api_key=VALUE → api_key=[REDACTED]
    msg = re.sub(
        r'(["\']?(?:api_key|apikey|token)["\']?\s*[:=]\s*["\']?)\S+(["\']?)',
        r'\1[REDACTED]\2',
        msg,
        flags=re.IGNORECASE,
    )
    # Mask "API key <value>" / "api key <value>" pattern (no = or :)
    msg = re.sub(
        r'(?i)(api\s+key\s+)\S+',
        r'\1[REDACTED]',
        msg,
    )
    return msg


def _format_message(m) -> dict:
    msg = {"role": m.role, "content": m.content}
    if m.tool_call_id:
        msg["tool_call_id"] = m.tool_call_id
    if m.tool_calls:
        msg["tool_calls"] = m.tool_calls
    return msg


def _anthropic_messages_generate(req: LLMRequest, cfg: dict) -> LLMResponse:
    """Call an Anthropic Messages-compatible provider.

    Provider identity and wire protocol are intentionally separate. Native
    Anthropic and MiniMax-M3 share this serializer/parser while retaining
    provider-specific base URLs, model names, and credentials.
    """
    try:
        import requests as _requests

        url = _anthropic_messages_url(cfg)
        body = _to_anthropic_messages_request(req, cfg)
        headers = {
            "Content-Type": "application/json",
            "x-api-key": cfg.get("api_key", ""),
            "anthropic-version": "2023-06-01",
        }
        def send(active_body: dict) -> LLMResponse:
            if req.stream:
                return _anthropic_messages_stream(url, active_body, headers, cfg, req)
            response = _requests.post(
                url, json=active_body, headers=headers, timeout=cfg.get("timeout", 120)
            )
            if response.status_code != 200:
                detail = response.text[:500]
                return LLMResponse(
                    error=f"provider_http_{response.status_code}: {detail[:300]}",
                    metadata={
                        "error_type": f"provider_http_{response.status_code}",
                        "http_status": response.status_code,
                        "error_detail": detail[:200],
                    },
                )
            return _parse_anthropic_messages_response(response.json(), cfg)

        result = send(body)
        cache_requested = _anthropic_prompt_cache_enabled(cfg) and _has_anthropic_prompt_cache(body)
        if cache_requested and _anthropic_prompt_cache_rejected(result):
            result = send(_without_anthropic_prompt_cache(body))
            result.metadata = {
                **(result.metadata or {}),
                "prompt_cache_requested": True,
                "prompt_cache_fallback": True,
            }
        else:
            result.metadata = {
                **(result.metadata or {}),
                "prompt_cache_requested": cache_requested,
                "prompt_cache_fallback": False,
            }
        return result
    except Exception as exc:
        return LLMResponse(error=f"provider_anthropic_error: {str(exc)[:300]}")


def _anthropic_messages_url(cfg: dict) -> str:
    """Resolve one exact Messages endpoint from a provider base URL."""
    from urllib.parse import urlsplit, urlunsplit

    provider = str(cfg.get("provider") or cfg.get("default_provider") or "").lower()
    default = "https://api.minimaxi.com/anthropic/v1" if provider == "minimax" else "https://api.anthropic.com/v1"
    parsed = urlsplit(str(cfg.get("base_url") or default).rstrip("/"))
    path = parsed.path.rstrip("/")
    if path.endswith("/messages"):
        messages_path = path
    elif provider == "minimax" and not path.endswith("/anthropic/v1"):
        messages_path = "/anthropic/v1/messages"
    else:
        messages_path = f"{path}/messages"
    return urlunsplit((parsed.scheme, parsed.netloc, messages_path, "", ""))


def _to_anthropic_messages_request(req: LLMRequest, cfg: dict) -> dict:
    from agent.llm.prompt_assembly import stable_and_dynamic_system

    stable_system, dynamic_system = stable_and_dynamic_system(req.messages)
    messages: list[dict] = []

    def append_message(role: str, content: list[dict]) -> None:
        if not content:
            return
        # Anthropic requires alternating user/assistant turns. QueryLoop may
        # append a user nudge immediately after one or more tool results, so
        # merge adjacent blocks with the same projected role.
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(content)
        else:
            messages.append({"role": role, "content": content})

    for message in req.messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            append_message("user", [{
                "type": "tool_result",
                "tool_use_id": str(message.tool_call_id or ""),
                "content": str(message.content or ""),
            }])
            continue

        blocks: list[dict] = []
        if isinstance(message.content, list):
            blocks.extend(_to_anthropic_content_part(part) for part in message.content)
        elif str(message.content or ""):
            blocks.append({"type": "text", "text": str(message.content)})
        if message.role == "assistant" and message.tool_calls:
            blocks.extend(_to_anthropic_tool_use(call) for call in message.tool_calls)
        if message.role in {"user", "assistant"}:
            append_message(message.role, blocks)
    body = {
        "model": cfg.get("model", req.model),
        "max_tokens": cfg.get("max_tokens", req.max_tokens),
        "temperature": cfg.get("temperature", req.temperature),
        "messages": messages,
    }
    if stable_system or dynamic_system:
        if _anthropic_prompt_cache_enabled(cfg):
            # Anthropic's prefix hierarchy is tools -> system -> messages. A
            # breakpoint follows only the stable kernel. Per-call subagent
            # assignment stays in a later uncached system block.
            system_blocks = []
            if stable_system:
                stable_block = {"type": "text", "text": stable_system}
                stable_block["cache_control"] = {"type": "ephemeral"}
                system_blocks.append(stable_block)
            if dynamic_system:
                system_blocks.append({"type": "text", "text": dynamic_system})
            body["system"] = system_blocks
        else:
            body["system"] = "\n\n".join(
                part for part in (stable_system, dynamic_system) if part
            )
    if req.tools:
        body["tools"] = [{
            "name": item.get("function", {}).get("name", ""),
            "description": item.get("function", {}).get("description", ""),
            "input_schema": item.get("function", {}).get("parameters", {"type": "object", "properties": {}}),
        } for item in req.tools if item.get("function", {}).get("name")]
        body["tool_choice"] = {"type": "auto"}
    if req.stream:
        body["stream"] = True
    return body


def _anthropic_prompt_cache_enabled(cfg: dict) -> bool:
    """Return the explicit cache policy for Anthropic-compatible transports."""
    from agent.llm.prompt_assembly import prompt_cache_enabled
    return prompt_cache_enabled(cfg)


def _has_anthropic_prompt_cache(body: dict) -> bool:
    system = body.get("system")
    return bool(
        isinstance(system, list)
        and any(isinstance(block, dict) and block.get("cache_control") for block in system)
    )


def _without_anthropic_prompt_cache(body: dict) -> dict:
    """Remove cache annotations without changing the request's semantics."""
    clean = json.loads(json.dumps(body))
    system = clean.get("system")
    if isinstance(system, list):
        blocks = []
        for block in system:
            if not isinstance(block, dict):
                continue
            item = dict(block)
            item.pop("cache_control", None)
            blocks.append(item)
        if len(blocks) == 1 and blocks[0].get("type") == "text":
            clean["system"] = str(blocks[0].get("text") or "")
        else:
            clean["system"] = blocks
    for tool in clean.get("tools") or []:
        if isinstance(tool, dict):
            tool.pop("cache_control", None)
    return clean


def _anthropic_prompt_cache_rejected(response: LLMResponse) -> bool:
    metadata = response.metadata or {}
    # Gateways often collapse an unsupported extension field into a generic
    # invalid-request response. Retrying the identical semantic request once
    # without cache metadata is safe; any genuine schema error remains visible
    # on the second response.
    return int(metadata.get("http_status") or 0) in {400, 422}


def _to_anthropic_tool_use(call: dict) -> dict:
    function = call.get("function") or {}
    name = str(function.get("name") or call.get("name") or "")
    arguments = function.get("arguments", call.get("arguments", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return {
        "type": "tool_use",
        "id": str(call.get("id") or ""),
        "name": name,
        "input": arguments,
    }


def _to_anthropic_content_part(part: dict) -> dict:
    if part.get("type") != "image_url":
        return part
    url = str((part.get("image_url") or {}).get("url") or "")
    prefix, separator, data = url.partition(",")
    if not separator or not prefix.startswith("data:") or ";base64" not in prefix:
        raise ValueError("Anthropic Messages image input requires a base64 data URL")
    media_type = prefix[5:].split(";", 1)[0]
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def _parse_anthropic_messages_response(data: dict, cfg: dict) -> LLMResponse:
    content = []
    calls = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            content.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            calls.append(LLMToolCall(id=str(block.get("id") or ""), name=str(block.get("name") or ""), arguments=dict(block.get("input") or {})))
    return LLMResponse(content="".join(content), provider=cfg.get("provider", ""), model=data.get("model", cfg.get("model", "")), usage=data.get("usage"), finish_reason=data.get("stop_reason", ""), raw=data, tool_calls=calls)


def _anthropic_messages_stream(url, body, headers, cfg, req) -> LLMResponse:
    import requests as _requests
    content_parts, blocks, usage, model, stop_reason = [], {}, None, cfg.get("model", ""), ""
    try:
        response = _requests.post(url, json=body, headers=headers, timeout=cfg.get("timeout", 120), stream=True)
        if response.status_code != 200:
            detail = response.text[:500]
            return LLMResponse(
                error=f"provider_http_{response.status_code}: {detail[:300]}",
                metadata={
                    "error_type": f"provider_http_{response.status_code}",
                    "http_status": response.status_code,
                    "error_detail": detail[:200],
                },
            )
        for raw in response.iter_lines(decode_unicode=True):
            if _is_stream_cancelled(req):
                response.close()
                return _cancelled_stream_response(
                    content_parts, cfg, model=model,
                    usage=usage, finish_reason=stop_reason,
                )
            if not raw or not raw.startswith("data:"):
                continue
            try:
                event = json.loads(raw[5:].strip())
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "message_start":
                message = event.get("message") or {}; usage = message.get("usage"); model = message.get("model", model)
            elif kind == "content_block_start":
                blocks[event.get("index", 0)] = event.get("content_block") or {}
            elif kind == "content_block_delta":
                delta = event.get("delta") or {}; block = blocks.setdefault(event.get("index", 0), {})
                if delta.get("type") == "text_delta":
                    token = str(delta.get("text") or "")
                    if _is_stream_cancelled(req):
                        response.close()
                        return _cancelled_stream_response(
                            content_parts, cfg, model=model,
                            usage=usage, finish_reason=stop_reason,
                        )
                    content_parts.append(token)
                    if req.metadata.get("stream_to_user") and token: _push_stream_token(token)
                elif delta.get("type") == "input_json_delta":
                    block["_partial_json"] = block.get("_partial_json", "") + str(delta.get("partial_json") or "")
            elif kind == "message_delta":
                delta = event.get("delta") or {}
                stop_reason = str(delta.get("stop_reason") or stop_reason)
                delta_usage = event.get("usage")
                if isinstance(delta_usage, dict):
                    usage = {**(usage or {}), **delta_usage}
    except Exception as exc:
        return LLMResponse(error=f"provider_anthropic_error: {str(exc)[:300]}")
    calls = []
    for block in blocks.values():
        if block.get("type") == "tool_use":
            try: args = json.loads(block.get("_partial_json") or "{}")
            except json.JSONDecodeError: args = {}
            calls.append(LLMToolCall(id=str(block.get("id") or ""), name=str(block.get("name") or ""), arguments=args))
    return LLMResponse(content="".join(content_parts), provider=cfg.get("provider", "minimax"), model=model, usage=usage, finish_reason=stop_reason, tool_calls=calls)


def _parse_message_tool_calls(message: dict) -> list:
    """Parse tool calls from common OpenAI-compatible response shapes."""
    if not isinstance(message, dict):
        return []
    parsed = _parse_tool_calls(message.get("tool_calls", []))
    if parsed:
        return parsed
    function_call = message.get("function_call")
    if isinstance(function_call, dict) and function_call.get("name"):
        return _parse_tool_calls([{
            "id": function_call.get("id", "call_function_0"),
            "function": {
                "name": function_call.get("name", ""),
                "arguments": function_call.get("arguments", "{}"),
            },
        }])
    return []


def _parse_tool_calls(raw) -> list:
    """Parse OpenAI-format tool_calls into LLMToolCall objects."""
    result = []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return result
    for tc in raw:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function", {})
        if not isinstance(fn, dict):
            fn = {}
        name = fn.get("name") or tc.get("name", "")
        arguments = fn.get("arguments", tc.get("arguments", "{}"))
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
        except (json.JSONDecodeError, TypeError):
            args = {}
        result.append(LLMToolCall(
            id=tc.get("id", ""),
            name=name,
            arguments=args,
        ))
    return result
