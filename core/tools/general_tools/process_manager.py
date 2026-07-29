"""
Process tree management for exec.run — cancel / kill-tree / orphan cleanup.

OpenCode-level subprocess hygiene: when a command times out or is
cancelled, we kill the entire process group (not just the parent)
so no orphaned child processes leak resources.

Cross-platform approach:
    Linux/macOS: os.setsid + os.killpg (process group kill)
    Windows:      taskkill /F /T /PID (native tree kill)

Usage:
    from core.tools.general_tools.process_manager import (
        start_process, kill_process_tree, RUNNING_PROCESSES,
    )
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time

_log = logging.getLogger(__name__)

# ── Running process registry ──────────────────────────────────────────
# Keyed by a user-provided string (e.g. run_id or session_id).
# Each entry: {"process": Popen, "pid": int, "started_at": float}

RUNNING_PROCESSES: dict[str, dict] = {}
_RUNNING_LOCK = threading.Lock()


def _cleanup_entry(key: str) -> None:
    """Remove a process entry from the registry."""
    with _RUNNING_LOCK:
        RUNNING_PROCESSES.pop(key, None)


def start_process(key: str, proc: subprocess.Popen) -> None:
    """Register a running process for cancellation / monitoring."""
    with _RUNNING_LOCK:
        RUNNING_PROCESSES[key] = {
            "process": proc,
            "pid": proc.pid,
            "started_at": time.time(),
        }


def _kill_tree_linux(pid: int) -> None:
    """Kill a process and all its children on Linux/macOS.

    Strategy:
        1. Send SIGTERM to the entire process group (os.killpg)
        2. Wait 2 seconds for graceful shutdown
        3. If still alive, send SIGKILL to the process group
        4. Fallback: pgrep/pkill for any remaining children
    """
    try:
        # Kill the process group — sends signal to the group leader
        # and all members created after setsid()
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        # Process group already gone — try just the PID
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass

    # Wait for processes to terminate
    time.sleep(0.5)

    try:
        os.killpg(pid, 0)  # Check if group still exists
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass

    # Clean up any stragglers via pgrep/pkill
    try:
        subprocess.run(
            ["pkill", "-TERM", "-P", str(pid)],
            timeout=2, capture_output=True,
        )
    except Exception:
        pass


def _kill_tree_windows(pid: int) -> None:
    """Kill a process and all its children on Windows using taskkill."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            timeout=5, capture_output=True,
        )
    except Exception:
        pass


def kill_process_tree(pid: int) -> bool:
    """Kill a process and all its descendants. Returns True on success."""
    if pid is None or pid <= 0:
        return False

    _log.debug("kill_process_tree: pid=%d", pid)

    if os.name == "nt":
        _kill_tree_windows(pid)
    else:
        _kill_tree_linux(pid)

    # Verify the process is gone
    time.sleep(0.3)
    try:
        os.kill(pid, 0)
        # Still alive — last resort
        if os.name == "nt":
            _kill_tree_windows(pid)
        else:
            os.kill(pid, signal.SIGKILL)
        return False
    except (ProcessLookupError, OSError):
        return True


def cancel_process(key: str) -> dict:
    """Cancel a running process by its registry key.

    Returns:
        {"ok": True/False, "pid": int, "message": str}
    """
    with _RUNNING_LOCK:
        entry = RUNNING_PROCESSES.get(key)
        if not entry:
            return {"ok": False, "pid": 0, "message": f"no such process: {key}"}

        proc = entry.get("process")
        pid = entry.get("pid", 0)

    killed = False
    if proc and proc.poll() is None:
        killed = kill_process_tree(pid)

    _cleanup_entry(key)

    if killed:
        return {"ok": True, "pid": pid, "message": f"process {pid} terminated"}
    else:
        return {"ok": True, "pid": pid,
                "message": f"process {pid} already exited or failed to kill"}


def cleanup_orphans(key: str) -> None:
    """Clean up a process entry after it has finished (or been killed).

    Called after subprocess.Popen.communicate() or subprocess.run()
    returns, regardless of success/failure/timeout.
    """
    with _RUNNING_LOCK:
        entry = RUNNING_PROCESSES.pop(key, None)

    if not entry:
        return

    pid = entry.get("pid", 0)
    proc = entry.get("process")

    if proc and proc.poll() is None:
        # Process is somehow still running — kill it
        _log.warning("cleanup_orphans: pid=%d still running, killing", pid)
        kill_process_tree(pid)


def list_running() -> list[dict]:
    """List all registered running processes."""
    with _RUNNING_LOCK:
        result = []
        for key, entry in list(RUNNING_PROCESSES.items()):
            proc = entry.get("process")
            pid = entry.get("pid", 0)
            alive = proc.poll() is None if proc else False
            result.append({
                "key": key,
                "pid": pid,
                "alive": alive,
                "started_at": entry.get("started_at", 0),
            })
        return result
