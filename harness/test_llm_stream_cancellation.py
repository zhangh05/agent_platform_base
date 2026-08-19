import asyncio
import sys
from types import SimpleNamespace

from agent.llm.schemas import LLMMessage, LLMRequest, LLMResponse
from agent.llm import provider
from core.runtime_engine.models import SSOTRuntimeConfig, StatelessContext
from core.runtime_engine.query_loop import QueryLoop


class _FakeStreamResponse:
    status_code = 200
    text = ""
    encoding = None

    def __init__(self, lines):
        self._lines = list(lines)
        self.closed = False

    def iter_lines(self, decode_unicode=True):
        yield from self._lines

    def close(self):
        self.closed = True


def _install_requests(monkeypatch, response):
    monkeypatch.setitem(
        sys.modules,
        "requests",
        SimpleNamespace(post=lambda *_args, **_kwargs: response, exceptions=SimpleNamespace(Timeout=TimeoutError)),
    )


def test_openai_stream_stops_before_projecting_token_after_cancel(monkeypatch):
    response = _FakeStreamResponse([
        'data: {"choices":[{"delta":{"content":"first"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"content":"second"},"finish_reason":null}]}',
    ])
    _install_requests(monkeypatch, response)
    projected = []
    cancelled = {"value": False}
    monkeypatch.setattr(provider, "_push_stream_token", projected.append)

    def cancel_check():
        return cancelled["value"]

    original_iter = response.iter_lines

    def lines(decode_unicode=True):
        iterator = iter(original_iter(decode_unicode))
        yield next(iterator)
        cancelled["value"] = True
        yield from iterator

    response.iter_lines = lines
    result = provider._api_generate_stream(
        "https://provider.invalid/v1/chat/completions",
        {}, {"api_key": "test", "provider": "test"},
        LLMRequest(task="assistant_chat", metadata={"stream_to_user": True}, cancel_check=cancel_check),
    )

    assert result.content == "first"
    assert result.metadata == {"stream_cancelled": True}
    assert projected == ["first"]
    assert response.closed is True


def test_anthropic_stream_stops_before_projecting_token_after_cancel(monkeypatch):
    response = _FakeStreamResponse([
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"first"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"second"}}',
    ])
    _install_requests(monkeypatch, response)
    projected = []
    cancelled = {"value": False}
    monkeypatch.setattr(provider, "_push_stream_token", projected.append)

    def cancel_check():
        return cancelled["value"]

    original_iter = response.iter_lines

    def lines(decode_unicode=True):
        iterator = iter(original_iter(decode_unicode))
        yield next(iterator)
        cancelled["value"] = True
        yield from iterator

    response.iter_lines = lines
    result = provider._anthropic_messages_stream(
        "https://provider.invalid/v1/messages", {}, {}, {"api_key": "test", "provider": "test"},
        LLMRequest(task="assistant_chat", metadata={"stream_to_user": True}, cancel_check=cancel_check),
    )

    assert result.content == "first"
    assert result.metadata == {"stream_cancelled": True}
    assert projected == ["first"]
    assert response.closed is True


def test_query_loop_passes_trusted_cancel_callback_as_llm_execution_control():
    captured = {}

    def invoke(**kwargs):
        captured.update(kwargs)
        return LLMResponse(content="ok")

    callback = lambda: False
    loop = QueryLoop(SSOTRuntimeConfig(), {}, None, llm_invoke=invoke)
    ctx = StatelessContext(
        workspace_id="ws-stream-cancel",
        session_id="session-stream-cancel",
        request_id="run-stream-cancel",
        user_input="hello",
        extras={"cancel_check": callback},
    )

    result = asyncio.run(loop._call_llm([LLMMessage(role="user", content="hello")], ctx))

    assert result.content == "ok"
    assert captured["extra"]["__runtime_cancel_check"] is callback
    assert "cancel_check" not in captured["extra"]
