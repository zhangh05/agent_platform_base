"""Cross-process event bus boundary with an in-process development adapter."""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from typing import Any


class InProcessEventBus:
    def __init__(self):
        self._lock = threading.RLock()
        self._subscribers: dict[str, list[queue.Queue[dict[str, Any]]]] = {}

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(topic, ()))
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(dict(payload))
            except queue.Full:
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(dict(payload))
                except (queue.Empty, queue.Full):
                    pass

    def subscribe(self, topic: str) -> Iterator[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.setdefault(topic, []).append(subscriber)
        try:
            while True:
                yield subscriber.get()
        finally:
            with self._lock:
                listeners = self._subscribers.get(topic, [])
                if subscriber in listeners:
                    listeners.remove(subscriber)
                if not listeners:
                    self._subscribers.pop(topic, None)


_DEFAULT_BUS = InProcessEventBus()


def get_event_bus() -> InProcessEventBus:
    return _DEFAULT_BUS
