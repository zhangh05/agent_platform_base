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
    if not cfg.get("enabled") or cfg.get("provider_type") == "disabled":
        return LLMResponse(error="LLM disabled", metadata={"error_type": ERROR_TYPE_DISABLED_BY_USER})
    if cfg.get("provider_type") == "mock":
        return _mock_generate(req, cfg)
    return _api_generate(req, cfg)


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


def _api_generate(req: LLMRequest, cfg: dict) -> LLMResponse:
    if not cfg.get("api_key"):
        return LLMResponse(
            error="API key not configured",
            metadata={"error_type": ERROR_TYPE_MISSING_API_KEY},
        )
    try:
        # MiniMax-M3 accepts image/video content through its Anthropic
        # compatibility endpoint, not its OpenAI chat-completions endpoint.
        # Keep the established OpenAI route for ordinary text/tool turns.
        if _uses_minimax_m3_vision(req, cfg):
            return _minimax_m3_vision_generate(req, cfg)
        url = cfg.get("base_url", "https://api.minimaxi.com/v1").rstrip("/") + "/chat/completions"
        body_dict = {
            "model": cfg.get("model", req.model),
            "messages": [_format_message(m) for m in req.messages],
            "temperature": cfg.get("temperature", req.temperature),
            "max_tokens": cfg.get("max_tokens", req.max_tokens),
        }
        if req.tools:
            body_dict["tools"] = req.tools
            body_dict["tool_choice"] = "auto"

        # Streaming mode: use requests with stream=True
        if req.stream:
            body_dict["stream"] = True
            return _api_generate_stream(url, body_dict, cfg, req)

        body = json.dumps(body_dict).encode()
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
        r = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(r, timeout=cfg.get("timeout", 90)) as resp:
            d = json.loads(resp.read().decode())
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
            if not line:
                continue
            if not line.startswith("data: "):
                continue

            data_str = line[6:]  # Remove "data: " prefix
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

            choices = chunk.get("choices", [])
            if not choices:
                continue

            choice = choices[0]
            delta = choice.get("delta", {})
            finish_reason = choice.get("finish_reason", finish_reason)

            # Token content
            token = delta.get("content", "")
            if token:
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

            # Usage (usually in last chunk)
            if chunk.get("usage"):
                usage = chunk["usage"]
            if chunk.get("model"):
                provider_model = chunk["model"]

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


def _uses_minimax_m3_vision(req: LLMRequest, cfg: dict) -> bool:
    if str(cfg.get("provider") or "").lower() != "minimax":
        return False
    if str(cfg.get("model") or "").lower() != "minimax-m3":
        return False
    return any(
        isinstance(message.content, list)
        and any(isinstance(part, dict) and part.get("type") == "image_url" for part in message.content)
        for message in req.messages
    )


def _minimax_m3_vision_generate(req: LLMRequest, cfg: dict) -> LLMResponse:
    """Call MiniMax-M3's documented Anthropic-compatible vision endpoint."""
    try:
        import requests as _requests
        from urllib.parse import urlsplit, urlunsplit

        base = str(cfg.get("base_url") or "https://api.minimaxi.com/v1")
        parsed = urlsplit(base)
        vision_url = urlunsplit((parsed.scheme, parsed.netloc, "/anthropic/v1/messages", "", ""))
        body = _to_minimax_anthropic_request(req, cfg)
        headers = {
            "Content-Type": "application/json",
            "x-api-key": cfg.get("api_key", ""),
            "anthropic-version": "2023-06-01",
        }
        if not req.stream:
            response = _requests.post(vision_url, json=body, headers=headers, timeout=cfg.get("timeout", 120))
            if response.status_code != 200:
                return LLMResponse(error=f"provider_http_{response.status_code}: {response.text[:300]}", metadata={"http_status": response.status_code})
            return _parse_minimax_anthropic_response(response.json(), cfg)
        return _minimax_m3_vision_stream(vision_url, body, headers, cfg, req)
    except Exception as exc:
        return LLMResponse(error=f"provider_vision_error: {str(exc)[:300]}")


def _to_minimax_anthropic_request(req: LLMRequest, cfg: dict) -> dict:
    system = "\n\n".join(str(m.content) for m in req.messages if m.role == "system" and isinstance(m.content, str))
    messages = []
    for message in req.messages:
        if message.role == "system":
            continue
        content = message.content
        if isinstance(content, list):
            content = [_to_anthropic_content_part(part) for part in content]
        messages.append({"role": message.role, "content": content})
    body = {"model": cfg.get("model", req.model), "max_tokens": cfg.get("max_tokens", req.max_tokens), "messages": messages}
    if system:
        body["system"] = system
    if req.tools:
        body["tools"] = [{
            "name": item.get("function", {}).get("name", ""),
            "description": item.get("function", {}).get("description", ""),
            "input_schema": item.get("function", {}).get("parameters", {"type": "object", "properties": {}}),
        } for item in req.tools if item.get("function", {}).get("name")]
    if req.stream:
        body["stream"] = True
    return body


def _to_anthropic_content_part(part: dict) -> dict:
    if part.get("type") != "image_url":
        return part
    url = str((part.get("image_url") or {}).get("url") or "")
    prefix, separator, data = url.partition(",")
    if not separator or not prefix.startswith("data:") or ";base64" not in prefix:
        raise ValueError("MiniMax-M3 vision requires a base64 data URL")
    media_type = prefix[5:].split(";", 1)[0]
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def _parse_minimax_anthropic_response(data: dict, cfg: dict) -> LLMResponse:
    content = []
    calls = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            content.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            calls.append(LLMToolCall(id=str(block.get("id") or ""), name=str(block.get("name") or ""), arguments=dict(block.get("input") or {})))
    return LLMResponse(content="".join(content), provider=cfg.get("provider", "minimax"), model=data.get("model", cfg.get("model", "")), usage=data.get("usage"), finish_reason=data.get("stop_reason", ""), raw=data, tool_calls=calls)


def _minimax_m3_vision_stream(url, body, headers, cfg, req) -> LLMResponse:
    import requests as _requests
    content_parts, blocks, usage, model, stop_reason = [], {}, None, cfg.get("model", ""), ""
    try:
        response = _requests.post(url, json=body, headers=headers, timeout=cfg.get("timeout", 120), stream=True)
        if response.status_code != 200:
            return LLMResponse(error=f"provider_http_{response.status_code}: {response.text[:300]}", metadata={"http_status": response.status_code})
        for raw in response.iter_lines(decode_unicode=True):
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
                    token = str(delta.get("text") or ""); content_parts.append(token)
                    if req.metadata.get("stream_to_user") and token: _push_stream_token(token)
                elif delta.get("type") == "input_json_delta":
                    block["_partial_json"] = block.get("_partial_json", "") + str(delta.get("partial_json") or "")
            elif kind == "message_delta":
                delta = event.get("delta") or {}; stop_reason = str(delta.get("stop_reason") or stop_reason); usage = event.get("usage") or usage
    except Exception as exc:
        return LLMResponse(error=f"provider_vision_error: {str(exc)[:300]}")
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
