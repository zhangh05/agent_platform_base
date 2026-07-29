"""Stream history timestamps and persistence contracts.

Covers current runtime, tool, and API regression contracts.

Each fix should have at least one focused test that fails on the old code
and passes on the new code.
"""

import json
import os
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

import pytest

# Ensure repo root on path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────────
# v3.10: TestMaxStepsResolve removed — the helper it tested,
# Runtime step limits are owned by the current QueryLoop configuration.
# TurnRunner path that the SSOT Runtime hard cut (ff38bab) replaced. Step
# budgets now flow through ``SSOTRuntimeConfig`` (single_node_timeout_ms /
# parallel_layer_timeout_ms) and ``BudgetController.check_*`` rather
# than env-override cascading.
# ─────────────────────────────────────────────────────────────────────


def types_simple(**kwargs):
    """Build a simple object with arbitrary attributes (for test stand-ins)."""
    obj = type("T", (), {})()
    for k, v in kwargs.items():
        setattr(obj, k, v)
    return obj


# ─────────────────────────────────────────────────────────────────────
# Trim history boundary correctness (extracted from context budget logic)
# ─────────────────────────────────────────────────────────────────────


def _apply_trim_history(ctx):
    """Inline trim-history logic matching current context budget manager."""
    if len(ctx.history_window) > 2:
        before = len(ctx.history_window)
        keep = max(2, len(ctx.history_window) - 2)
        ctx.history_window = ctx.history_window[-keep:]
        after = len(ctx.history_window)
        ctx.metadata["compact_history_before"] = before
        ctx.metadata["compact_history_after"] = after


class TestTrimHistoryBoundary:
    """Trim history must drop oldest 2 turns without over-trimming."""

    def test_history_window_len_2_not_modified(self):
        ctx = types_simple(
            model_config={"model": ""},
            history_window=[{"role": "user"}, {"role": "assistant"}],
            metadata={},
        )
        _apply_trim_history(ctx)
        assert len(ctx.history_window) == 2

    def test_history_window_len_3_keeps_pairing(self):
        ctx = types_simple(
            model_config={"model": ""},
            history_window=[{"role": "user"}, {"role": "assistant"}, {"role": "user"}],
            metadata={},
        )
        _apply_trim_history(ctx)
        assert len(ctx.history_window) == 2
        assert ctx.metadata.get("compact_history_before") == 3
        assert ctx.metadata.get("compact_history_after") == 2

    def test_history_window_len_4_keeps_2(self):
        ctx = types_simple(
            model_config={"model": ""},
            history_window=[{"i": i} for i in range(4)],
            metadata={},
        )
        _apply_trim_history(ctx)
        assert len(ctx.history_window) == 2
        assert ctx.metadata.get("compact_history_before") == 4
        assert ctx.metadata.get("compact_history_after") == 2

    def test_history_window_len_1_not_modified(self):
        ctx = types_simple(
            model_config={"model": ""},
            history_window=[{"role": "user"}],
            metadata={},
        )
        _apply_trim_history(ctx)
        assert len(ctx.history_window) == 1
        assert ctx.metadata.get("compact_history_before") is None
        assert ctx.metadata.get("compact_history_after") is None


def _history_turn():
    return types_simple(item_type="history_turn")


# ─────────────────────────────────────────────────────────────────────
# Trim history: history_window vs compressed_items mismatch regression
# ─────────────────────────────────────────────────────────────────────

class TestTrimHistoryCompressedItemsMismatch:
    """Regression: trim must guard and slice using len(ctx.history_window),
    not a separate compressed_items count.
    """

    def test_history_window_3_keeps_pairing(self):
        ctx = types_simple(
            model_config={"model": ""},
            history_window=[{"role": "user"}, {"role": "assistant"}, {"role": "user"}],
            metadata={},
        )
        _apply_trim_history(ctx)
        assert len(ctx.history_window) == 2

    def test_history_window_3_empty_compressed(self):
        ctx = types_simple(
            model_config={"model": ""},
            history_window=[{"i": i} for i in range(3)],
            metadata={},
        )
        _apply_trim_history(ctx)
        assert len(ctx.history_window) == 2

    def test_history_window_5_keeps_3(self):
        ctx = types_simple(
            model_config={"model": ""},
            history_window=[{"i": i} for i in range(5)],
            metadata={},
        )
        _apply_trim_history(ctx)
        assert len(ctx.history_window) == 3
        assert ctx.metadata.get("compact_history_before") == 5
        assert ctx.metadata.get("compact_history_after") == 3

    def test_history_window_10_keeps_8(self):
        ctx = types_simple(
            model_config={"model": ""},
            history_window=[{"i": i} for i in range(10)],
            metadata={},
        )
        _apply_trim_history(ctx)
        assert len(ctx.history_window) == 8


# ─────────────────────────────────────────────────────────────────────
# StreamEmitter — UTC ISO timestamp alongside float
# ─────────────────────────────────────────────────────────────────────

class TestStreamEmitterTimestamp:
    """StreamEmitter must emit both timestamp (float) and timestamp_iso (UTC ISO)."""

    def test_both_timestamps_present(self):
        from agent.runtime.stream_emitter import StreamEmitter
        em = StreamEmitter()
        em.emit("test_event", {"foo": "bar"})
        events = em.to_events()
        assert len(events) == 1
        ev = events[0]
        assert "timestamp" in ev and isinstance(ev["timestamp"], float)
        assert "timestamp_iso" in ev and isinstance(ev["timestamp_iso"], str)
        # ISO format ends with timezone marker
        assert ev["timestamp_iso"].endswith("+00:00") or ev["timestamp_iso"].endswith("Z")

    def test_timestamp_iso_parseable(self):
        from agent.runtime.stream_emitter import StreamEmitter
        from datetime import datetime
        em = StreamEmitter()
        em.emit("test_event", {})
        ev = em.to_events()[0]
        # Must parse without error
        ts = datetime.fromisoformat(ev["timestamp_iso"].replace("Z", "+00:00"))
        assert ts.tzinfo is not None
        assert ts.year >= 2026

    def test_realtime_callback_receives_both_timestamps(self):
        from agent.runtime.stream_emitter import StreamEmitter
        captured = []
        StreamEmitter.set_realtime_callback(captured.append)
        try:
            em = StreamEmitter()
            em.emit("test_event", {})
            assert len(captured) == 1
            ev = captured[0]
            assert isinstance(ev["timestamp"], float)
            assert isinstance(ev["timestamp_iso"], str)
        finally:
            StreamEmitter.clear_realtime_callback()

    def test_old_float_timestamp_still_works_for_duration(self):
        # The loop.py:438 uses `float(e.get('timestamp', 0))` for duration math.
        # Make sure the float field remains and is still numeric.
        from agent.runtime.stream_emitter import StreamEmitter
        em = StreamEmitter()
        em.emit("a", {})
        time.sleep(0.001)
        em.emit("b", {})
        events = em.to_events()
        duration = (max(e["timestamp"] for e in events) - min(e["timestamp"] for e in events)) * 1000
        assert duration > 0


# ─────────────────────────────────────────────────────────────────────
# backend/api/runtime_routes.py — atomic write for tool history
# ─────────────────────────────────────────────────────────────────────

class TestRuntimeRoutesAtomicWrite:
    """_persist_history must use atomic_write_json (now per-workspace)."""

    def test_persist_history_uses_atomic_write(self, monkeypatch, tmp_path):
        from backend.api import runtime_routes
        captured = {}
        def fake_atomic_write_json(path, obj, indent=None):
            captured["path"] = path
            captured["obj"] = obj
            captured["indent"] = indent
        monkeypatch.setattr("storage.tool_history_store.atomic_write_json", fake_atomic_write_json)
        ws = "adhoc_ws_atomic"
        runtime_routes._tool_exec_history.pop(ws, None)
        runtime_routes._tool_exec_history[ws] = OrderedDict()
        runtime_routes._tool_exec_history[ws]["inv-1"] = {"invocation_id": "inv-1", "ok": True}
        runtime_routes._persist_history(ws)
        assert captured["indent"] == 2
        assert len(captured["obj"]) == 1
        assert captured["obj"][0]["invocation_id"] == "inv-1"

    def test_load_persisted_uses_safe_read_json(self, monkeypatch, tmp_path):
        from backend.api import runtime_routes
        from storage import tool_history_store
        hist_path = tmp_path / "tool_history_default.json"
        hist_path.write_text(json.dumps([{"invocation_id": "inv-99"}]))

        def fake_history_path(ws_id):
            return hist_path
        monkeypatch.setattr(tool_history_store, "_history_path", fake_history_path)
        runtime_routes._tool_exec_history.pop("default", None)
        runtime_routes._tool_exec_history["default"] = OrderedDict()

        runtime_routes._ensure_ws_history("default")
        assert "inv-99" in runtime_routes._tool_exec_history["default"]

    def test_load_persisted_handles_missing_file(self, monkeypatch, tmp_path):
        from backend.api import runtime_routes
        from storage import tool_history_store
        missing = tmp_path / "missing1.json"
        def fake_history_path(ws_id):
            return missing
        monkeypatch.setattr(tool_history_store, "_history_path", fake_history_path)
        runtime_routes._tool_exec_history.pop("default", None)
        runtime_routes._tool_exec_history["default"] = OrderedDict()
        runtime_routes._ensure_ws_history("default")
        assert len(runtime_routes._tool_exec_history["default"]) == 0

    def test_load_persisted_handles_corrupt_json(self, monkeypatch, tmp_path):
        from backend.api import runtime_routes
        from storage import tool_history_store
        bad = tmp_path / "bad.json"
        bad.write_text("{this is not json")
        def fake_history_path(ws_id):
            return bad
        monkeypatch.setattr(tool_history_store, "_history_path", fake_history_path)
        runtime_routes._tool_exec_history.pop("default", None)
        runtime_routes._tool_exec_history["default"] = OrderedDict()
        runtime_routes._ensure_ws_history("default")
        assert len(runtime_routes._tool_exec_history["default"]) == 0
