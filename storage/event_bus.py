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


class RedisEventBus:
    def __init__(self, url: str):
        import redis
        self.client = redis.Redis.from_url(url, decode_responses=True)

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        import json
        self.client.publish(topic, json.dumps(payload, ensure_ascii=False, default=str))

    def subscribe(self, topic: str) -> Iterator[dict[str, Any]]:
        import json
        pubsub = self.client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(topic)
        try:
            for message in pubsub.listen():
                if message.get("type") == "message":
                    yield json.loads(message.get("data") or "{}")
        finally:
            pubsub.close()

    def pump(
        self,
        topic: str,
        target: queue.Queue[dict[str, Any]],
        stop: threading.Event,
        ready: threading.Event | None = None,
    ) -> None:
        """Forward Redis messages into a bounded local queue until stopped."""
        import json
        pubsub = self.client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(topic)
        if ready is not None:
            ready.set()
        try:
            while not stop.is_set():
                message = pubsub.get_message(timeout=0.5)
                if not message or message.get("type") != "message":
                    continue
                try:
                    payload = json.loads(message.get("data") or "{}")
                    target.put_nowait(payload)
                except (ValueError, queue.Full):
                    continue
        finally:
            pubsub.close()


def get_event_bus():
    import os
    mode = os.environ.get("LZCORE_EVENT_BUS_MODE", "inprocess").strip().lower()
    if mode == "redis":
        url = os.environ.get("LZCORE_EVENT_BUS_URL") or os.environ.get("LZCORE_QUEUE_URL", "")
        if not url:
            raise RuntimeError("Redis event bus URL is required")
        return RedisEventBus(url)
    return _DEFAULT_BUS
