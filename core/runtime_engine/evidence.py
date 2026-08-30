"""Typed, request-local evidence transport for QueryLoop.

Tools publish ``evidence_parts`` in their normal output. QueryLoop validates
and registers those parts, delivers pending model-consumable evidence on the
next LLM call, and records the delivery in one request-local ledger. Binary
content never enters the ledger, trace, transcript, or persistence.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

EVIDENCE_KINDS = frozenset({
    "image", "text", "structured", "table", "command_output", "tool_result",
})
REFERENCE_KINDS = frozenset({"managed_file", "artifact", "tool_result"})
_ARTIFACT_THRESHOLD_CHARS = 8_000
_PROJECTION_MAX_CHARS = 4_000


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


def register_tool_evidence(
    extras: dict[str, Any],
    results: Iterable[object],
    *,
    workspace_id: str = "",
    session_id: str = "",
    request_id: str = "",
    user_input: str = "",
) -> list[str]:
    """Register every observable tool result as typed evidence.

    Producers may publish richer ``evidence_parts``.  A producer that does not
    do so still receives a canonical ``tool_result`` evidence
    record, which prevents task/cognitive/evidence state from disagreeing.
    Large results are persisted as redacted immutable artifacts and the model
    receives a bounded projection plus the artifact reference.
    """
    registered: list[str] = []
    for result in results:
        output = getattr(result, "output", None)
        if not isinstance(output, dict):
            output = {}
        parts = output.get("evidence_parts")
        if not isinstance(parts, list) or not parts:
            parts = [_tool_result_evidence_part(
                result,
                output,
                workspace_id=workspace_id,
                session_id=session_id,
                request_id=request_id,
                user_input=user_input,
            )]
        registered.extend(register_evidence_parts(
            extras,
            parts,
            source_tool=str(getattr(result, "tool_name", "") or "unknown"),
            source_call_id=str(getattr(result, "call_id", "") or ""),
        ))
    return registered


def _tool_result_evidence_part(
    result: object,
    output: dict[str, Any],
    *,
    workspace_id: str,
    session_id: str,
    request_id: str,
    user_input: str,
) -> dict[str, Any]:
    from storage.redaction import redact_value

    safe_output = redact_value(output)
    serialized = json.dumps(
        safe_output, ensure_ascii=False, separators=(",", ":"), default=str,
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    call_id = str(getattr(result, "call_id", "") or "")
    tool_name = str(getattr(result, "tool_name", "") or "tool").replace("__", ".")
    artifact_id = ""
    if workspace_id and len(serialized) >= _ARTIFACT_THRESHOLD_CHARS:
        try:
            from artifacts.store import save_artifact

            record = save_artifact(
                workspace_id=workspace_id,
                content=serialized,
                artifact_type="tool_evidence",
                title=f"{tool_name} evidence {call_id or digest[:8]}",
                sensitivity="internal",
                run_id=request_id,
                session_id=session_id,
                source="runtime_tool_evidence",
                metadata={
                    "producer_id": tool_name,
                    "source_call_id": call_id,
                    "content_digest": digest,
                    "hidden_from_default_listing": True,
                },
            )
            if record is not None:
                artifact_id = str(record.artifact_id or "")
                if artifact_id:
                    output.setdefault("artifact_ids", [])
                    if artifact_id not in output["artifact_ids"]:
                        output["artifact_ids"].append(artifact_id)
                    output.setdefault("artifact_ref", {"artifact_id": artifact_id})
        except (OSError, RuntimeError, TypeError, ValueError):
            # Evidence registration must never turn a successful tool into a
            # failed operation merely because durable projection is unavailable.
            artifact_id = ""

    projection = _bounded_projection(safe_output, query=user_input)
    reference = (
        {"kind": "artifact", "artifact_id": artifact_id, "digest": digest}
        if artifact_id else
        {"kind": "tool_result", "call_id": call_id, "digest": digest}
    )
    # QueryLoop uses this producer-neutral projection for the immediate
    # continuation call.  The complete result remains in the artifact/run
    # record and is never silently substituted by this bounded view.
    output["_evidence_projection"] = projection
    output["_evidence_content_digest"] = digest
    return {
        "kind": "tool_result",
        "reference": reference,
        "consumer": "llm",
        "coverage": {
            "status": str(output.get("status") or ("succeeded" if bool(getattr(result, "ok", False)) else "failed")),
            "complete": (
                bool(getattr(result, "ok", False))
                and str(output.get("coverage_status") or "complete").lower() == "complete"
                and not bool(output.get("partial") or output.get("truncated"))
            ),
            "content_chars": len(serialized),
        },
        "summary": _tool_result_summary(result, safe_output),
        "projection": projection,
    }


def _tool_result_summary(result: object, output: dict[str, Any]) -> str:
    explicit = str(
        getattr(result, "summary", "")
        or output.get("summary")
        or output.get("message")
        or getattr(result, "error", "")
        or ""
    ).strip()
    if explicit:
        return explicit[:500]
    identity = output.get("connection") if isinstance(output.get("connection"), dict) else {}
    subject = (
        identity.get("name") or identity.get("device_name")
        or output.get("device_id") or output.get("connection_id") or ""
    )
    facts = output.get("facts") if isinstance(output.get("facts"), dict) else {}
    fact_names = ", ".join(str(key) for key in list(facts)[:8])
    base = str(subject or getattr(result, "tool_name", "tool"))
    return (f"{base}: collected {fact_names}" if fact_names else f"{base}: succeeded")[:500]


def _bounded_projection(
    value: Any,
    *,
    max_chars: int = _PROJECTION_MAX_CHARS,
    query: str = "",
) -> Any:
    """Preserve structure while replacing oversized leaves with auditable refs."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value
        excerpts = _relevant_text_excerpts(value, query=query, max_chars=max_chars)
        return {
            "content_chars": len(value),
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            "relevant_excerpts": excerpts,
            "truncated": True,
        }
    if isinstance(value, list):
        projected_items = [
            _bounded_projection(
                item,
                max_chars=max(400, max_chars // max(1, min(len(value), 8))),
                query=query,
            )
            for item in value[:32]
        ]
        if len(value) > 32:
            projected_items.append({"_omitted_items": len(value) - 32, "truncated": True})
        return projected_items
    if isinstance(value, dict):
        projected: dict[str, Any] = {}
        child_limit = max(500, max_chars // max(1, min(len(value), 8)))
        for key, item in list(value.items())[:64]:
            item_limit = (
                max_chars
                if str(key) in {
                    "analysis_projection", "output", "facts", "command_results",
                    "data", "result", "content", "preview",
                }
                else child_limit
            )
            projected[str(key)] = _bounded_projection(item, max_chars=item_limit, query=query)
        if len(value) > 64:
            projected["_omitted_fields"] = len(value) - 64
        return projected
    return str(value)[:max_chars]


def _relevant_text_excerpts(text: str, *, query: str, max_chars: int) -> str:
    """Select query-relevant line windows while retaining document boundaries."""
    lines = str(text or "").splitlines()
    if not lines:
        return text[:max_chars]
    terms = {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_.:/-]{1,31}|[\u4e00-\u9fff]{2,8}", str(query or ""))
        if token.strip()
    }
    selected: set[int] = set(range(min(8, len(lines))))
    selected.update(range(max(0, len(lines) - 4), len(lines)))
    if terms:
        for index, line in enumerate(lines):
            lowered = line.lower()
            if any(term in lowered for term in terms):
                selected.update(range(max(0, index - 2), min(len(lines), index + 3)))
    ordered = sorted(selected)
    chunks: list[str] = []
    previous = -2
    for index in ordered:
        if index != previous + 1 and chunks:
            chunks.append("... [omitted] ...")
        chunks.append(lines[index])
        previous = index
        if len("\n".join(chunks)) >= max_chars:
            break
    return "\n".join(chunks)[:max_chars]


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


def evidence_manifest(
    extras: dict[str, Any],
    *,
    max_items: int = 64,
) -> list[dict[str, Any]]:
    """Return the bounded, model-safe evidence view for final synthesis."""
    manifest: list[dict[str, Any]] = []
    for item in extras.get("evidence_ledger") or []:
        if not isinstance(item, dict):
            continue
        reference = item.get("reference") if isinstance(item.get("reference"), dict) else {}
        manifest.append({
            "evidence_id": str(item.get("evidence_id") or ""),
            "kind": str(item.get("kind") or ""),
            "source_tool": str(item.get("source_tool") or ""),
            "source_call_id": str(item.get("source_call_id") or ""),
            "reference": dict(reference),
            "coverage": dict(item.get("coverage") or {}),
            "summary": str(item.get("summary") or "")[:500],
            "projection": item.get("projection"),
        })
        if len(manifest) >= max_items:
            break
    return manifest


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
    required_reference_keys = {
        "managed_file": "file_id",
        "artifact": "artifact_id",
        "tool_result": "call_id",
    }
    required_key = required_reference_keys[reference_kind]
    if not str(reference.get(required_key) or "").strip():
        return {}, f"{reference_kind}_{required_key}_required"
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
    immediately_delivered = reference_kind in {"artifact", "tool_result"}
    return {
        "evidence_id": f"ev_{digest}",
        "kind": kind,
        "reference": dict(reference),
        "mime_type": str(raw.get("mime_type") or ""),
        "consumer": consumer,
        "coverage": dict(coverage),
        "summary": str(raw.get("summary") or "")[:500],
        "projection": raw.get("projection"),
        "source_tool": source_tool,
        "source_call_id": source_call_id,
        "delivery_status": "delivered" if immediately_delivered else "pending",
        "delivery_count": 1 if immediately_delivered else 0,
    }, ""
