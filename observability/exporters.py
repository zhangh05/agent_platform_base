"""Optional trace exporters; local trace persistence remains canonical."""

from __future__ import annotations

import json
import os
import urllib.request
import hashlib
from datetime import datetime
from typing import Any, Protocol


class TraceExporter(Protocol):
    def export(self, trace: dict[str, Any]) -> None: ...


class JsonlTraceExporter:
    def __init__(self, path: str):
        self.path = path

    def export(self, trace: dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(trace, ensure_ascii=False, default=str) + "\n")


class OtlpHttpTraceExporter:
    """Dependency-free OTLP HTTP JSON bridge for deployment smoke tests."""

    def __init__(self, endpoint: str | None = None, timeout: float = 3.0):
        self.endpoint = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")
        self.timeout = timeout

    def export(self, trace: dict[str, Any]) -> None:
        if not self.endpoint:
            return
        trace_id = hashlib.sha256(str(trace.get("trace_id", "")).encode()).hexdigest()[:32]
        span_id = hashlib.sha256(str(trace.get("run_id", "")).encode()).hexdigest()[:16]
        start = _unix_nano(trace.get("started_at"))
        end = _unix_nano(trace.get("finished_at")) or start
        span = {"traceId": trace_id, "spanId": span_id, "name": "agent.run", "kind": 1, "startTimeUnixNano": str(start), "endTimeUnixNano": str(end), "attributes": [{"key": "gen_ai.agent.id", "value": {"stringValue": str(trace.get("run_id", ""))}}, {"key": "agent.workspace.id", "value": {"stringValue": str(trace.get("workspace_id", ""))}}], "status": {"code": 1 if trace.get("status") == "success" else 2}}
        payload = json.dumps({"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "lzcore"}}]}, "scopeSpans": [{"scope": {"name": "lzcore"}, "spans": [span]}]}]}).encode()
        endpoint = self.endpoint if self.endpoint.endswith("/v1/traces") else self.endpoint + "/v1/traces"
        request = urllib.request.Request(endpoint, data=payload, method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout):
            pass


def _unix_nano(value) -> int:
    if not value:
        return 0
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1_000_000_000)
    except ValueError:
        return 0


def export_configured_trace(trace: dict[str, Any]) -> None:
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if endpoint:
        OtlpHttpTraceExporter(endpoint).export(trace)
