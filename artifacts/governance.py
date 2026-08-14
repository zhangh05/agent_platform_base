"""Generic artifact lineage and evidence projection.

LZCore does not define domain authority. It only groups artifacts
by explicit evidence keys when producers provide them, marks the latest complete
item in each stream, and treats everything else as ordinary deliverables.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from artifacts.schemas import ArtifactRecord


AUTHORITY_POLICY = "generic_latest_complete_evidence"


def _metadata(record: ArtifactRecord) -> dict[str, Any]:
    return record.metadata if isinstance(record.metadata, dict) else {}


def _evidence_key(record: ArtifactRecord) -> str:
    key = str(_metadata(record).get("evidence_key", "") or "").strip()
    if key and key != "[REDACTED_SECRET]":
        return key
    producer = str(_metadata(record).get("producer_id", "") or record.run_id or "").strip()
    role = str(_metadata(record).get("evidence_role", "") or "").strip()
    if producer and role:
        return f"{producer}:{role}"
    return ""


def _quality(record: ArtifactRecord) -> str:
    quality = str(_metadata(record).get("evidence_quality", "") or "").strip()
    return quality if quality in {"complete", "partial"} else "unknown"


def build_governance(records: Iterable[ArtifactRecord]) -> dict[str, dict[str, Any]]:
    """Return artifact_id -> generic governance projection for active records."""
    streams: dict[str, list[ArtifactRecord]] = defaultdict(list)
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        key = _evidence_key(record)
        if key:
            streams[key].append(record)

    for key, versions in streams.items():
        ordered = sorted(versions, key=lambda item: (str(item.created_at or ""), item.artifact_id))
        complete = [item for item in ordered if _quality(item) == "complete"]
        selected = complete[-1] if complete else ordered[-1]
        latest = ordered[-1]
        for version, record in enumerate(ordered, start=1):
            quality = _quality(record)
            if record.artifact_id == selected.artifact_id:
                status = "authoritative" if quality == "complete" else "provisional"
            elif quality != "complete":
                status = "incomplete"
            else:
                status = "historical"
            result[record.artifact_id] = {
                "evidence_key": key,
                "evidence_role": str(_metadata(record).get("evidence_role") or "artifact"),
                "evidence_quality": quality,
                "authority_domain": "evidence",
                "authority_status": status,
                "authority_reason": {
                    "authoritative": "同一证据流中的最近一次完整制品",
                    "provisional": "该证据流尚无完整制品，暂用最近一次记录",
                    "incomplete": "不完整制品不会覆盖完整证据",
                    "historical": "已被同一证据流中的更新完整制品替代",
                }[status],
                "authority_policy": AUTHORITY_POLICY,
                "authoritative_artifact_id": selected.artifact_id,
                "latest_artifact_id": latest.artifact_id,
                "is_latest_observation": record.artifact_id == latest.artifact_id,
                "version": version,
                "version_count": len(ordered),
            }
    return result


def governance_summary(records: Iterable[ArtifactRecord]) -> dict[str, Any]:
    materialized = list(records)
    projection = build_governance(materialized)
    counts = Counter(item["authority_status"] for item in projection.values())
    return {
        "policy": AUTHORITY_POLICY,
        "evidence_streams": len({item["evidence_key"] for item in projection.values()}),
        "authoritative": counts["authoritative"],
        "current_state_authoritative": 0,
        "contextual": 0,
        "provisional": counts["provisional"],
        "incomplete": counts["incomplete"],
        "historical": counts["historical"],
        "deliverables": sum(1 for record in materialized if record.artifact_id not in projection),
    }
