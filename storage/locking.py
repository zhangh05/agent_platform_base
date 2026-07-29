"""Cross-platform, fail-closed advisory file locks for storage adapters."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path


class FileLock:
    """Exclusive inter-process lock backed by ``flock`` or ``msvcrt``.

    Lock acquisition always fails closed: a timeout raises ``TimeoutError`` and
    callers must not enter the protected section.  A per-path thread lock also
    serializes threads because POSIX file locks are process-scoped.
    """

    _thread_locks: dict[str, threading.RLock] = {}
    _thread_locks_guard = threading.Lock()
    _thread_state = threading.local()

    def __init__(self, path: Path, *, timeout: float = 5.0, retry_interval: float = 0.05):
        self.path = Path(path)
        self.timeout = max(0.0, float(timeout))
        self.retry_interval = max(0.001, float(retry_interval))
        self._thread_lock: threading.RLock | None = None
        self._fd: int | None = None
        self._backend = ""
        self._key = ""
        self._reentrant = False

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = str(self.path.resolve())
        self._key = key
        with self._thread_locks_guard:
            self._thread_lock = self._thread_locks.setdefault(key, threading.RLock())
        if not self._thread_lock.acquire(timeout=self.timeout):
            raise TimeoutError(f"storage thread lock timeout: {self.path}")
        held = getattr(self._thread_state, "held", None)
        if held is None:
            held = {}
            self._thread_state.held = held
        if held.get(key, 0) > 0:
            held[key] += 1
            self._reentrant = True
            return self
        try:
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
            self._acquire_process_lock()
            held[key] = 1
            return self
        except Exception:
            self._close_fd()
            self._thread_lock.release()
            self._thread_lock = None
            raise

    def _acquire_process_lock(self) -> None:
        assert self._fd is not None
        deadline = time.monotonic() + self.timeout
        if os.name == "nt":
            import msvcrt

            if os.fstat(self._fd).st_size == 0:
                os.write(self._fd, b"0")
                os.fsync(self._fd)
            while True:
                try:
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                    self._backend = "msvcrt"
                    return
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"storage file lock timeout: {self.path}") from exc
                    time.sleep(self.retry_interval)

        import fcntl

        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._backend = "fcntl"
                return
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"storage file lock timeout: {self.path}") from exc
                time.sleep(self.retry_interval)

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        held = getattr(self._thread_state, "held", {})
        if self._reentrant:
            held[self._key] -= 1
            self._thread_lock.release()
            self._thread_lock = None
            self._reentrant = False
            return False
        try:
            self._release_process_lock()
        finally:
            held.pop(self._key, None)
            self._close_fd()
            if self._thread_lock is not None:
                self._thread_lock.release()
                self._thread_lock = None
        return False

    def _release_process_lock(self) -> None:
        if self._fd is None:
            return
        try:
            if self._backend == "msvcrt":
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            elif self._backend == "fcntl":
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass

    def _close_fd(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._backend = ""
