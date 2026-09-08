"""Transport-agnostic programmatic control surface for a live Echo Runtime."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from echo.core.action_history import ActionHistoryEntry, ActionStatus
from echo.core.inspection import json_safe
from echo.core.runtime import Runtime
from echo.core.runtime_log import RuntimeEventType
from echo.core.scheduler import SignalPriority
from echo.core.signal import Signal
from echo.core.signal_history import SignalHistoryEntry
from echo.core.task import TaskStatus
from echo.core.task_history import TaskHistoryEntry
from echo.entity.attention import AttentionCandidate
from echo.entity.influence import SignalInfluence
from echo.runtime_events import (
    InvalidSubscriptionError,
    RuntimeEventSubscription,
    RuntimeSubscriptionRequest,
)


class RuntimeServiceError(Exception):
    """Base class for clean failures at the runtime service boundary."""

    code = "runtime_service_error"

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = _copy(dict(details or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": _copy(self.details),
        }


class InvalidRequestError(RuntimeServiceError):
    code = "invalid_request"


class ResourceNotFoundError(RuntimeServiceError):
    code = "not_found"


class StateUpdateNotAllowedError(RuntimeServiceError):
    code = "state_update_not_allowed"


class TaskNotCancellableError(RuntimeServiceError):
    code = "task_not_cancellable"


class SignalEmissionError(RuntimeServiceError):
    code = "signal_emission_failed"


class InvalidCharacterInfluenceError(RuntimeServiceError):
    code = "invalid_character_influence"


def _copy(value: Any) -> Any:
    try:
        return deepcopy(value)
    except Exception:
        return json_safe(value)


def _validate_limit(limit: int | None) -> None:
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
    ):
        raise InvalidRequestError("limit must be a non-negative integer")


@dataclass(slots=True, kw_only=True, frozen=True)
class RuntimeStatusResult:
    runtime_id: str
    status: str
    uptime_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "status": self.status,
            "uptime_seconds": self.uptime_seconds,
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class EntityResult:
    id: str
    state: dict[str, Any]
    character: dict[str, Any]
    active_task_ids: tuple[str, ...]
    handlers: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": _copy(self.state),
            "character": _copy(self.character),
            "active_task_ids": list(self.active_task_ids),
            "handlers": _copy(self.handlers),
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class EmitSignalRequest:
    signal: Signal
    priority: SignalPriority | int | str = SignalPriority.NORMAL


@dataclass(slots=True, kw_only=True, frozen=True)
class SignalQuery:
    limit: int | None = 20
    signal_type: str | None = None
    source: str | None = None


@dataclass(slots=True, kw_only=True, frozen=True)
class TaskQuery:
    limit: int | None = 20
    status: TaskStatus | str | None = None


@dataclass(slots=True, kw_only=True, frozen=True)
class ActionQuery:
    limit: int | None = 20
    action_type: str | None = None
    status: ActionStatus | str | None = None
    task_id: str | None = None
    signal_id: str | None = None


@dataclass(slots=True, kw_only=True, frozen=True)
class StateResult:
    entity_id: str
    values: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"entity_id": self.entity_id, "values": _copy(self.values)}


@dataclass(slots=True, kw_only=True, frozen=True)
class SetStateValuesRequest:
    entity_id: str
    values: Mapping[str, Any]


@dataclass(slots=True, kw_only=True, frozen=True)
class LogQuery:
    limit: int | None = 100
    event_type: RuntimeEventType | str | None = None
    entity_id: str | None = None
    signal_id: str | None = None
    task_id: str | None = None
    action_id: str | None = None


@dataclass(slots=True, kw_only=True, frozen=True)
class LogResult:
    events: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"events": [_copy(event) for event in self.events]}


@dataclass(slots=True, kw_only=True, frozen=True)
class CharacterStateResult:
    entity_id: str
    internal_state: dict[str, float]
    drive_baselines: dict[str, float]
    drive_activations: dict[str, float]
    attention_candidates: tuple[AttentionCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "internal_state": self.internal_state.copy(),
            "drive_baselines": self.drive_baselines.copy(),
            "drive_activations": self.drive_activations.copy(),
            "attention_candidates": [
                candidate.to_dict() for candidate in self.attention_candidates
            ],
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class ApplySignalInfluenceRequest:
    entity_id: str
    signal_id: str
    influence: SignalInfluence


@dataclass(slots=True, kw_only=True, frozen=True)
class SignalInfluenceResult:
    entity_id: str
    signal_id: str
    internal_state: dict[str, float]
    drive_activations: dict[str, float]
    attention_candidate: AttentionCandidate | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "signal_id": self.signal_id,
            "internal_state": self.internal_state.copy(),
            "drive_activations": self.drive_activations.copy(),
            "attention_candidate": (
                self.attention_candidate.to_dict()
                if self.attention_candidate is not None
                else None
            ),
        }


@runtime_checkable
class RuntimeServiceProtocol(Protocol):
    """Public control contract implemented by RuntimeService."""

    def get_runtime_status(self) -> RuntimeStatusResult: ...

    def get_entities(self) -> tuple[EntityResult, ...]: ...

    def inspect_entity(self, entity_id: str) -> EntityResult: ...

    async def emit_signal(
        self,
        request: EmitSignalRequest,
    ) -> SignalHistoryEntry: ...

    def get_recent_signals(
        self,
        query: SignalQuery | None = None,
    ) -> tuple[SignalHistoryEntry, ...]: ...

    def inspect_signal(self, signal_id: str) -> SignalHistoryEntry: ...

    def get_tasks(
        self,
        query: TaskQuery | None = None,
    ) -> tuple[TaskHistoryEntry, ...]: ...

    def inspect_task(self, task_id: str) -> TaskHistoryEntry: ...

    async def cancel_task(self, task_id: str) -> TaskHistoryEntry: ...

    def get_actions(
        self,
        query: ActionQuery | None = None,
    ) -> tuple[ActionHistoryEntry, ...]: ...

    def inspect_action(self, action_id: str) -> ActionHistoryEntry: ...

    def get_entity_state(self, entity_id: str) -> StateResult: ...

    def set_allowed_state_values(
        self,
        request: SetStateValuesRequest,
    ) -> StateResult: ...

    def get_logs(self, query: LogQuery | None = None) -> LogResult: ...

    def subscribe_events(
        self,
        request: RuntimeSubscriptionRequest | None = None,
    ) -> RuntimeEventSubscription: ...

    def get_character_state(self, entity_id: str) -> CharacterStateResult: ...

    def get_attention_candidates(
        self,
        entity_id: str,
        limit: int | None = 20,
    ) -> tuple[AttentionCandidate, ...]: ...

    def apply_signal_influence(
        self,
        request: ApplySignalInfluenceRequest,
    ) -> SignalInfluenceResult: ...


class RuntimeService:
    """Stable facade through which adapters and tests control one Runtime."""

    def __init__(
        self,
        runtime: Runtime,
        *,
        allowed_state_keys: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        if not isinstance(runtime, Runtime):
            raise InvalidRequestError("runtime must be a Runtime")
        if allowed_state_keys is not None and not isinstance(
            allowed_state_keys, Mapping
        ):
            raise InvalidRequestError("allowed_state_keys must be a mapping")
        self._runtime = runtime
        try:
            self._allowed_state_keys = {
                entity_id: frozenset(keys)
                for entity_id, keys in (allowed_state_keys or {}).items()
            }
        except TypeError as error:
            raise InvalidRequestError(
                "allowed state keys must be iterable collections"
            ) from error

    def get_runtime_status(self) -> RuntimeStatusResult:
        snapshot = self._runtime.inspect(recent_limit=0)
        return RuntimeStatusResult(
            runtime_id=snapshot["runtime_id"],
            status=snapshot["runtime_status"],
            uptime_seconds=snapshot["uptime_seconds"],
        )

    def get_entities(self) -> tuple[EntityResult, ...]:
        return tuple(
            self._entity_result(entity_id) for entity_id in self._runtime.entities
        )

    def inspect_entity(self, entity_id: str) -> EntityResult:
        self._require_entity(entity_id)
        return self._entity_result(entity_id)

    async def emit_signal(self, request: EmitSignalRequest) -> SignalHistoryEntry:
        if not isinstance(request, EmitSignalRequest) or not isinstance(
            request.signal, Signal
        ):
            raise InvalidRequestError("emit_signal requires an EmitSignalRequest")
        if isinstance(request.priority, str):
            if request.priority.upper() not in SignalPriority.__members__:
                raise InvalidRequestError(
                    f"unknown signal priority: {request.priority}"
                )
        else:
            try:
                int(request.priority)
            except (TypeError, ValueError) as error:
                raise InvalidRequestError(
                    "signal priority must be a name or integer"
                ) from error
        try:
            await self._runtime.emit(request.signal, priority=request.priority)
        except Exception as error:
            raise SignalEmissionError(
                "signal processing failed",
                details={
                    "signal_id": request.signal.id,
                    "error_type": type(error).__name__,
                    "message": str(error),
                },
            ) from error
        result = self._runtime.get_signal(request.signal.id)
        if result is None:
            raise RuntimeServiceError("emitted signal was not retained")
        return result

    def get_recent_signals(
        self,
        query: SignalQuery | None = None,
    ) -> tuple[SignalHistoryEntry, ...]:
        if query is not None and not isinstance(query, SignalQuery):
            raise InvalidRequestError("get_recent_signals requires a SignalQuery")
        query = query or SignalQuery()
        _validate_limit(query.limit)
        values = self._runtime.filter_signals(
            signal_type=query.signal_type,
            source=query.source,
        )
        return values if query.limit is None else values[: query.limit]

    def inspect_signal(self, signal_id: str) -> SignalHistoryEntry:
        self._validate_identifier(signal_id, "signal_id")
        result = self._runtime.get_signal(signal_id)
        if result is None:
            raise ResourceNotFoundError(
                "signal not found", details={"signal_id": signal_id}
            )
        return result

    def get_tasks(self, query: TaskQuery | None = None) -> tuple[TaskHistoryEntry, ...]:
        if query is not None and not isinstance(query, TaskQuery):
            raise InvalidRequestError("get_tasks requires a TaskQuery")
        query = query or TaskQuery()
        _validate_limit(query.limit)
        try:
            values = self._runtime.filter_tasks(status=query.status)
        except ValueError as error:
            raise InvalidRequestError(str(error)) from error
        return values if query.limit is None else values[: query.limit]

    def inspect_task(self, task_id: str) -> TaskHistoryEntry:
        self._validate_identifier(task_id, "task_id")
        result = self._runtime.get_task(task_id)
        if result is None:
            raise ResourceNotFoundError("task not found", details={"task_id": task_id})
        return result

    async def cancel_task(self, task_id: str) -> TaskHistoryEntry:
        before = self.inspect_task(task_id)
        if before.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            raise TaskNotCancellableError(
                "task is already terminal",
                details={"task_id": task_id, "status": before.status.value},
            )
        result = await self._runtime.cancel_task(task_id)
        if result is None:
            raise ResourceNotFoundError("task not found", details={"task_id": task_id})
        if result.status is not TaskStatus.CANCELLED:
            raise TaskNotCancellableError(
                "task has no cancellable live execution",
                details={"task_id": task_id, "status": result.status.value},
            )
        return result

    def get_actions(
        self,
        query: ActionQuery | None = None,
    ) -> tuple[ActionHistoryEntry, ...]:
        if query is not None and not isinstance(query, ActionQuery):
            raise InvalidRequestError("get_actions requires an ActionQuery")
        query = query or ActionQuery()
        _validate_limit(query.limit)
        try:
            values = self._runtime.filter_actions(
                action_type=query.action_type,
                status=query.status,
                task_id=query.task_id,
                signal_id=query.signal_id,
            )
        except ValueError as error:
            raise InvalidRequestError(str(error)) from error
        return values if query.limit is None else values[: query.limit]

    def inspect_action(self, action_id: str) -> ActionHistoryEntry:
        self._validate_identifier(action_id, "action_id")
        result = self._runtime.get_action(action_id)
        if result is None:
            raise ResourceNotFoundError(
                "action not found", details={"action_id": action_id}
            )
        return result

    def get_entity_state(self, entity_id: str) -> StateResult:
        entity = self._require_entity(entity_id)
        return StateResult(entity_id=entity_id, values=_copy(entity.state))

    def get_character_state(self, entity_id: str) -> CharacterStateResult:
        entity = self._require_entity(entity_id)
        return CharacterStateResult(
            entity_id=entity.id,
            internal_state=entity.internal_state.to_dict(),
            drive_baselines=entity.drives.to_dict(),
            drive_activations=dict(entity.drive_activations),
            attention_candidates=entity.attention_candidates,
        )

    def get_attention_candidates(
        self,
        entity_id: str,
        limit: int | None = 20,
    ) -> tuple[AttentionCandidate, ...]:
        _validate_limit(limit)
        entity = self._require_entity(entity_id)
        candidates = entity.attention_candidates
        return candidates if limit is None else candidates[:limit]

    def apply_signal_influence(
        self,
        request: ApplySignalInfluenceRequest,
    ) -> SignalInfluenceResult:
        if not isinstance(request, ApplySignalInfluenceRequest):
            raise InvalidRequestError(
                "apply_signal_influence requires an ApplySignalInfluenceRequest"
            )
        self._validate_identifier(request.entity_id, "entity_id")
        self._validate_identifier(request.signal_id, "signal_id")
        if not isinstance(request.influence, SignalInfluence):
            raise InvalidRequestError("influence must be a SignalInfluence")
        self._require_entity(request.entity_id)
        self.inspect_signal(request.signal_id)
        try:
            result = self._runtime._apply_signal_influence(
                request.entity_id,
                request.signal_id,
                request.influence,
            )
        except (KeyError, ValueError) as error:
            raise InvalidCharacterInfluenceError(
                str(error),
                details={
                    "entity_id": request.entity_id,
                    "signal_id": request.signal_id,
                },
            ) from error
        assert result is not None
        return SignalInfluenceResult(
            entity_id=request.entity_id,
            signal_id=request.signal_id,
            internal_state=result["internal_state"],
            drive_activations=result["drive_activations"],
            attention_candidate=result["attention_candidate"],
        )

    def set_allowed_state_values(self, request: SetStateValuesRequest) -> StateResult:
        if not isinstance(request, SetStateValuesRequest):
            raise InvalidRequestError(
                "set_allowed_state_values requires a SetStateValuesRequest"
            )
        self._require_entity(request.entity_id)
        if not isinstance(request.values, Mapping):
            raise InvalidRequestError("state values must be a mapping")
        values = _copy(dict(request.values))
        allowed = self._allowed_state_keys.get(request.entity_id, frozenset())
        denied = sorted(set(values) - allowed)
        if denied:
            raise StateUpdateNotAllowedError(
                "one or more state keys are not writable",
                details={"entity_id": request.entity_id, "keys": denied},
            )
        updated = self._runtime.update_entity_state(request.entity_id, values)
        assert updated is not None
        return StateResult(entity_id=request.entity_id, values=updated)

    def get_logs(self, query: LogQuery | None = None) -> LogResult:
        if query is not None and not isinstance(query, LogQuery):
            raise InvalidRequestError("get_logs requires a LogQuery")
        query = query or LogQuery()
        _validate_limit(query.limit)
        try:
            events = self._runtime.latest_logs(
                query.limit,
                event_type=query.event_type,
                entity_id=query.entity_id,
                signal_id=query.signal_id,
                task_id=query.task_id,
                action_id=query.action_id,
            )
        except ValueError as error:
            raise InvalidRequestError(str(error)) from error
        return LogResult(events=tuple(json_safe(event.to_dict()) for event in events))

    def subscribe_events(
        self,
        request: RuntimeSubscriptionRequest | None = None,
    ) -> RuntimeEventSubscription:
        """Subscribe to future live activity without exposing Runtime internals."""

        try:
            return self._runtime.subscribe_events(request)
        except InvalidSubscriptionError as error:
            raise InvalidRequestError(
                error.message,
                details=error.details,
            ) from error

    def _require_entity(self, entity_id: str) -> Any:
        self._validate_identifier(entity_id, "entity_id")
        entity = self._runtime.get_entity(entity_id)
        if entity is None:
            raise ResourceNotFoundError(
                "entity not found", details={"entity_id": entity_id}
            )
        return entity

    def _entity_result(self, entity_id: str) -> EntityResult:
        entity = self._require_entity(entity_id)
        return EntityResult(
            id=entity.id,
            state=_copy(entity.state),
            character=_copy(entity.inspect_character()),
            active_task_ids=tuple(entity.active_tasks),
            handlers=_copy(entity.handlers.inspect()),
        )

    @staticmethod
    def _validate_identifier(value: str, name: str) -> None:
        if not isinstance(value, str) or not value:
            raise InvalidRequestError(f"{name} must be a non-empty string")
