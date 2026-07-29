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


@contextmanager
def subscribe(workspace_id: str):
    subscriber_id = uuid.uuid4().hex
    subscriber: queue.Queue = queue.Queue(maxsize=64)
    with _lock:
        _subscribers.setdefault(workspace_id, {})[subscriber_id] = subscriber
    try:
        yield subscriber
    finally:
        with _lock:
            listeners = _subscribers.get(workspace_id)
            if listeners is not None:
                listeners.pop(subscriber_id, None)
                if not listeners:
                    _subscribers.pop(workspace_id, None)
