"""Concurrency regression tests for session metadata transactions."""
from __future__ import annotations

import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


def _process_update(root: str, session_id: str, workspace_id: str, field: str, value: str) -> None:
    os.environ["NA_WORKSPACE_ROOT"] = root
    from storage.session_store import update_session

    if field == "title":
        update_session(session_id, workspace_id, title=value)
    elif field == "status":
        update_session(session_id, workspace_id, status=value)
    else:
        update_session(session_id, workspace_id, metadata={field: value})


def _process_ensure(root: str, session_id: str, workspace_id: str) -> None:
    os.environ["NA_WORKSPACE_ROOT"] = root
    from storage.session_store import ensure_session

    ensure_session(session_id, workspace_id, title="parallel")


@pytest.fixture
def isolated_store(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


def test_thread_updates_preserve_independent_fields(isolated_store):
    from storage.session_store import ensure_session, get_session, update_session

    session_id, workspace_id = "sess_thread_lock", "ws_lock"
    ensure_session(session_id, workspace_id)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(update_session, session_id, workspace_id, title="renamed"),
            pool.submit(update_session, session_id, workspace_id, status="archived"),
            pool.submit(update_session, session_id, workspace_id, metadata={"source": "thread"}),
        ]
        for future in futures:
            future.result()
    session = get_session(session_id, workspace_id)
    assert session["title"] == "renamed"
    assert session["status"] == "archived"
    assert session["metadata"]["source"] == "thread"


def test_process_updates_preserve_independent_fields(isolated_store):
    from storage.session_store import ensure_session, get_session

    session_id, workspace_id = "sess_process_lock", "ws_lock"
    ensure_session(session_id, workspace_id)
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_process_update, args=(str(isolated_store), session_id, workspace_id, "title", "process-title")),
        context.Process(target=_process_update, args=(str(isolated_store), session_id, workspace_id, "status", "archived")),
        context.Process(target=_process_update, args=(str(isolated_store), session_id, workspace_id, "process", "true")),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    session = get_session(session_id, workspace_id)
    assert session["title"] == "process-title"
    assert session["status"] == "archived"
    assert session["metadata"]["process"] == "true"


def test_concurrent_ensure_creates_one_valid_record(isolated_store):
    from storage.session_store import get_session

    session_id, workspace_id = "sess_ensure_lock", "ws_lock"
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_process_ensure, args=(str(isolated_store), session_id, workspace_id)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    session = get_session(session_id, workspace_id)
    assert session and session["session_id"] == session_id
    assert len(list((isolated_store / workspace_id / "sessions").glob(f"{session_id}.json"))) == 1


def test_permanent_delete_prevents_concurrent_update_revival(isolated_store):
    from storage.session_store import delete_session_permanently, ensure_session, get_session, update_session

    session_id, workspace_id = "sess_delete_lock", "ws_lock"
    ensure_session(session_id, workspace_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleted = pool.submit(delete_session_permanently, session_id, workspace_id, True)
        updated = pool.submit(update_session, session_id, workspace_id, title="must-not-revive")
        assert deleted.result() is True
        updated.result()
    assert get_session(session_id, workspace_id) is None
    assert not (isolated_store / workspace_id / "sessions" / f"{session_id}.json").exists()
    assert (isolated_store / workspace_id / "sessions" / f".{session_id}.deleted").is_file()
    with pytest.raises(ValueError, match="permanently deleted"):
        ensure_session(session_id, workspace_id)


def test_permanent_delete_reports_partial_failure_and_can_retry(isolated_store, monkeypatch):
    import shutil
    from storage.session_store import delete_session_permanently, ensure_session

    session_id, workspace_id = "sess_delete_retry", "ws_lock"
    ensure_session(session_id, workspace_id)
    message_dir = isolated_store / workspace_id / "sessions" / session_id
    message_dir.mkdir(parents=True)
    (message_dir / "residual.json").write_text("{}", encoding="utf-8")
    real_rmtree = shutil.rmtree
    monkeypatch.setattr(shutil, "rmtree", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("busy")))
    assert delete_session_permanently(session_id, workspace_id, True) is False
    assert message_dir.exists()
    monkeypatch.setattr(shutil, "rmtree", real_rmtree)
    assert delete_session_permanently(session_id, workspace_id, True) is True
    assert not message_dir.exists()
