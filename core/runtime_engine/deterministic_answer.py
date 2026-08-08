"""Deterministic direct answers for exact, low-risk user requests.

This module is deliberately small and rule-bound.  It handles cases where
LLM generation is the wrong tool because the answer is a mechanical
calculation or a short correction tied to recent conversation state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DeterministicAnswer:
    response: str
    route: str
    reason: str


_SPEED_WITH_UNIT_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>[kKmMgG](?:b|B)(?:it|yte)?(?:/s|ps|每秒)?)"
)

_FOLLOWUP_UNIT_RE = re.compile(r"^\s*(?:我是|是|不是|按|用|应该是)?\s*(?P<label>小b|大B)\s*[。！!？?]?\s*$")


def answer_deterministically(user_input: str, conversation_history: str = "") -> DeterministicAnswer | None:
    """Return a deterministic answer when the current turn is exact enough.

    The first supported family is speed-unit conversion.  Network speed units
    are case-sensitive: lowercase ``b`` is bit, uppercase ``B`` is byte.  A
    follow-up like "我是小b" is treated as a correction to the latest speed
    value in conversation history, not as a user identity statement.
    """
    text = (user_input or "").strip()
    if not text:
        return None

    followup = _FOLLOWUP_UNIT_RE.match(text)
    if followup:
        prior = _extract_latest_speed_value(conversation_history)
        if prior is None:
            return None
        value, _unit = prior
        label = followup.group("label")
        forced_unit = "kb/s" if label == "小b" else "KB/s"
        return DeterministicAnswer(
            response=_format_speed_answer(value, forced_unit, correction_label=label),
            route="deterministic_speed_unit_correction",
            reason="short unit correction with prior speed value",
        )

    match = _SPEED_WITH_UNIT_RE.search(text)
    if not match:
        return None

    if not _looks_like_speed_question(text):
        return None

    value = float(match.group("value"))
    unit = _normalize_speed_unit(match.group("unit"))
    if unit is None:
        return None
    return DeterministicAnswer(
        response=_format_speed_answer(value, unit),
        route="deterministic_speed_unit_conversion",
        reason="case-sensitive speed unit conversion",
    )


def _looks_like_speed_question(text: str) -> bool:
    if any(k in text for k in ("速度", "带宽", "网速", "下载", "上传", "是多少", "多快", "换算")):
        return True
    return bool(_SPEED_WITH_UNIT_RE.fullmatch(text.strip()))


def _extract_latest_speed_value(conversation_history: str) -> tuple[float, str] | None:
    latest: tuple[float, str] | None = None
    for match in _SPEED_WITH_UNIT_RE.finditer(conversation_history or ""):
        unit = _normalize_speed_unit(match.group("unit"))
        if unit is None:
            continue
        latest = (float(match.group("value")), unit)
    return latest


def _normalize_speed_unit(raw: str) -> str | None:
    unit = (raw or "").strip()
    if not unit:
        return None

    suffix = "/s"
    compact = unit.replace("每秒", "/s")
    if compact.lower().endswith("ps"):
        suffix = "ps"
        compact = compact[:-2]
    elif compact.endswith("/s"):
        compact = compact[:-2]

    prefix = compact[0]
    marker = compact[1:]
    if not prefix:
        return None
    scale = prefix.lower()
    if scale not in {"k", "m", "g"}:
        return None

    if marker.startswith("B") or marker.startswith("Byte"):
        return f"{scale.upper()}B/{'s' if suffix == '/s' else 's'}"
    if marker.startswith("b") or marker.startswith("bit"):
        return f"{scale}b/{'s' if suffix == '/s' else 's'}"
    return None


def _format_speed_answer(value: float, unit: str, correction_label: str = "") -> str:
    scale = unit[0]
    is_byte = unit[1] == "B"
    bits_per_second = _to_bits_per_second(value, scale, is_byte)

    mbps = bits_per_second / 1_000_000
    kb_per_s = bits_per_second / 8 / 1000
    mb_per_s = bits_per_second / 8 / 1_000_000
    kib_per_s = bits_per_second / 8 / 1024
    mib_per_s = bits_per_second / 8 / 1024 / 1024

    if correction_label == "小b":
        lead = "对，按小写 b 计算，b 是 bit，不是 Byte。"
    elif correction_label == "大B":
        lead = "对，按大写 B 计算，B 是 Byte。"
    else:
        lead = f"{_fmt(value)} {unit} 的换算结果："

    return (
        f"{lead}\n\n"
        f"- 网络带宽口径：约 {_fmt(mbps)} Mbps\n"
        f"- 下载/传输口径：约 {_fmt(kb_per_s)} KB/s，也就是约 {_fmt(mb_per_s)} MB/s\n"
        f"- 如果按二进制显示：约 {_fmt(kib_per_s)} KiB/s，也就是约 {_fmt(mib_per_s)} MiB/s\n\n"
        "注意：小写 b 表示 bit，大写 B 表示 Byte；1 Byte = 8 bit。"
    )


def _to_bits_per_second(value: float, scale: str, is_byte: bool) -> float:
    multiplier = {
        "k": 1_000,
        "m": 1_000_000,
        "g": 1_000_000_000,
    }[scale.lower()]
    bits = value * multiplier
    if is_byte:
        bits *= 8
    return bits


def _fmt(value: float) -> str:
    if abs(value) >= 100:
        return f"{value:.0f}"
    if abs(value) >= 10:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{value:.2f}".rstrip("0").rstrip(".")
