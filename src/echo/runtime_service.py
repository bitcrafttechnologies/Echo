"""Transport-agnostic programmatic control surface for a live Echo Runtime."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from echo.config import ConfigurationError
from echo.config_reload import (
    ConfigurationInspectionResult,
    ConfigurationReloadResult,
    RuntimeConfigurationManager,
)
from echo.core.action_history import ActionHistoryEntry, ActionStatus
from echo.core.inspection import json_safe
from echo.core.runtime import Runtime
from echo.core.runtime_log import RuntimeEventType, RuntimeLogSeverity
from echo.core.scheduler import SignalPriority
from echo.core.signal import Signal
from echo.core.signal_history import SignalHistoryEntry
from echo.core.task import TaskStatus
from echo.core.task_history import TaskHistoryEntry
from echo.entity.attention import AttentionCandidate
from echo.entity.influence import SignalInfluence
from echo.entity.relationships import RelationshipState
from echo.providers import (
    InferenceRequest,
    InferenceResult,
    InferenceUnavailableError,
    ProviderMode,
    ProviderRouter,
)
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


class InferenceServiceUnavailableError(RuntimeServiceError):
    code = "inference_unavailable"


class ConfigurationReloadUnavailableError(RuntimeServiceError):
    code = "configuration_reload_unavailable"


class ConfigurationValidationServiceError(RuntimeServiceError):
    code = "invalid_configuration"


def _copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        value = dict(value)
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
    severity: RuntimeLogSeverity | str | None = None
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
class ProviderInspectionResult:
    mode: ProviderMode
    preference: tuple[str, ...]
    configured_providers: dict[str, dict[str, Any]]
    health: dict[str, dict[str, Any]]
    active_provider: dict[str, Any] | None
    model: str | None
    last_latency_ms: float | None
    recent_failures: tuple[dict[str, Any], ...]
    recent_inferences: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "preference": list(self.preference),
            "configured_providers": _copy(self.configured_providers),
            "health": _copy(self.health),
            "active_provider": _copy(self.active_provider),
            "model": self.model,
            "last_latency_ms": self.last_latency_ms,
            "recent_failures": [_copy(item) for item in self.recent_failures],
            "recent_inferences": [_copy(item) for item in self.recent_inferences],
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

    def get_relationships(
        self, entity_id: str
    ) -> tuple[RelationshipState, ...]: ...

    def inspect_relationship(
        self, entity_id: str, subject_id: str
    ) -> RelationshipState: ...

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

    async def infer(self, request: InferenceRequest) -> InferenceResult: ...

    async def inspect_providers(self) -> ProviderInspectionResult: ...

    async def set_provider_mode(
        self, mode: ProviderMode | str
    ) -> ProviderInspectionResult: ...

    def reload_configuration(self) -> ConfigurationReloadResult: ...

    def inspect_configuration(self) -> ConfigurationInspectionResult: ...

    def update_configuration(
        self, values: Mapping[str, Any]
    ) -> ConfigurationReloadResult: ...


class RuntimeService:
    """Stable facade through which adapters and tests control one Runtime."""

    def __init__(
        self,
        runtime: Runtime,
        *,
        allowed_state_keys: Mapping[str, Iterable[str]] | None = None,
        provider_router: ProviderRouter | None = None,
        configuration_manager: RuntimeConfigurationManager | None = None,
    ) -> None:
        if not isinstance(runtime, Runtime):
            raise InvalidRequestError("runtime must be a Runtime")
        if allowed_state_keys is not None and not isinstance(
            allowed_state_keys, Mapping
        ):
            raise InvalidRequestError("allowed_state_keys must be a mapping")
        if provider_router is not None and not isinstance(provider_router, ProviderRouter):
            raise InvalidRequestError("provider_router must be a ProviderRouter")
        if configuration_manager is not None and not isinstance(
            configuration_manager, RuntimeConfigurationManager
        ):
            raise InvalidRequestError(
                "configuration_manager must be a RuntimeConfigurationManager"
            )
        if (
            configuration_manager is not None
            and configuration_manager.runtime is not runtime
        ):
            raise InvalidRequestError(
                "configuration_manager must own this Runtime"
            )
        if (
            configuration_manager is not None
            and configuration_manager.provider_router is not provider_router
        ):
            raise InvalidRequestError(
                "configuration_manager must own this ProviderRouter"
            )
        self._runtime = runtime
        self._provider_router = provider_router
        self._configuration_manager = configuration_manager
        try:
            self._allowed_state_keys = {
                entity_id: frozenset(keys)
                for entity_id, keys in (allowed_state_keys or {}).items()
            }
        except TypeError as error:
            raise InvalidRequestError(
                "allowed state keys must be iterable collections"
            ) from error

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        if not isinstance(request, InferenceRequest):
            raise InvalidRequestError("infer requires an InferenceRequest")
        if self._provider_router is None:
            raise InferenceServiceUnavailableError(
                "no intelligence providers are configured",
                details={
                    "request_id": request.request_id,
                    "mode": ProviderMode.AUTO.value,
                    "attempts": [],
                },
            )
        try:
            return await self._provider_router.infer(request)
        except InferenceUnavailableError as error:
            details = error.to_dict()
            details.pop("message", None)
            details.pop("code", None)
            raise InferenceServiceUnavailableError(
                error.reason, details=details
            ) from error

    async def inspect_providers(self) -> ProviderInspectionResult:
        router = self._provider_router
        if router is None:
            return ProviderInspectionResult(
                mode=ProviderMode.AUTO,
                preference=(),
                configured_providers={},
                health={},
                active_provider=None,
                model=None,
                last_latency_ms=None,
                recent_failures=(),
                recent_inferences=(),
            )
        status = router.status()
        health = await router.health_all()
        recent = router.recent_inferences(25)
        failures: list[dict[str, Any]] = []
        for record in recent:
            for attempt in record.attempts:
                if not attempt.succeeded:
                    failure = attempt.to_dict()
                    failure.update(
                        request_id=record.request_id,
                        mode=record.mode.value,
                        completed_at=record.completed_at.isoformat(),
                    )
                    failures.append(failure)
        active = status.current_selected_provider
        return ProviderInspectionResult(
            mode=status.mode,
            preference=tuple(slot.value for slot in status.preference),
            configured_providers={
                slot.value: metadata.to_dict()
                for slot, metadata in status.configured_providers
            },
            health={slot.value: result.to_dict() for slot, result in health},
            active_provider=active.to_dict() if active else None,
            model=active.model if active else None,
            last_latency_ms=status.latency_ms,
            recent_failures=tuple(failures[:25]),
            recent_inferences=tuple(record.to_dict() for record in recent),
        )

    async def set_provider_mode(
        self, mode: ProviderMode | str
    ) -> ProviderInspectionResult:
        if self._provider_router is None:
            raise InferenceServiceUnavailableError(
                "no intelligence providers are configured"
            )
        try:
            requested_mode = ProviderMode(mode)
            if self._configuration_manager is not None:
                self._configuration_manager.update(
                    {"providers.mode": requested_mode.value},
                    source="provider.mode",
                )
            else:
                self._provider_router.set_mode(requested_mode)
        except (ConfigurationError, TypeError, ValueError) as error:
            raise InvalidRequestError(
                "inference mode cannot be applied",
                details={"mode": str(mode), "reason": str(error)},
            ) from error
        return await self.inspect_providers()

    def reload_configuration(self) -> ConfigurationReloadResult:
        manager = self._configuration_manager
        if manager is None:
            raise ConfigurationReloadUnavailableError(
                "configuration reload is not configured for this Runtime"
            )
        try:
            return manager.reload()
        except ConfigurationError as error:
            details = error.to_dict()
            details.pop("message", None)
            details.pop("code", None)
            raise ConfigurationValidationServiceError(
                "configuration reload validation failed",
                details=details,
            ) from error

    def inspect_configuration(self) -> ConfigurationInspectionResult:
        manager = self._configuration_manager
        if manager is None:
            raise ConfigurationReloadUnavailableError(
                "configuration inspection is not configured for this Runtime"
            )
        return manager.inspect()

    def update_configuration(
        self, values: Mapping[str, Any]
    ) -> ConfigurationReloadResult:
        manager = self._configuration_manager
        if manager is None:
            raise ConfigurationReloadUnavailableError(
                "configuration control is not configured for this Runtime"
            )
        try:
            return manager.update(values)
        except ConfigurationError as error:
            details = error.to_dict()
            details.pop("message", None)
            details.pop("code", None)
            raise ConfigurationValidationServiceError(
                "configuration update validation failed",
                details=details,
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

    def get_relationships(self, entity_id: str) -> tuple[RelationshipState, ...]:
        return self._require_entity(entity_id).relationships

    def inspect_relationship(
        self, entity_id: str, subject_id: str
    ) -> RelationshipState:
        self._validate_identifier(subject_id, "subject_id")
        relationship = self._require_entity(entity_id).inspect_relationship(
            subject_id
        )
        if relationship is None:
            raise ResourceNotFoundError(
                "relationship not found",
                details={"entity_id": entity_id, "subject_id": subject_id},
            )
        return relationship

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
                severity=query.severity,
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
