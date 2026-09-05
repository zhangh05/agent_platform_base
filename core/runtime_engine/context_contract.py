"""Domain-neutral contracts for observations and operational references.

The runtime transports facts from many domains. These helpers define only
their provenance and lifecycle; domain meaning remains with the producer.
"""

from __future__ import annotations

from typing import Any


REFERENCE_STATES = frozenset({"candidate", "confirmed", "superseded", "invalidated"})
OBSERVATION_COMPLETENESS = frozenset({"complete", "partial", "failed", "unknown"})
REFERENCE_AUTHORITIES = frozenset({"observed", "user_confirmed", "declared_intent", "external_authority"})


def normalize_observation_descriptor(value: dict[str, Any]) -> dict[str, Any]:
    """Validate the portable metadata shared by all observation producers."""
    if not isinstance(value, dict):
        raise ValueError("observation_must_be_object")
    observation_id = str(value.get("observation_id") or "").strip()
    source_kind = str(value.get("source_kind") or "").strip()
    observed_at = str(value.get("observed_at") or "").strip()
    completeness = str(value.get("completeness") or "unknown").strip().lower()
    if not observation_id or not source_kind or not observed_at:
        raise ValueError("observation_identity_source_and_time_required")
    if completeness not in OBSERVATION_COMPLETENESS:
        raise ValueError("invalid_observation_completeness")
    return {
        **value,
        "observation_id": observation_id,
        "source_kind": source_kind,
        "observed_at": observed_at,
        "completeness": completeness,
        # An observation is a fact about one point in time. It can never
        # declare itself to be normal, even when collection was complete.
        "authoritative_for_normal": False,
    }


def normalize_reference_descriptor(value: dict[str, Any]) -> dict[str, Any]:
    """Validate lifecycle metadata without interpreting referenced state."""
    if not isinstance(value, dict):
        raise ValueError("reference_must_be_object")
    reference_id = str(value.get("reference_id") or "").strip()
    state = str(value.get("state") or "candidate").strip().lower()
    authority = str(value.get("authority") or "observed").strip().lower()
    source_observation_ids = [
        str(item).strip() for item in value.get("source_observation_ids") or []
        if str(item).strip()
    ]
    if not reference_id or not source_observation_ids:
        raise ValueError("reference_identity_and_source_required")
    if state not in REFERENCE_STATES:
        raise ValueError("invalid_reference_state")
    if authority not in REFERENCE_AUTHORITIES:
        raise ValueError("invalid_reference_authority")
    if state == "confirmed" and authority == "observed":
        raise ValueError("confirmed_reference_requires_explicit_authority")
    return {
        **value,
        "reference_id": reference_id,
        "state": state,
        "authority": authority,
        "source_observation_ids": list(dict.fromkeys(source_observation_ids)),
        "current": state == "confirmed" and bool(value.get("current", True)),
    }
