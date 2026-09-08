"""Bounded, in-memory Action lifecycle history."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from echo.core.action import Action


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _copy_value(value: Any) -> Any:
    try:
        return deepcopy(value)
    except Exception:
        return value


class ActionStatus(StrEnum):
    CREATED = "created"
    EXECUTED = "executed"
    FAILED = "failed"


@dataclass(slots=True, kw_only=True)
class _ActionRecord:
    action: Action
    signal_id: str | None
    status: ActionStatus = ActionStatus.CREATED
    execution_time: datetime | None = None
    result: Any = None
    error: str | None = None


@dataclass(slots=True, kw_only=True, frozen=True)
class ActionHistoryEntry:
    """Read snapshot of an Action's lifecycle and associations."""

    id: str
    type: str
    created_at: datetime
    execution_time: datetime | None
    status: ActionStatus
    task_id: str | None
    signal_id: str | None
    entity_id: str | None
    parameters: dict[str, Any]
    result: Any = None
    error: str | None = None

    @classmethod
    def from_record(cls, record: _ActionRecord) -> ActionHistoryEntry:
        action = record.action
        return cls(
            id=action.id,
            type=action.type,
            created_at=action.created_at,
            execution_time=record.execution_time,
            status=record.status,
            task_id=action.task_id,
            signal_id=record.signal_id,
            entity_id=action.entity_id,
            parameters=_copy_value(action.parameters),
            result=_copy_value(record.result),
            error=record.error,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "created_at": self.created_at.isoformat(),
            "execution_time": (
                self.execution_time.isoformat() if self.execution_time else None
            ),
            "status": self.status.value,
            "task_id": self.task_id,
            "signal_id": self.signal_id,
            "entity_id": self.entity_id,
            "parameters": _copy_value(self.parameters),
            "result": _copy_value(self.result),
            "error": self.error,
        }


class ActionHistory:
    """Bounded Action references with minimal lifecycle annotations."""

    def __init__(self, max_size: int = 1000) -> None:
        if isinstance(max_size, bool) or not isinstance(max_size, int) or max_size <= 0:
            raise ValueError("action history max_size must be a positive integer")
        self.max_size = max_size
        self._records: OrderedDict[str, _ActionRecord] = OrderedDict()

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, action_id: str) -> bool:
        return action_id in self._records

    def record(self, action: Action, *, signal_id: str | None = None) -> None:
        self._records.pop(action.id, None)
        self._records[action.id] = _ActionRecord(action=action, signal_id=signal_id)
        while len(self._records) > self.max_size:
            self._records.popitem(last=False)

    def mark_executed(
        self,
        action_id: str,
        *,
        result: Any = None,
        execution_time: datetime | None = None,
    ) -> ActionHistoryEntry | None:
        record = self._records.get(action_id)
        if record is None:
            return None
        record.status = ActionStatus.EXECUTED
        record.execution_time = execution_time or _utc_now()
        record.result = result
        record.error = None
        return ActionHistoryEntry.from_record(record)

    def mark_failed(
        self,
        action_id: str,
        error: BaseException | str,
        *,
        execution_time: datetime | None = None,
    ) -> ActionHistoryEntry | None:
        record = self._records.get(action_id)
        if record is None:
            return None
        record.status = ActionStatus.FAILED
        record.execution_time = execution_time or _utc_now()
        record.result = None
        record.error = str(error)
        return ActionHistoryEntry.from_record(record)

    def latest(self, limit: int | None = None) -> tuple[ActionHistoryEntry, ...]:
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
                raise ValueError("action history limit must be a non-negative integer")
            if limit == 0:
                return ()
        records = list(reversed(self._records.values()))
        if limit is not None:
            records = records[:limit]
        return tuple(ActionHistoryEntry.from_record(record) for record in records)

    def get(self, action_id: str) -> ActionHistoryEntry | None:
        record = self._records.get(action_id)
        return ActionHistoryEntry.from_record(record) if record is not None else None

    def filter(
        self,
        *,
        action_type: str | None = None,
        status: ActionStatus | str | None = None,
        task_id: str | None = None,
        signal_id: str | None = None,
    ) -> tuple[ActionHistoryEntry, ...]:
        if isinstance(status, str):
            status = ActionStatus(status)
        return tuple(
            ActionHistoryEntry.from_record(record)
            for record in reversed(self._records.values())
            if (action_type is None or record.action.type == action_type)
            and (status is None or record.status is status)
            and (task_id is None or record.action.task_id == task_id)
            and (signal_id is None or record.signal_id == signal_id)
        )
