"""Bounded in-process HTTP metrics with Prometheus rendering."""

from __future__ import annotations

from collections import defaultdict
import threading
import time


_LOCK = threading.Lock()
_STARTED_AT = time.time()
_REQUESTS: dict[tuple[str, str, int], int] = defaultdict(int)
_DURATION: dict[tuple[str, str], tuple[int, float]] = {}
_OPERATIONS: dict[tuple[str, str], int] = defaultdict(int)
_GAUGES: dict[str, float] = {}


def record_operation(operation: str, status: str) -> None:
    """Record a bounded operational event without user/resource labels."""
    with _LOCK:
        _OPERATIONS[(str(operation), str(status))] += 1


def set_operational_gauge(name: str, value: float) -> None:
    with _LOCK:
        _GAUGES[str(name)] = float(value)


def install_http_metrics(app) -> None:
    from flask import Response, g, jsonify, request

    @app.before_request
    def _metrics_start():
        g.metrics_started_at = time.monotonic()

    @app.after_request
    def _metrics_finish(response):
        started = getattr(g, "metrics_started_at", None)
        duration = max(0.0, time.monotonic() - started) if started is not None else 0.0
        route = request.url_rule.rule if request.url_rule is not None else "unmatched"
        method = request.method
        with _LOCK:
            _REQUESTS[(method, route, int(response.status_code))] += 1
            count, total = _DURATION.get((method, route), (0, 0.0))
            _DURATION[(method, route)] = (count + 1, total + duration)
        return response

    @app.route("/api/metrics")
    def api_metrics():
        return jsonify(metrics_snapshot())

    @app.route("/metrics")
    def prometheus_metrics():
        return Response(render_prometheus(), mimetype="text/plain; version=0.0.4")


def metrics_snapshot() -> dict:
    with _LOCK:
        requests = [{"method": method, "route": route, "status": status, "count": count} for (method, route, status), count in sorted(_REQUESTS.items())]
        durations = [{"method": method, "route": route, "count": count, "sum_seconds": round(total, 6)} for (method, route), (count, total) in sorted(_DURATION.items())]
        operations = [{"operation": operation, "status": status, "count": count} for (operation, status), count in sorted(_OPERATIONS.items())]
        gauges = [{"name": name, "value": value} for name, value in sorted(_GAUGES.items())]
    return {"ok": True, "uptime_seconds": round(time.time() - _STARTED_AT, 3), "requests": requests, "durations": durations, "operations": operations, "gauges": gauges}


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_prometheus() -> str:
    snapshot = metrics_snapshot()
    lines = ["# HELP lzcore_uptime_seconds Process uptime.", "# TYPE lzcore_uptime_seconds gauge", f"lzcore_uptime_seconds {snapshot['uptime_seconds']}"]
    lines.extend(["# HELP lzcore_http_requests_total HTTP requests.", "# TYPE lzcore_http_requests_total counter"])
    for item in snapshot["requests"]:
        lines.append(f'lzcore_http_requests_total{{method="{_label(item["method"])}",route="{_label(item["route"])}",status="{item["status"]}"}} {item["count"]}')
    lines.extend(["# HELP lzcore_http_request_duration_seconds Request duration sum.", "# TYPE lzcore_http_request_duration_seconds summary"])
    for item in snapshot["durations"]:
        labels = f'method="{_label(item["method"])}",route="{_label(item["route"])}"'
        lines.append(f"lzcore_http_request_duration_seconds_count{{{labels}}} {item['count']}")
        lines.append(f"lzcore_http_request_duration_seconds_sum{{{labels}}} {item['sum_seconds']}")
    lines.extend(["# HELP lzcore_operations_total Bounded platform operation outcomes.", "# TYPE lzcore_operations_total counter"])
    for item in snapshot["operations"]:
        labels = f'operation="{_label(item["operation"])}",status="{_label(item["status"])}"'
        lines.append(f"lzcore_operations_total{{{labels}}} {item['count']}")
    lines.extend(["# HELP lzcore_operational_gauge Current bounded platform operational state.", "# TYPE lzcore_operational_gauge gauge"])
    for item in snapshot["gauges"]:
        lines.append(f'lzcore_operational_gauge{{name="{_label(item["name"])}"}} {item["value"]}')
    return "\n".join(lines) + "\n"
