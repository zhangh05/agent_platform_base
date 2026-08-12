"""Small deterministic quality gate for user-facing QueryLoop responses.

The gate does not judge writing style or business correctness.  It catches
observable contract violations that should never be persisted as a successful
answer: corrupt Unicode, silently narrowing an explicit all-scope follow-up,
and Markdown tables that are too wide to remain usable in the workbench.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class ResponseQualityIssue:
    code: str
    message: str


_ALL_SCOPE_RE = re.compile(
    r"^(?:全部|所有|全都|都要|每个|每一个|全部都要|all|everything|all of them)[。.!！?？\s]*$",
    re.IGNORECASE,
)
_PARTIAL_SCOPE_RE = re.compile(r"主要|代表(?:性)?|示例|参考|部分|若干|几个")
_EXPLICIT_LIMIT_RE = re.compile(
    r"(?:本次|当前|以下|这里)(?:查询|展示|覆盖|统计)?(?:范围|口径)|"
    r"仅(?:查询|展示|覆盖)|并非全部|未覆盖全部|无法一次覆盖|按.{0,20}(?:范围|口径)"
)


def validate_response_quality(
    text: str,
    *,
    user_input: str = "",
    tool_results: Iterable[object] = (),
    evidence: dict | None = None,
) -> list[ResponseQualityIssue]:
    """Return deterministic user-visible quality violations."""
    value = str(text or "")
    issues: list[ResponseQualityIssue] = []

    if "\ufffd" in value:
        issues.append(ResponseQualityIssue(
            code="CORRUPT_UNICODE",
            message="The answer contains the Unicode replacement character (�). Regenerate the affected words.",
        ))

    if (
        _ALL_SCOPE_RE.fullmatch(str(user_input or "").strip())
        and _PARTIAL_SCOPE_RE.search(value)
        and not _EXPLICIT_LIMIT_RE.search(value)
    ):
        issues.append(ResponseQualityIssue(
            code="SCOPE_SILENTLY_NARROWED",
            message=(
                "The user explicitly requested all items, but the answer silently presents a representative "
                "subset. Complete a defensible explicit scope, or state the exact coverage/limitation before "
                "presenting partial results. Never label a subset as complete."
            ),
        ))

    widest = max(_markdown_table_widths(value), default=0)
    if widest > 7:
        issues.append(ResponseQualityIssue(
            code="TABLE_TOO_WIDE",
            message=(
                f"The answer contains a {widest}-column Markdown table, which is not usable in the chat view. "
                "Reformat it as an answer-first summary plus compact tables with at most 7 columns, or split by entity."
            ),
        ))

    if _has_weather_evidence(tool_results) and re.search(
        r"防务提示|中等毛毛雨|大毛毛雨|雷暴伴小冰�",
        value,
    ):
        issues.append(ResponseQualityIssue(
            code="UNNATURAL_WEATHER_TERMINOLOGY",
            message=(
                "The weather answer contains corrupt or literal provider wording. Use natural Chinese weather "
                "terms and 防护/出行提示; preserve forecast uncertainty instead of overstating hail."
            ),
        ))

    delivered_images = int(((evidence or {}).get("delivered_by_kind") or {}).get("image", 0) or 0)
    if delivered_images and re.search(
        r"(?:无法|不能|未能|没法).{0,18}(?:查看|读取|识别|分析).{0,12}(?:图片|图像|视觉内容)|"
        r"(?:图片|图像).{0,12}(?:未发送|不可见|无法访问)",
        value,
        re.IGNORECASE,
    ):
        issues.append(ResponseQualityIssue(
            code="DELIVERED_EVIDENCE_DENIED",
            message=(
                f"The runtime delivered {delivered_images} image evidence part(s), but the draft claims the "
                "images were unavailable. Analyze the delivered visual evidence and answer from it; do not deny "
                "evidence that the runtime confirms was supplied."
            ),
        ))

    return issues


def build_response_quality_nudge(issues: Iterable[ResponseQualityIssue]) -> str:
    details = "\n".join(f"- {issue.code}: {issue.message}" for issue in issues)
    return (
        "[RUNTIME RESPONSE QUALITY CORRECTION]\n"
        "The draft answer was not accepted for persistence. Correct only the listed defects while preserving "
        "verified evidence and the user's original request. You may call tools if scope evidence is missing. "
        "Return a clean user-facing answer, not a discussion of this correction.\n"
        + details
    )


def _markdown_table_widths(text: str) -> list[int]:
    lines = str(text or "").replace("\r\n", "\n").split("\n")
    widths: list[int] = []
    for index in range(len(lines) - 1):
        header = lines[index].strip()
        separator = lines[index + 1].strip()
        if "|" not in header or "|" not in separator:
            continue
        if not re.fullmatch(r"\|?[\s:|-]+\|?", separator):
            continue
        cells = _split_table_row(header)
        separator_cells = _split_table_row(separator)
        if len(cells) >= 2 and len(separator_cells) >= 2:
            widths.append(len(cells))
    return widths


def _split_table_row(line: str) -> list[str]:
    value = line.strip().strip("|")
    return [cell.strip() for cell in value.split("|")]


def _has_weather_evidence(tool_results: Iterable[object]) -> bool:
    for result in tool_results:
        output = getattr(result, "output", None)
        if not isinstance(output, dict):
            continue
        source_type = str(output.get("source_type") or "")
        tool_id = str(output.get("tool_id") or "")
        if source_type == "structured_weather" or ".weather." in tool_id:
            return True
    return False
