# agent/task.py
"""Task state machine — inspired by Codex's Session→Task→Turn model.

A Task represents one user intent with a defined goal.
Its state machine: created → running → completed | failed | cancelled.
"""

from __future__ import annotations

import enum
import time
from typing import Any
from agent.runtime.utils import now_iso


class TaskStatus(enum.Enum):
    """Task lifecycle states."""
    CREATED = "created"        # task created, not yet started
    RUNNING = "running"        # task is executing turns
    COMPLETED = "completed"    # task finished successfully
    FAILED = "failed"          # task failed with an error
    CANCELLED = "cancelled"    # task was interrupted/cancelled


class Task:
    """A single user intent execution context.

    Tasks are ephemeral — they live for the duration of one agent run.
    State transitions are monotonic: CREATED → RUNNING → terminal.

    Usage:
        task = Task(
            intent="analyze_data",
            user_input="summarize this file",
            workspace_id="default",
            session_id="sess-1",
        )
        task.start()
        for turn in task.iter_turns():
            turn.execute()
        task.complete()
    """

    __slots__ = (
        "task_id", "intent", "user_input", "workspace_id", "session_id",
        "status", "created_at", "started_at", "completed_at",
        "turn_count", "error", "metadata",
    )

    def __init__(
        self,
        intent: str = "",
        user_input: str = "",
        workspace_id: str = "",  # v3.10: no default fallback
        session_id: str = "",
    ) -> None:
        self.task_id: str = f"task_{int(time.time_ns())}"
        self.intent: str = intent
        self.user_input: str = user_input
        self.workspace_id: str = workspace_id
        self.session_id: str = session_id
        self.status: TaskStatus = TaskStatus.CREATED
        self.created_at: str = now_iso()
        self.started_at: str | None = None
        self.completed_at: str | None = None
        self.turn_count: int = 0
        self.error: str | None = None
        self.metadata: dict[str, Any] = {}

    def start(self) -> None:
        """Transition CREATED → RUNNING."""
        if self.status != TaskStatus.CREATED:
            raise TaskStateError(
                f"Cannot start task in state {self.status.value}"
            )
        self.status = TaskStatus.RUNNING
        self.started_at = now_iso()

    def complete(self, metadata: dict | None = None) -> None:
        """Transition RUNNING → COMPLETED."""
        if self.status != TaskStatus.RUNNING:
            raise TaskStateError(
                f"Cannot complete task in state {self.status.value}"
            )
        self.status = TaskStatus.COMPLETED
        self.completed_at = now_iso()
        if metadata:
            self.metadata.update(metadata)

    def fail(self, error: str) -> None:
        """Transition RUNNING → FAILED."""
        self.status = TaskStatus.FAILED
        self.completed_at = now_iso()
        self.error = error

    def cancel(self) -> None:
        """Transition to CANCELLED (from any non-terminal state)."""
        if self.is_terminal:
            return
        self.status = TaskStatus.CANCELLED
        self.completed_at = now_iso()

    def record_turn(self) -> int:
        """Increment turn counter. Returns the new turn number."""
        self.turn_count += 1
        return self.turn_count

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        )

    @property
    def is_active(self) -> bool:
        return self.status == TaskStatus.RUNNING

    @property
    def elapsed_ms(self) -> float:
        """Elapsed time in milliseconds since task start (or creation if not started).

        v3.9.10: created_at / started_at / completed_at are now ISO-8601
        strings (UTC). Use ``from_iso`` to parse.
        """
        from agent.runtime.utils import from_iso
        t0_str = self.started_at or self.created_at
        t1_str = self.completed_at
        if t1_str is None:
            t1_epoch = time.time()
            t0_epoch = from_iso(t0_str) if isinstance(t0_str, str) else float(t0_str)
        else:
            t0_epoch = from_iso(t0_str) if isinstance(t0_str, str) else float(t0_str)
            t1_epoch = from_iso(t1_str)
        return (t1_epoch - t0_epoch) * 1000

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "intent": self.intent,
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "status": self.status.value,
            "turn_count": self.turn_count,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "error": self.error,
            "metadata": self.metadata,
        }


class TaskStateError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass
