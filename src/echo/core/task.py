"""Tasks: lifecycle records for units of Entity work."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvalidTaskTransition(RuntimeError):
    pass


@dataclass(slots=True, kw_only=True)
class Task:
    name: str
    owner: str
    priority: int = 0
    context: dict[str, Any] = field(default_factory=dict)
    parent: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=_utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    children: list[str] = field(default_factory=list)
    result: Any = None
    error: str | None = None

    def start(self) -> None:
        self._require(TaskStatus.PENDING)
        self.status = TaskStatus.RUNNING
        self.started_at = _utc_now()

    def pause(self) -> None:
        self._require(TaskStatus.RUNNING)
        self.status = TaskStatus.PAUSED

    def block(self) -> None:
        self._require(TaskStatus.RUNNING)
        self.status = TaskStatus.BLOCKED

    def resume(self) -> None:
        self._require(TaskStatus.PAUSED, TaskStatus.BLOCKED)
        self.status = TaskStatus.RUNNING

    def complete(self, result: Any = None) -> None:
        self._require(TaskStatus.RUNNING)
        self.status = TaskStatus.COMPLETED
        self.result = result
        self.completed_at = _utc_now()

    def fail(self, error: BaseException | str) -> None:
        self._require(TaskStatus.RUNNING)
        self.status = TaskStatus.FAILED
        self.error = str(error)
        self.completed_at = _utc_now()

    def cancel(self) -> None:
        self._require(
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.PAUSED,
            TaskStatus.BLOCKED,
        )
        self.status = TaskStatus.CANCELLED
        self.completed_at = _utc_now()

    def add_child(self, child: Task) -> None:
        if child.id not in self.children:
            self.children.append(child.id)
        child.parent = self.id

    def _require(self, *allowed: TaskStatus) -> None:
        if self.status not in allowed:
            expected = ", ".join(status.value for status in allowed)
            raise InvalidTaskTransition(
                f"cannot transition task {self.id} from {self.status.value}; "
                f"expected {expected}"
            )

