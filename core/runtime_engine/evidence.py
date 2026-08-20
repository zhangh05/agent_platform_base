"""Typed, request-local evidence transport for QueryLoop.

Tools publish ``evidence_parts`` in their normal output. QueryLoop validates
and registers those parts, delivers pending model-consumable evidence on the
next LLM call, and records the delivery in one request-local ledger. Binary
content never enters the ledger, trace, transcript, or persistence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

EVIDENCE_KINDS = frozenset({"image"})
REFERENCE_KINDS = frozenset({"managed_file"})


def managed_image_evidence(
    file_id: str,
    *,
    image_index: int | None = None,
    source_file_id: str = "",
    mime_type: str = "",
) -> dict[str, Any]:
    """Build the canonical tool-output representation for one managed image."""
    coverage: dict[str, Any] = {}
    if image_index is not None:
        coverage["image_index"] = int(image_index)
    if source_file_id:
        coverage["source_file_id"] = str(source_file_id)
    return {
        "kind": "image",
        "reference": {"kind": "managed_file", "file_id": str(file_id)},
        "mime_type": str(mime_type or ""),
        "consumer": "llm",
        "coverage": coverage,
    }


def initialize_evidence_ledger(extras: dict[str, Any]) -> None:
    """Register original image attachments once for the first model turn."""
    extras.setdefault("evidence_ledger", [])
    initial: list[dict[str, Any]] = []
    for attachment in extras.get("attachments") or []:
        if not isinstance(attachment, dict) or attachment.get("kind") != "image":
            continue
        file_id = str(attachment.get("file_id") or "").strip()
        if not file_id:
            continue
        initial.append(managed_image_evidence(
            file_id,
            mime_type=str(attachment.get("mime_type") or ""),
        ))
    register_evidence_parts(extras, initial, source_tool="user.attachment", source_call_id="user")


def register_tool_evidence(extras: dict[str, Any], results: Iterable[object]) -> list[str]:
    """Validate and register evidence emitted by successful tool results."""
    registered: list[str] = []
    for result in results:
        if not bool(getattr(result, "ok", False)):
            continue
        output = getattr(result, "output", None)
        if not isinstance(output, dict):
            continue
        parts = output.get("evidence_parts")
        if not isinstance(parts, list):
            continue
        registered.extend(register_evidence_parts(
            extras,
            parts,
            source_tool=str(getattr(result, "tool_name", "") or "unknown"),
            source_call_id=str(getattr(result, "call_id", "") or ""),
        ))
    return registered


def register_evidence_parts(
    extras: dict[str, Any],
    parts: Iterable[object],
    *,
    source_tool: str,
    source_call_id: str,
) -> list[str]:
    ledger = extras.setdefault("evidence_ledger", [])
    if not isinstance(ledger, list):
        ledger = []
        extras["evidence_ledger"] = ledger
    known_ids = {
        str(item.get("evidence_id") or "")
        for item in ledger if isinstance(item, dict)
    }
    registered: list[str] = []
    rejected = extras.setdefault("evidence_rejections", [])
    for raw in parts:
        normalized, error = _normalize_part(
            raw,
            source_tool=source_tool,
            source_call_id=source_call_id,
        )
        if error:
            if isinstance(rejected, list):
                rejected.append({"source_tool": source_tool, "source_call_id": source_call_id, "error": error})
            continue
        evidence_id = str(normalized["evidence_id"])
        if evidence_id in known_ids:
            continue
        ledger.append(normalized)
        known_ids.add(evidence_id)
        registered.append(evidence_id)
    return registered


def pending_llm_evidence(extras: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in extras.get("evidence_ledger") or []
        if isinstance(item, dict)
        and item.get("consumer") == "llm"
        and item.get("delivery_status") == "pending"
    ]


def mark_evidence_delivered(extras: dict[str, Any], evidence_ids: Iterable[str]) -> None:
    ids = {str(value) for value in evidence_ids if str(value)}
    if not ids:
        return
    for item in extras.get("evidence_ledger") or []:
        if isinstance(item, dict) and str(item.get("evidence_id") or "") in ids:
            item["delivery_status"] = "delivered"
            item["delivery_count"] = int(item.get("delivery_count", 0) or 0) + 1


def evidence_to_vision_references(parts: Iterable[object]) -> list[dict[str, Any]]:
    """Project typed image evidence to the FileStore vision resolver input."""
    references: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict) or part.get("kind") != "image":
            continue
        reference = part.get("reference")
        if not isinstance(reference, dict) or reference.get("kind") != "managed_file":
            continue
        file_id = str(reference.get("file_id") or "").strip()
        if not file_id:
            continue
        projected = {"file_id": file_id, "kind": "image"}
        coverage = part.get("coverage")
        if isinstance(coverage, dict) and coverage.get("image_index") is not None:
            projected["image_index"] = coverage["image_index"]
        references.append(projected)
    return references


def evidence_summary(extras: dict[str, Any]) -> dict[str, Any]:
    ledger = [item for item in extras.get("evidence_ledger") or [] if isinstance(item, dict)]
    by_kind: dict[str, int] = {}
    delivered_by_kind: dict[str, int] = {}
    for item in ledger:
        kind = str(item.get("kind") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if item.get("delivery_status") == "delivered":
            delivered_by_kind[kind] = delivered_by_kind.get(kind, 0) + 1
    return {
        "registered": len(ledger),
        "pending": sum(1 for item in ledger if item.get("delivery_status") == "pending"),
        "delivered": sum(1 for item in ledger if item.get("delivery_status") == "delivered"),
        "by_kind": by_kind,
        "delivered_by_kind": delivered_by_kind,
        "rejected": len(extras.get("evidence_rejections") or []),
    }


def _normalize_part(
    raw: object,
    *,
    source_tool: str,
    source_call_id: str,
) -> tuple[dict[str, Any], str]:
    if not isinstance(raw, dict):
        return {}, "evidence_part_must_be_object"
    kind = str(raw.get("kind") or "").strip()
    if kind not in EVIDENCE_KINDS:
        return {}, "invalid_evidence_kind"
    reference = raw.get("reference")
    if not isinstance(reference, dict):
        return {}, "evidence_reference_required"
    reference_kind = str(reference.get("kind") or "").strip()
    if reference_kind not in REFERENCE_KINDS:
        return {}, "invalid_evidence_reference_kind"
    if reference_kind == "managed_file" and not str(reference.get("file_id") or "").strip():
        return {}, "managed_file_id_required"
    consumer = str(raw.get("consumer") or "llm").strip()
    if consumer not in {"llm", "tool", "frontend"}:
        return {}, "invalid_evidence_consumer"
    if consumer != "llm":
        return {}, "unsupported_evidence_consumer"
    coverage = raw.get("coverage") or {}
    if not isinstance(coverage, dict):
        return {}, "invalid_evidence_coverage"
    identity = {
        "kind": kind,
        "reference": reference,
        "coverage": coverage,
        "source_tool": source_tool,
        "source_call_id": source_call_id,
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]
    return {
        "evidence_id": f"ev_{digest}",
        "kind": kind,
        "reference": dict(reference),
        "mime_type": str(raw.get("mime_type") or ""),
        "consumer": consumer,
        "coverage": dict(coverage),
        "source_tool": source_tool,
        "source_call_id": source_call_id,
        "delivery_status": "pending" if consumer == "llm" else "registered",
        "delivery_count": 0,
    }, ""
