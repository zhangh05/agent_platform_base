"""Canonical tool adapter for the shared location-resolution subsystem."""

from __future__ import annotations

import re

from core.resolution import resolve_location, resolve_locations, reverse_location
from core.tools.general_tools.shared import _error_inv, _result
from core.tools.schemas import ToolInvocation


def handle_location_manage(inv: ToolInvocation) -> dict:
    args = inv.arguments or {}
    action = str(args.get("action") or "").strip().lower()
    language = str(args.get("language") or "zh").strip() or "zh"
    country_code = str(args.get("country_code") or "").strip().upper()
    admin_hint = str(args.get("admin_hint") or "").strip()
    try:
        limit = max(1, min(int(args.get("limit") or 5), 10))
    except (TypeError, ValueError):
        return _error_inv(inv, "limit must be an integer from 1 to 10")
    if country_code and not re.fullmatch(r"[A-Z]{2}", country_code):
        return _error_inv(inv, "country_code must be a two-letter ISO country code")

    if action == "resolve":
        resolution = resolve_location(
            str(args.get("query") or ""), language=language,
            country_code=country_code, admin_hint=admin_hint, limit=limit,
        )
        payload = resolution.as_dict()
        if resolution.resolved is not None:
            payload.update({
                "canonical_name": resolution.resolved.canonical_name,
                "latitude": resolution.resolved.latitude,
                "longitude": resolution.resolved.longitude,
            })
        return _result(inv, resolution.ok, payload)

    if action == "resolve_batch":
        queries = args.get("queries")
        if not isinstance(queries, list):
            return _error_inv(inv, "queries must be an array of 2-20 location strings")
        if any(not isinstance(item, str) for item in queries):
            return _error_inv(inv, "every queries item must be a string")
        cleaned = list(dict.fromkeys(
            str(item or "").strip() for item in queries if str(item or "").strip()
        ))
        if not 2 <= len(cleaned) <= 20:
            return _error_inv(inv, "queries must contain 2-20 unique non-empty locations")
        resolutions = resolve_locations(
            cleaned, language=language, country_code=country_code,
            admin_hint=admin_hint, limit=limit,
        )
        results = [item.as_dict() for item in resolutions]
        resolved_entities = [
            {
                "name": item.resolved.canonical_name,
                "latitude": item.resolved.latitude,
                "longitude": item.resolved.longitude,
            }
            for item in resolutions
            if item.ok and item.resolved is not None
        ]
        resolved_count = sum(item.ok for item in resolutions)
        complete = resolved_count == len(cleaned)
        return _result(inv, resolved_count > 0, {
            "status": "resolved" if complete else ("partial" if resolved_count else "failed"),
            "coverage_status": "complete" if complete else ("partial" if resolved_count else "failed"),
            "partial": 0 < resolved_count < len(cleaned),
            "results": results,
            "resolved_entities": resolved_entities,
            "coverage": {
                "requested": cleaned,
                "resolved": [item.query for item in resolutions if item.ok],
                "unresolved": [item.query for item in resolutions if not item.ok],
                "requested_count": len(cleaned),
                "resolved_count": resolved_count,
            },
        })

    if action == "reverse":
        if isinstance(args.get("latitude"), bool) or isinstance(args.get("longitude"), bool):
            return _error_inv(inv, "latitude and longitude must be valid numbers")
        try:
            latitude = float(args["latitude"])
            longitude = float(args["longitude"])
        except (KeyError, TypeError, ValueError):
            return _error_inv(inv, "latitude and longitude must be valid numbers")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            return _error_inv(inv, "latitude or longitude is outside the valid range")
        resolution = reverse_location(latitude, longitude, language=language)
        payload = resolution.as_dict()
        if resolution.resolved is not None:
            payload.update({
                "canonical_name": resolution.resolved.canonical_name,
                "latitude": resolution.resolved.latitude,
                "longitude": resolution.resolved.longitude,
            })
        return _result(inv, resolution.ok, payload)

    return _error_inv(inv, "unsupported action; expected resolve|resolve_batch|reverse")


__all__ = ["handle_location_manage"]
