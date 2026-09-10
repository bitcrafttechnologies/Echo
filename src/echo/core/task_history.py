"""Bounded, reference-backed Task lifecycle history."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from echo.core.task import Task, TaskStatus


def _copy_value(value: Any) -> Any:
    try:
        return deepcopy(value)
    except Exception:
        return value


@dataclass(slots=True, kw_only=True, frozen=True)
class TaskHistoryEntry:
    """Read snapshot of a Task's current lifecycle state."""

    id: str
    name: str
    owner: str
    status: TaskStatus
    priority: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    parent: str | None
    children: tuple[str, ...] = ()
    result: Any = None
    error: str | None = None
    signal_id: str | None = None

    @classmethod
    def from_task(cls, task: Task) -> TaskHistoryEntry:
        signal = task.context.get("signal")
        signal_id = signal.get("id") if isinstance(signal, dict) else None
        return cls(
            id=task.id,
            name=task.name,
            owner=task.owner,
            status=task.status,
            priority=task.priority,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
            parent=task.parent,
            children=tuple(task.children),
            result=_copy_value(task.result),
            error=task.error,
            signal_id=signal_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "owner": self.owner,
            "status": self.status.value,
            "priority": self.priority,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "parent": self.parent,
            "children": list(self.children),
            "result": _copy_value(self.result),
            "error": self.error,
            "signal_id": self.signal_id,
        }


class TaskHistory:
    """Bounded Task references; queries materialize safe read snapshots."""

    def __init__(self, max_size: int = 1000) -> None:
        if isinstance(max_size, bool) or not isinstance(max_size, int) or max_size <= 0:
            raise ValueError("task history max_size must be a positive integer")
        self.max_size = max_size
        self._tasks: OrderedDict[str, Task] = OrderedDict()

    def __len__(self) -> int:
        return len(self._tasks)

    def resize(self, max_size: int) -> None:
        """Change retention in place, evicting oldest Tasks if reduced."""

        if isinstance(max_size, bool) or not isinstance(max_size, int) or max_size <= 0:
            raise ValueError("task history max_size must be a positive integer")
        self.max_size = max_size
        while len(self._tasks) > self.max_size:
            self._tasks.popitem(last=False)

    def record(self, task: Task) -> None:
        self._tasks.pop(task.id, None)
        self._tasks[task.id] = task
        while len(self._tasks) > self.max_size:
            self._tasks.popitem(last=False)

    def latest(self, limit: int | None = None) -> tuple[TaskHistoryEntry, ...]:
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
                raise ValueError("task history limit must be a non-negative integer")
            if limit == 0:
                return ()
        tasks = list(reversed(self._tasks.values()))
        if limit is not None:
            tasks = tasks[:limit]
        return tuple(TaskHistoryEntry.from_task(task) for task in tasks)

    def get(self, task_id: str) -> TaskHistoryEntry | None:
        task = self._tasks.get(task_id)
        return TaskHistoryEntry.from_task(task) if task is not None else None

    def filter(
        self,
        *,
        status: TaskStatus | str | None = None,
    ) -> tuple[TaskHistoryEntry, ...]:
        if isinstance(status, str):
            status = TaskStatus(status)
        return tuple(
            TaskHistoryEntry.from_task(task)
            for task in reversed(self._tasks.values())
            if status is None or task.status is status
        )
