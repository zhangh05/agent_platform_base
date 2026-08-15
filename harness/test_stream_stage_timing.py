from __future__ import annotations


class _CaptureEmitter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def emit(self, name: str, payload: dict) -> None:
        self.calls.append((name, payload))


def test_engine_stage_payload_has_turn_and_stage_elapsed(monkeypatch):
    from core.runtime_engine import engine as engine_module
    from core.runtime_engine.engine import SSOTRuntimeEngine

    emitter = _CaptureEmitter()
    runtime = object.__new__(SSOTRuntimeEngine)
    runtime._emitter = emitter
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: 12.75)

    runtime._emit_stage("planner_started", 10.0, stage_elapsed_ms=320)

    assert emitter.calls == [
        ("planner_started", {
            "stage": "planner_started",
            "elapsed_ms": 2750,
            "turn_elapsed_ms": 2750,
            "stage_elapsed_ms": 320,
        }),
    ]


def test_query_loop_stage_payload_measures_current_stage(monkeypatch):
    from core.runtime_engine import query_loop as query_loop_module
    from core.runtime_engine.query_loop import QueryLoop

    emitter = _CaptureEmitter()
    loop = object.__new__(QueryLoop)
    loop._emitter = emitter
    monkeypatch.setattr(query_loop_module.time, "monotonic", lambda: 12.75)

    loop._emit_stage(
        "model_completed",
        10.0,
        stage_started_at=11.5,
        iteration=2,
    )

    assert emitter.calls == [
        ("model_completed", {
            "stage": "model_completed",
            "elapsed_ms": 2750,
            "turn_elapsed_ms": 2750,
            "stage_elapsed_ms": 1250,
            "iteration": 2,
        }),
    ]
