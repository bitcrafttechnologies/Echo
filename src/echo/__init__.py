"""Public API for the Echo kernel."""

from echo.core.action import Action
from echo.core.action_history import ActionHistory, ActionHistoryEntry, ActionStatus
from echo.core.entity import Entity
from echo.core.handlers import HandlerRegistry
from echo.core.runtime import Runtime
from echo.core.runtime_log import (
    InMemoryLogSink,
    LogSink,
    RuntimeEventType,
    RuntimeLogEvent,
)
from echo.core.scheduler import Scheduler, SignalPriority
from echo.core.signal import Signal
from echo.core.signal_history import (
    SignalHistory,
    SignalHistoryEntry,
    SignalRoutingResult,
)
from echo.core.task import InvalidTaskTransition, Task, TaskStatus
from echo.core.task_history import TaskHistory, TaskHistoryEntry
from echo.entity.audit import (
    CharacterMutationAuditRecord,
    CharacterMutationDecision,
    CharacterMutationTarget,
)
from echo.entity.identity import EntityIdentity
from echo.entity.self_model import SelfModel
from echo.entity.traits import TraitEvidence, TraitProfile

__all__ = [
    "Action",
    "ActionHistory",
    "ActionHistoryEntry",
    "ActionStatus",
    "CharacterMutationAuditRecord",
    "CharacterMutationDecision",
    "CharacterMutationTarget",
    "Entity",
    "EntityIdentity",
    "HandlerRegistry",
    "InvalidTaskTransition",
    "InMemoryLogSink",
    "LogSink",
    "Runtime",
    "RuntimeEventType",
    "RuntimeLogEvent",
    "Scheduler",
    "Signal",
    "SignalHistory",
    "SignalHistoryEntry",
    "SignalPriority",
    "SignalRoutingResult",
    "SelfModel",
    "Task",
    "TaskHistory",
    "TaskHistoryEntry",
    "TaskStatus",
    "TraitEvidence",
    "TraitProfile",
]
