"""Token usage tracking and estimation.

v2.0: Lightweight token estimator (CJK-aware) + JSONL persistence.
No third-party tokenizer required.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from agent.runtime.utils import now_iso
from storage.usage_store import append_usage, clear_usage, read_usage

_LOG = logging.getLogger(__name__)


def _is_cjk(c: str) -> bool:
    cp = ord(c)
    return (
        0x4E00 <= cp <= 0x9FFF or    # CJK Unified Ideographs
        0x3400 <= cp <= 0x4DBF or    # CJK Extension A
        0x3000 <= cp <= 0x303F or    # CJK Symbols and Punctuation
        0x31C0 <= cp <= 0x31EF or    # CJK Strokes
        0x3200 <= cp <= 0x32FF or    # Enclosed CJK Letters
        0x3300 <= cp <= 0x33FF or    # CJK Compatibility
        0xFE30 <= cp <= 0xFE4F or    # CJK Compatibility Forms
        0xFF00 <= cp <= 0xFFEF       # Halfwidth and Fullwidth Forms
    )


def estimate_text(text: str) -> int:
    """Estimate token count from text length (CJK-aware).

    CJK characters: ~1 char per token.
    Latin/ASCII: ~4 chars per token.
    Mixed text uses weighted split.
    """
    if not text:
        return 0
    s = str(text)
    cjk = sum(1 for c in s if _is_cjk(c))
    non_cjk = len(s) - cjk
    return max(1, cjk + non_cjk // 4)


def estimate_messages(messages: list) -> int:
    """Estimate total tokens for a list of messages (dicts, objects, or strings)."""
    total = 0
    for msg in (messages or []):
        if isinstance(msg, str):
            total += estimate_text(msg)
        elif isinstance(msg, dict):
            for v in msg.values():
                total += estimate_text(str(v) if not isinstance(v, str) else v)
        elif hasattr(msg, 'content'):
            total += estimate_text(getattr(msg, 'content', ''))
        else:
            total += estimate_text(str(msg))
    return max(1, total)


@dataclass
class TokenRecord:
    workspace_id: str
    session_id: str = ""
    run_id: str = ""
    turn_id: str = ""
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    source: str = "estimated"
    created_at: str = ""


# Minimal price map — extend as needed
_MODEL_PRICE_PER_1K: dict[str, tuple[float, float]] = {
    # (input_price, output_price) per 1000 tokens
    "minimax-m3": (0.001, 0.003),
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-3.5-turbo": (0.0005, 0.0015),
    "deepseek-v3": (0.0005, 0.001),
    "claude-3.5-sonnet": (0.003, 0.015),
    "claude-3.5-haiku": (0.0008, 0.004),
    "claude-3-opus": (0.015, 0.075),
    "claude-3-haiku": (0.00025, 0.00125),
    "claude-3-sonnet": (0.003, 0.015),
    "default": (0, 0),
}


def record_llm_call(
    workspace_id: str = "default",
    session_id: str = "",
    run_id: str = "",
    turn_id: str = "",
    provider: str = "",
    model: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> dict:
    """Record an LLM call and persist to JSONL."""
    total = input_tokens + output_tokens
    model_key = (model or "default").lower()
    prices = _MODEL_PRICE_PER_1K.get(model_key, _MODEL_PRICE_PER_1K["default"])
    cost = (input_tokens / 1000.0) * prices[0] + (output_tokens / 1000.0) * prices[1]

    record = TokenRecord(
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        turn_id=turn_id,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total,
        estimated_cost=round(cost, 6),
        created_at=now_iso(),
    )
    try:
        append_usage(workspace_id, {
            "workspace_id": record.workspace_id,
            "session_id": record.session_id,
            "run_id": record.run_id,
            "turn_id": record.turn_id,
            "provider": record.provider,
            "model": record.model,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "total_tokens": record.total_tokens,
            "estimated_cost": record.estimated_cost,
            "source": "estimated",
            "created_at": record.created_at,
        })
    except Exception:
        _LOG.warning("token usage persistence failed", exc_info=True)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
        "estimated_cost": record.estimated_cost,
    }


def get_usage(workspace_id: str = "default", session_id: str = "") -> dict:
    """Get aggregated usage stats."""
    rows = read_usage(workspace_id)
    if not rows:
        return _empty_usage(workspace_id, session_id)

    input_t, output_t, total_t, cost, count = 0, 0, 0, 0.0, 0
    latest = ""
    try:
        for rec in rows:
            if session_id and rec.get("session_id") != session_id:
                continue
            input_t += rec.get("input_tokens", 0)
            output_t += rec.get("output_tokens", 0)
            cost += rec.get("estimated_cost", 0)
            count += 1
            latest = rec.get("created_at", "")
    except Exception:
        pass

    return {
        "ok": True,
        "workspace_id": workspace_id,
        "session_id": session_id,
        "input_tokens": input_t,
        "output_tokens": output_t,
        "total_tokens": input_t + output_t,
        "estimated_cost": round(cost, 6),
        "call_count": count,
        "last_updated": latest,
    }


def _empty_usage(workspace_id: str, session_id: str) -> dict:
    return {
        "ok": True,
        "workspace_id": workspace_id,
        "session_id": session_id,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost": 0,
        "call_count": 0,
        "last_updated": "",
    }


def reset_usage_for_tests(workspace_id: str = "default"):
    """Remove usage data for tests."""
    clear_usage(workspace_id)
