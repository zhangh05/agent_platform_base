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
_ACTION_COMPLETION_CLAIM_RE = re.compile(
    r"(?:我)?(?:已经|已)(?:成功)?(?:执行|部署|修改|删除|创建|上传|保存|写入|重启|关闭|连接|配置|发布)"
    r"|(?:executed|deployed|modified|deleted|created|uploaded|saved|restarted|connected)\s+successfully",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?:password|passwd|api[_-]?key|access[_-]?token|authorization|community)"
    r"\s*(?:=|:)\s*(?!\[REDACTED_SECRET\])\S+"
    r"|\bsk-[A-Za-z0-9]{20,}\b",
    re.IGNORECASE,
)
_REFERENCE_RE = re.compile(r"\b(?:art|job|run|report|trace)[A-Za-z0-9_-]{8,64}\b")


def _is_textual_continuation_edit_claim(claim: str, contract: dict | None) -> bool:
    """Allow only textual scope/rewrite edits, never external action claims."""
    if not isinstance(contract, dict):
        return False
    relation = contract.get("relation")
    kind = str(relation.get("kind") or "") if isinstance(relation, dict) else ""
    if kind not in {"scope", "rewrite", "refine", "repair"}:
        return False
    return bool(re.search(r"(?:修改|删除)$", str(claim or "")))


def validate_response_quality(
    text: str,
    *,
    user_input: str = "",
    tool_results: Iterable[object] = (),
    evidence: dict | None = None,
    known_reference_ids: Iterable[str] = (),
    task_continuation_contract: dict | None = None,
) -> list[ResponseQualityIssue]:
    """Return deterministic user-visible quality violations."""
    value = str(text or "")
    tool_results = tuple(tool_results)
    issues: list[ResponseQualityIssue] = []
    continuation_issue = _validate_task_continuation_output(
        value,
        task_continuation_contract,
    )
    if continuation_issue:
        issues.append(continuation_issue)

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

    completion_claims = tuple(_ACTION_COMPLETION_CLAIM_RE.finditer(value))
    unverified_completion_claims = [
        claim for claim in completion_claims
        if not _is_textual_continuation_edit_claim(claim.group(0), task_continuation_contract)
    ]
    if unverified_completion_claims and not _has_successful_tool_result(tool_results):
        issues.append(ResponseQualityIssue(
            code="UNVERIFIED_ACTION_COMPLETION",
            message=(
                "The draft claims a real action completed, but this turn has no successful tool result. "
                "Describe it as a proposal or unverified state, or call the appropriate tool and verify the outcome."
            ),
        ))

    if _SECRET_ASSIGNMENT_RE.search(value):
        issues.append(ResponseQualityIssue(
            code="SENSITIVE_OUTPUT",
            message=(
                "The draft contains a credential-like value. Remove or mask the value while preserving only "
                "the minimum user-visible explanation."
            ),
        ))

    referenced_ids = set(_REFERENCE_RE.findall(value))
    if referenced_ids:
        known_ids = {str(item) for item in known_reference_ids if str(item)}
        known_ids.update(_tool_result_reference_ids(tool_results))
        unknown_ids = sorted(referenced_ids - known_ids)
        if unknown_ids:
            issues.append(ResponseQualityIssue(
                code="UNVERIFIED_REFERENCE",
                message=(
                    "The draft contains identifiers not present in runtime or tool evidence: "
                    + ", ".join(unknown_ids[:5])
                    + ". Remove them or use only identifiers returned by the runtime."
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


def _validate_task_continuation_output(
    text: str,
    contract: dict | None,
) -> ResponseQualityIssue | None:
    """Validate only server-derived mechanical continuation constraints."""
    if not isinstance(contract, dict):
        return None
    validation = contract.get("validation")
    if not isinstance(validation, dict) or validation.get("kind") != "enumerated_items":
        return None
    try:
        expected_count = int(validation.get("expected_new_items") or validation.get("expected_total_items") or 0)
        expected_start = int(validation.get("expected_start_ordinal") or 0)
    except (TypeError, ValueError):
        return None
    if expected_count <= 0 or expected_start <= 0:
        return None
    required_prefix = str(validation.get("required_prefix") or "")
    item_pattern = re.compile(
        r"^\s*(?:[-*+]\s+)?(?P<prefix>[A-Za-z][A-Za-z0-9_-]{0,15}-)?"
        r"(?P<ordinal>\d{1,4})\s*(?:[.、:：)])"
    )
    items = []
    for line in str(text or "").splitlines():
        match = item_pattern.match(line)
        if match:
            items.append((str(match.group("prefix") or ""), int(match.group("ordinal"))))
    expected_ordinals = list(range(expected_start, expected_start + expected_count))
    actual_ordinals = [item[1] for item in items]
    prefixes_ok = not required_prefix or all(prefix == required_prefix for prefix, _ in items)
    if actual_ordinals == expected_ordinals and prefixes_ok:
        return None
    unit = str(validation.get("unit") or "条")
    mode = str(validation.get("mode") or "append")
    if mode == "replace_scope":
        requirement = (
            f"requires exactly {expected_count} total {unit}, numbered continuously "
            f"from {expected_start} through {expected_start + expected_count - 1}"
        )
    else:
        requirement = (
            f"requires exactly {expected_count} new {unit}, numbered continuously "
            f"from {expected_start} through {expected_start + expected_count - 1}"
        )
    return ResponseQualityIssue(
        code="TASK_CONTINUATION_CONTRACT_VIOLATION",
        message=(
            "The server-derived task continuation contract " + requirement
            + (f" with prefix {required_prefix}" if required_prefix else "")
            + ". Regenerate the complete continuation and satisfy this contract exactly."
        ),
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


def _has_successful_tool_result(tool_results: Iterable[object]) -> bool:
    return any(bool(getattr(result, "ok", False)) for result in tool_results)


def _tool_result_reference_ids(tool_results: Iterable[object]) -> set[str]:
    found: set[str] = set()

    def visit(value: object, *, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, key=str(child_key))
            return
        if isinstance(value, (list, tuple)):
            for child in value:
                visit(child, key=key)
            return
        if isinstance(value, str) and (key.endswith("_id") or _REFERENCE_RE.fullmatch(value)):
            found.add(value)

    for result in tool_results:
        visit(getattr(result, "output", None))
    return found
