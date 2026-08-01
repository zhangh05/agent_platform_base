"""Optional trace exporters; local trace persistence remains canonical."""

from __future__ import annotations

import json
import os
import urllib.request
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
        payload = json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": [trace]}]}]}).encode()
        request = urllib.request.Request(self.endpoint, data=payload, method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout):
            pass
