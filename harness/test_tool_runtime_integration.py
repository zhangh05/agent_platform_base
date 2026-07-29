"""
P2c fix coverage: ``core.tools.integration.get_default_tool_runtime_client``
must be a thread-safe singleton with double-checked locking.

Background (5-layer audit, v3.10):

  The previous implementation read ``_default_client`` outside any
  lock and only took the lock during construction. Two threads that
  both observed ``_default_client is None`` would each build a
  full registry + general-tools + client. This produced:

    * duplicated constructor work,
    * each thread ended up with its own ``ToolRuntimeClient``,
    * downstream code that mutated the singleton (e.g. register a
      runtime-only tool) would silently see inconsistent state.

  The new contract:

    * ``get_default_tool_runtime_client()`` uses a single
      ``threading.Lock`` covering the read+create+assign critical
      section, with a re-check inside the lock (the classic
      double-checked locking pattern).
    * A ``get_default_client_build_count()`` counter exposes how
      many times canonical registry construction has actually run, so
      tests can assert the singleton is built at most once even
      under contention.
    * A ``reset_default_client_for_tests()`` helper drops the
      cached singleton (and zeros the counter) so tests can start
      from a clean state.

These tests cover single-thread (sanity), multi-thread race
(critical), and the reset helper.
"""

from __future__ import annotations

import threading

import pytest

from core.tools.integration import (
    get_default_client_build_count,
    get_default_tool_runtime_client,
    reset_default_client_for_tests,
)


@pytest.fixture(autouse=True)
def _clean_singleton():
    """Each test starts with a freshly-reset singleton. Without
    this, tests run after each other in the same process and the
    first test's build would leak into the next."""
    reset_default_client_for_tests()
    yield
    reset_default_client_for_tests()


# ── A: single-thread sanity — same object on repeat calls ─────────────


def test_single_thread_returns_same_singleton():
    """Sanity: two consecutive calls must return the SAME object
    (identity, not equality)."""
    a = get_default_tool_runtime_client()
    b = get_default_tool_runtime_client()
    assert a is b
    assert get_default_client_build_count() == 1


# ── B: concurrent callers do not duplicate the build ─────────────────


def test_concurrent_callers_do_not_duplicate_build():
    """Critical: 32 threads racing on first call must result in
    exactly one build. The pre-v3.10 code would typically trigger
    2-5 duplicate builds (depending on scheduler luck)."""
    N_THREADS = 32
    barrier = threading.Barrier(N_THREADS)
    results: list = [None] * N_THREADS

    def worker(idx: int) -> None:
        # All threads wait at the barrier, then race to call.
        # Maximizes the chance of catching a missing lock.
        barrier.wait()
        results[idx] = get_default_tool_runtime_client()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All threads got the same object.
    first = results[0]
    assert first is not None
    for r in results[1:]:
        assert r is first, (
            f"concurrent callers got different objects — locking is broken. "
            f"results[0]={id(first)} results[1]={id(r)}"
        )

    # The build counter must be exactly 1 (NOT N_THREADS).
    assert get_default_client_build_count() == 1, (
        f"expected exactly 1 build, got {get_default_client_build_count()} — "
        f"the lock is not covering the read+create+assign critical section."
    )


# ── C: reset helper drops the cache and zeros the counter ────────────


def test_reset_helper_drops_singleton_and_zeros_counter():
    """After ``reset_default_client_for_tests()``, the next call
    must rebuild (counter → 1) and may return a different object
    identity than the pre-reset singleton.
    """
    first = get_default_tool_runtime_client()
    assert get_default_client_build_count() == 1

    reset_default_client_for_tests()
    assert get_default_client_build_count() == 0

    second = get_default_tool_runtime_client()
    assert get_default_client_build_count() == 1
    # Identity may differ (a fresh client was constructed).
    assert first is not second
    # But the type and basic contract are the same.
    assert isinstance(first, type(second))


# ── D: reset is itself thread-safe ────────────────────────────────────


def test_reset_under_contention_is_safe():
    """Reset and concurrent callers must not deadlock. Several
    threads call ``get_default_tool_runtime_client()`` while the
    main thread alternates reset/get. We only assert that all
    calls return and the counter stays non-negative.
    """
    N_WORKERS = 8
    stop = threading.Event()

    def worker() -> None:
        while not stop.is_set():
            get_default_tool_runtime_client()

    workers = [threading.Thread(target=worker) for _ in range(N_WORKERS)]
    for w in workers:
        w.start()

    # Alternate reset/get on the main thread for a brief period.
    for _ in range(20):
        reset_default_client_for_tests()
        get_default_tool_runtime_client()

    stop.set()
    for w in workers:
        w.join(timeout=2.0)
        assert not w.is_alive(), "worker thread did not exit cleanly"

    # Counter is non-negative (sanity — a lock would prevent it
    # from going negative even under contention).
    assert get_default_client_build_count() >= 1
