"""Workspace-scoped broadcast events for managed file projections."""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from contextlib import contextmanager


_lock = threading.RLock()
_subscribers: dict[str, dict[str, queue.Queue]] = {}


def publish(workspace_id: str, domain: str, action: str, entity_id: str = "") -> None:
    payload = json.dumps({
        "domain": domain,
        "action": action,
        "workspace_id": workspace_id,
        "entity_id": entity_id,
        "ts": time.time(),
    }, ensure_ascii=False)
    with _lock:
        subscribers = list((_subscribers.get(workspace_id) or {}).values())
    for subscriber in subscribers:
        try:
            subscriber.put_nowait(payload)
        except queue.Full:
            try:
                subscriber.get_nowait()
                subscriber.put_nowait(payload)
            except (queue.Empty, queue.Full):
                continue
    try:
        from storage.event_bus import get_event_bus, InProcessEventBus
        bus = get_event_bus()
        if not isinstance(bus, InProcessEventBus):
            bus.publish(f"workspace:{workspace_id}", json.loads(payload))
    except Exception:
        pass


@contextmanager
def subscribe(workspace_id: str):
    subscriber_id = uuid.uuid4().hex
    subscriber: queue.Queue = queue.Queue(maxsize=64)
    redis_bus = None
    stop = None
    thread = None
    try:
        from storage.event_bus import RedisEventBus, get_event_bus
        configured_bus = get_event_bus()
        if isinstance(configured_bus, RedisEventBus):
            redis_bus = configured_bus
            raw_queue: queue.Queue = queue.Queue(maxsize=64)
            stop = threading.Event()
            ready = threading.Event()
            thread = threading.Thread(
                target=redis_bus.pump,
                args=(f"workspace:{workspace_id}", raw_queue, stop, ready),
                daemon=True,
            )
            thread.start()
            if not ready.wait(timeout=2.0):
                raise RuntimeError("Redis event subscription did not become ready")

            def _encode_messages():
                while not stop.is_set():
                    try:
                        subscriber.put(json.dumps(raw_queue.get(timeout=0.5), ensure_ascii=False))
                    except queue.Empty:
                        continue

            encoder = threading.Thread(target=_encode_messages, daemon=True)
            encoder.start()
        else:
            with _lock:
                _subscribers.setdefault(workspace_id, {})[subscriber_id] = subscriber
    except Exception:
        with _lock:
            _subscribers.setdefault(workspace_id, {})[subscriber_id] = subscriber
    try:
        yield subscriber
    finally:
        if stop is not None:
            stop.set()
        if thread is not None:
            thread.join(timeout=1.0)
        with _lock:
            listeners = _subscribers.get(workspace_id)
            if listeners is not None:
                listeners.pop(subscriber_id, None)
                if not listeners:
                    _subscribers.pop(workspace_id, None)
