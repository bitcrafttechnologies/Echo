"""Structured, replaceable logging primitives for the Echo runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RuntimeEventType(StrEnum):
    SIGNAL_RECEIVED = "signal.received"
    SIGNAL_ROUTED = "signal.routed"
    TASK_CREATED = "task.created"
    TASK_STATUS_CHANGED = "task.status_changed"
    ACTION_CREATED = "action.created"
    ACTION_EXECUTED = "action.executed"
    STATE_CHANGED = "state.changed"
    RUNTIME_STARTED = "runtime.started"
    RUNTIME_STOPPED = "runtime.stopped"
    CONFIGURATION_RELOAD = "configuration.reload"
    ERROR = "error"


class RuntimeLogSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(slots=True, kw_only=True, frozen=True)
class RuntimeLogEvent:
    """One structured observation produced by a Runtime."""

    event_type: RuntimeEventType
    severity: RuntimeLogSeverity = RuntimeLogSeverity.INFO
    timestamp: datetime = field(default_factory=_utc_now)
    entity_id: str | None = None
    signal_id: str | None = None
    task_id: str | None = None
    action_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.event_type, str):
            object.__setattr__(self, "event_type", RuntimeEventType(self.event_type))
        if isinstance(self.severity, str):
            object.__setattr__(self, "severity", RuntimeLogSeverity(self.severity))
        if (
            self.event_type is RuntimeEventType.ERROR
            and self.severity is RuntimeLogSeverity.INFO
        ):
            object.__setattr__(self, "severity", RuntimeLogSeverity.ERROR)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "timestamp": self.timestamp.isoformat(),
            "entity_id": self.entity_id,
            "signal_id": self.signal_id,
            "task_id": self.task_id,
            "action_id": self.action_id,
            "metadata": self.metadata.copy(),
        }


class LogSink(Protocol):
    """Small interface implemented by structured runtime log destinations."""

    def write(self, event: RuntimeLogEvent) -> None: ...


class InMemoryLogSink:
    """In-process log storage suitable for inspection and tests."""

    def __init__(self) -> None:
        self._events: list[RuntimeLogEvent] = []

    @property
    def events(self) -> tuple[RuntimeLogEvent, ...]:
        return tuple(self._events)

    def write(self, event: RuntimeLogEvent) -> None:
        self._events.append(event)

    def query(
        self,
        *,
        event_type: RuntimeEventType | str | None = None,
        severity: RuntimeLogSeverity | str | None = None,
        entity_id: str | None = None,
        signal_id: str | None = None,
        task_id: str | None = None,
        action_id: str | None = None,
    ) -> tuple[RuntimeLogEvent, ...]:
        """Return matching events ordered by timestamp."""

        if isinstance(event_type, str):
            event_type = RuntimeEventType(event_type)
        if isinstance(severity, str):
            severity = RuntimeLogSeverity(severity)
        events = (
            event
            for event in self._events
            if (event_type is None or event.event_type is event_type)
            and (severity is None or event.severity is severity)
            and (entity_id is None or event.entity_id == entity_id)
            and (signal_id is None or event.signal_id == signal_id)
            and (task_id is None or event.task_id == task_id)
            and (action_id is None or event.action_id == action_id)
        )
        return tuple(sorted(events, key=lambda event: event.timestamp))
