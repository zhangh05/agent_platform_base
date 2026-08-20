"""Real PostgreSQL, Redis and S3-compatible adapter checks for CI services."""

from __future__ import annotations

import os
import queue
import threading
import uuid

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("LZCORE_RUN_DISTRIBUTED_INTEGRATION") != "1",
    reason="distributed service integration is opt-in",
)


def test_postgres_records_are_visible_across_adapter_instances():
    from storage.backend import PostgresRecordBackend

    dsn = os.environ["LZCORE_DATABASE_URL"]
    first = PostgresRecordBackend(dsn)
    second = PostgresRecordBackend(dsn)
    key = f"integration/{uuid.uuid4().hex}"
    first.write(key, {"owner": "first", "revision": 1})
    assert second.read(key) == {"owner": "first", "revision": 1}
    assert second.delete(key) is True
    assert first.read(key) is None


def test_redis_lease_reclaim_survives_new_queue_instance():
    from jobs.queue import RedisJobQueue

    url = os.environ["LZCORE_QUEUE_URL"]
    first = RedisJobQueue(url)
    second = RedisJobQueue(url)
    first.client.delete(first.QUEUED, first.PROCESSING, first.LEASES)
    first.enqueue("integration", "job_12345678")
    receipt = first.claim("worker-a")
    assert receipt is not None
    lease = first.client.hget(first.LEASES, receipt.lease_id)
    assert lease
    import json
    payload = json.loads(lease)
    payload["heartbeat_at"] = 0
    first.client.hset(first.LEASES, receipt.lease_id, json.dumps(payload))
    assert second.reclaim_stale(30) == 1
    retried = second.claim("worker-b")
    assert retried is not None and retried.attempt == 2
    second.ack(retried)


def test_redis_event_bus_cross_instance_delivery():
    from storage.event_bus import RedisEventBus

    url = os.environ["LZCORE_EVENT_BUS_URL"]
    first = RedisEventBus(url)
    second = RedisEventBus(url)
    topic = f"integration:{uuid.uuid4().hex}"
    received: queue.Queue = queue.Queue(maxsize=1)
    stop = threading.Event()
    ready = threading.Event()
    thread = threading.Thread(target=second.pump, args=(topic, received, stop, ready), daemon=True)
    thread.start()
    assert ready.wait(timeout=2)
    try:
        first.publish(topic, {"source": "first", "ok": True})
        assert received.get(timeout=3) == {"source": "first", "ok": True}
    finally:
        stop.set()
        thread.join(timeout=2)


def test_redis_worker_states_are_kept_per_worker(monkeypatch):
    import redis
    from jobs.worker import _write_state, get_worker_state

    client = redis.Redis.from_url(os.environ["LZCORE_QUEUE_URL"], decode_responses=True)
    for key in client.scan_iter(match="lzcore:worker_state:*"):
        client.delete(key)
    monkeypatch.setenv("LZCORE_QUEUE_MODE", "redis")
    _write_state({"status": "idle", "worker_id": "integration-worker-a"})
    _write_state({"status": "running", "worker_id": "integration-worker-b"})
    state = get_worker_state()
    assert state["worker_count"] == 2
    assert {item["worker_id"] for item in state["workers"]} == {
        "integration-worker-a", "integration-worker-b",
    }


def test_s3_object_round_trip_through_two_clients():
    import boto3
    from botocore.config import Config
    from storage.object_store import S3ObjectStore

    endpoint = os.environ["LZCORE_S3_ENDPOINT_URL"]
    bucket = os.environ["LZCORE_OBJECT_STORE_BUCKET"]
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        config=Config(s3={"addressing_style": "path"}),
    )
    try:
        client.create_bucket(Bucket=bucket)
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    key = f"integration/{uuid.uuid4().hex}.txt"
    first = S3ObjectStore(bucket, "ci")
    second = S3ObjectStore(bucket, "ci")
    first.put(key, b"durable", "text/plain")
    assert second.get(key) == b"durable"
    assert second.delete(key) is True
