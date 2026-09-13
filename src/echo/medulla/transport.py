"""Protocol-neutral transport contracts for the Medulla world boundary."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Mapping, Protocol, runtime_checkable

from echo.core.action import Action
from echo.core.signal import Signal


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TransportLifecycle(str, Enum):
    """The local lifecycle of one transport instance."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


class TransportHealthState(str, Enum):
    """Availability reported by a transport health check."""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class TransportOperation(str, Enum):
    START = "start"
    STOP = "stop"
    RECEIVE = "receive"
    EXECUTE = "execute"
    HEALTH = "health"
    VALIDATE = "validate"


class TransportErrorCode(str, Enum):
    INVALID_STATE = "invalid_state"
    INVALID_PAYLOAD = "invalid_payload"
    CONNECTION_FAILED = "connection_failed"
    TIMEOUT = "timeout"
    PERMISSION_DENIED = "permission_denied"
    UNAVAILABLE = "unavailable"
    EXECUTION_FAILED = "execution_failed"
    QUEUE_FULL = "queue_full"
    INTERNAL = "internal"


class ActionDispatchState(str, Enum):
    """How far an outbound Action progressed outside Echo."""

    ACCEPTED = "accepted"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


def _required_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _optional_string(value: Any, path: str) -> str | None:
    if value is not None:
        return _required_string(value, path)
    return None


def _safe_json(value: Any, path: str = "value") -> Any:
    """Copy an ordinary JSON value without invoking conversion hooks."""

    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        return value
    if type(value) is list:
        return [_safe_json(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if type(value) is dict:
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{path} keys must be strings")
            copied[key] = _safe_json(item, f"{path}.{key}")
        return copied
    raise ValueError(
        f"{path} contains unsupported type "
        f"{type(value).__module__}.{type(value).__qualname__}"
    )


@dataclass(slots=True, frozen=True, kw_only=True)
class TransportErrorInfo:
    transport_id: str
    operation: TransportOperation
    code: TransportErrorCode
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        _required_string(self.transport_id, "transport_id")
        _required_string(self.message, "message")

    def to_dict(self) -> dict[str, Any]:
        return {
            "transport_id": self.transport_id,
            "operation": self.operation.value,
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
        }


class TransportError(Exception):
    """Structured operational failure contained by the Medulla boundary."""

    def __init__(
        self,
        message: str,
        *,
        transport_id: str,
        operation: TransportOperation,
        code: TransportErrorCode,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.info = TransportErrorInfo(
            transport_id=_required_string(transport_id, "transport_id"),
            operation=operation,
            code=code,
            message=_required_string(message, "message"),
            retryable=retryable,
        )

    @property
    def transport_id(self) -> str:
        return self.info.transport_id

    @property
    def operation(self) -> TransportOperation:
        return self.info.operation

    @property
    def code(self) -> TransportErrorCode:
        return self.info.code

    @property
    def retryable(self) -> bool:
        return self.info.retryable

    def to_dict(self) -> dict[str, Any]:
        return self.info.to_dict()


class TransportStateError(TransportError):
    pass


class TransportValidationError(TransportError, ValueError):
    pass


class TransportOperationError(TransportError):
    pass


@dataclass(slots=True, frozen=True, kw_only=True)
class TransportStatus:
    """Non-probing, local transport state suitable for inspection."""

    transport_id: str
    lifecycle: TransportLifecycle
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    last_error: TransportErrorInfo | None = None

    def __post_init__(self) -> None:
        _required_string(self.transport_id, "transport_id")
        for name, value in (("started_at", self.started_at), ("stopped_at", self.stopped_at)):
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")


@dataclass(slots=True, frozen=True, kw_only=True)
class TransportHealth:
    """A bounded health observation; it never carries protocol objects."""

    transport_id: str
    lifecycle: TransportLifecycle
    state: TransportHealthState
    checked_at: datetime = field(default_factory=_utc_now)
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_string(self.transport_id, "transport_id")
        if self.checked_at.tzinfo is None:
            raise ValueError("checked_at must be timezone-aware")
        if self.message is not None:
            _required_string(self.message, "message")
        details = _safe_json(self.details, "health.details")
        if not isinstance(details, dict):
            raise ValueError("health.details must be an object")
        object.__setattr__(self, "details", details)


@dataclass(slots=True, frozen=True, kw_only=True)
class ActionDispatchResult:
    """Protocol-neutral acknowledgement or outcome for one Action."""

    transport_id: str
    action_id: str
    state: ActionDispatchState
    completed_at: datetime = field(default_factory=_utc_now)
    external_id: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: TransportErrorInfo | None = None

    def __post_init__(self) -> None:
        _required_string(self.transport_id, "transport_id")
        _required_string(self.action_id, "action_id")
        if self.completed_at.tzinfo is None:
            raise ValueError("completed_at must be timezone-aware")
        if self.external_id is not None:
            _required_string(self.external_id, "external_id")
        result = _safe_json(self.result, "action_result.result")
        if not isinstance(result, dict):
            raise ValueError("action_result.result must be an object")
        if self.state in (ActionDispatchState.REJECTED, ActionDispatchState.FAILED):
            if self.error is None:
                raise ValueError("rejected or failed Action results require an error")
        elif self.error is not None:
            raise ValueError("accepted or completed Action results cannot contain an error")
        if self.error is not None and self.error.transport_id != self.transport_id:
            raise ValueError("Action result error transport_id does not match transport")
        object.__setattr__(self, "result", result)


def external_signal_from_dict(data: Mapping[str, Any]) -> Signal:
    """Validate a normalized external wire object and create a fresh Signal."""

    if type(data) is not dict:
        raise ValueError("external Signal must be an ordinary object")
    allowed = {"id", "type", "source", "timestamp", "payload", "metadata"}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"external Signal contains unknown fields: {sorted(unknown)!r}")

    timestamp_value = data.get("timestamp")
    if not isinstance(timestamp_value, str):
        raise ValueError("signal.timestamp must be an ISO-8601 string")
    try:
        timestamp = datetime.fromisoformat(timestamp_value)
    except ValueError as error:
        raise ValueError("signal.timestamp must be an ISO-8601 string") from error
    if timestamp.tzinfo is None:
        raise ValueError("signal.timestamp must be timezone-aware")

    payload = _safe_json(data.get("payload", {}), "signal.payload")
    metadata = _safe_json(data.get("metadata", {}), "signal.metadata")
    if not isinstance(payload, dict) or not isinstance(metadata, dict):
        raise ValueError("signal payload and metadata must be objects")
    return Signal(
        id=_required_string(data.get("id"), "signal.id"),
        type=_required_string(data.get("type"), "signal.type"),
        source=_required_string(data.get("source"), "signal.source"),
        timestamp=timestamp,
        payload=payload,
        metadata=metadata,
    )


def validate_inbound_signal(signal: Signal) -> Signal:
    """Return a detached, validated base Signal from a transport result."""

    if not isinstance(signal, Signal):
        raise ValueError("transport receive must return a Signal")
    return external_signal_from_dict(signal.to_dict())


def validate_outbound_action(action: Action) -> Action:
    """Return a detached Action whose wire-facing values are safe JSON."""

    if not isinstance(action, Action):
        raise ValueError("transport execute requires an Action")
    if action.created_at.tzinfo is None:
        raise ValueError("action.created_at must be timezone-aware")
    entity_id = _optional_string(action.entity_id, "action.entity_id")
    task_id = _optional_string(action.task_id, "action.task_id")
    parameters = _safe_json(action.parameters, "action.parameters")
    if not isinstance(parameters, dict):
        raise ValueError("action.parameters must be an object")
    return Action(
        id=_required_string(action.id, "action.id"),
        type=_required_string(action.type, "action.type"),
        parameters=parameters,
        entity_id=entity_id,
        task_id=task_id,
        created_at=action.created_at,
    )


@runtime_checkable
class Transport(Protocol):
    """Structural contract implemented by every Medulla transport."""

    @property
    def transport_id(self) -> str: ...

    def status(self) -> TransportStatus: ...

    async def start(self) -> TransportStatus: ...

    async def stop(self) -> TransportStatus: ...

    async def receive(self) -> Signal: ...

    async def execute(self, action: Action) -> ActionDispatchResult: ...

    async def health(self) -> TransportHealth: ...


class BaseTransport(ABC):
    """Lifecycle and failure containment shared by transport implementations.

    Concrete transports implement only the protected protocol-specific hooks.
    Public methods validate boundary values and translate unexpected I/O errors
    into ``TransportError``. ``asyncio.CancelledError`` remains cooperative
    cancellation and is never disguised as an I/O failure.
    """

    def __init__(self, transport_id: str) -> None:
        self._transport_id = _required_string(transport_id, "transport_id")
        self._lifecycle = TransportLifecycle.STOPPED
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = _utc_now()
        self._last_error: TransportErrorInfo | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def transport_id(self) -> str:
        return self._transport_id

    def status(self) -> TransportStatus:
        return TransportStatus(
            transport_id=self.transport_id,
            lifecycle=self._lifecycle,
            started_at=self._started_at,
            stopped_at=self._stopped_at,
            last_error=self._last_error,
        )

    def _state_error(self, operation: TransportOperation) -> TransportStateError:
        return TransportStateError(
            f"transport is {self._lifecycle.value}",
            transport_id=self.transport_id,
            operation=operation,
            code=TransportErrorCode.INVALID_STATE,
        )

    def _operation_error(
        self, operation: TransportOperation, error: Exception
    ) -> TransportError:
        if isinstance(error, TransportError):
            translated = error
        else:
            message = str(error) or type(error).__name__
            translated = TransportOperationError(
                f"{operation.value} failed: {message}",
                transport_id=self.transport_id,
                operation=operation,
                code=TransportErrorCode.INTERNAL,
            )
        self._last_error = translated.info
        return translated

    async def start(self) -> TransportStatus:
        async with self._lifecycle_lock:
            if self._lifecycle is TransportLifecycle.RUNNING:
                return self.status()
            if self._lifecycle in (TransportLifecycle.STARTING, TransportLifecycle.STOPPING):
                raise self._state_error(TransportOperation.START)
            self._lifecycle = TransportLifecycle.STARTING
            try:
                await self._start()
            except asyncio.CancelledError:
                self._lifecycle = TransportLifecycle.FAILED
                raise
            except TransportError as error:
                self._lifecycle = TransportLifecycle.FAILED
                self._last_error = error.info
                raise
            except Exception as error:
                self._lifecycle = TransportLifecycle.FAILED
                raise self._operation_error(TransportOperation.START, error) from error
            self._lifecycle = TransportLifecycle.RUNNING
            self._started_at = _utc_now()
            self._stopped_at = None
            self._last_error = None
            return self.status()

    async def stop(self) -> TransportStatus:
        async with self._lifecycle_lock:
            if self._lifecycle is TransportLifecycle.STOPPED:
                return self.status()
            if self._lifecycle in (TransportLifecycle.STARTING, TransportLifecycle.STOPPING):
                raise self._state_error(TransportOperation.STOP)
            self._lifecycle = TransportLifecycle.STOPPING
            try:
                await self._stop()
            except asyncio.CancelledError:
                self._lifecycle = TransportLifecycle.FAILED
                raise
            except TransportError as error:
                self._lifecycle = TransportLifecycle.FAILED
                self._last_error = error.info
                raise
            except Exception as error:
                self._lifecycle = TransportLifecycle.FAILED
                raise self._operation_error(TransportOperation.STOP, error) from error
            self._lifecycle = TransportLifecycle.STOPPED
            self._stopped_at = _utc_now()
            return self.status()

    async def receive(self) -> Signal:
        if self._lifecycle is not TransportLifecycle.RUNNING:
            raise self._state_error(TransportOperation.RECEIVE)
        try:
            return validate_inbound_signal(await self._receive())
        except asyncio.CancelledError:
            raise
        except TransportError as error:
            self._last_error = error.info
            raise
        except ValueError as error:
            translated = TransportValidationError(
                str(error),
                transport_id=self.transport_id,
                operation=TransportOperation.VALIDATE,
                code=TransportErrorCode.INVALID_PAYLOAD,
            )
            self._last_error = translated.info
            raise translated from error
        except Exception as error:
            raise self._operation_error(TransportOperation.RECEIVE, error) from error

    async def execute(self, action: Action) -> ActionDispatchResult:
        if self._lifecycle is not TransportLifecycle.RUNNING:
            raise self._state_error(TransportOperation.EXECUTE)
        try:
            safe_action = validate_outbound_action(action)
            result = await self._execute(safe_action)
            if not isinstance(result, ActionDispatchResult):
                raise ValueError("transport execute must return an ActionDispatchResult")
            if result.transport_id != self.transport_id:
                raise ValueError("Action result transport_id does not match transport")
            if result.action_id != safe_action.id:
                raise ValueError("Action result action_id does not match Action")
            return result
        except asyncio.CancelledError:
            raise
        except TransportError as error:
            self._last_error = error.info
            raise
        except ValueError as error:
            translated = TransportValidationError(
                str(error),
                transport_id=self.transport_id,
                operation=TransportOperation.VALIDATE,
                code=TransportErrorCode.INVALID_PAYLOAD,
            )
            self._last_error = translated.info
            raise translated from error
        except Exception as error:
            raise self._operation_error(TransportOperation.EXECUTE, error) from error

    async def health(self) -> TransportHealth:
        """Return unavailable health instead of raising on probe failure."""

        try:
            health = await self._health()
            if not isinstance(health, TransportHealth):
                raise ValueError("transport health must return TransportHealth")
            if health.transport_id != self.transport_id:
                raise ValueError("health transport_id does not match transport")
            if health.lifecycle is not self._lifecycle:
                raise ValueError("health lifecycle does not match transport")
            return health
        except asyncio.CancelledError:
            raise
        except Exception as error:
            translated = self._operation_error(TransportOperation.HEALTH, error)
            return TransportHealth(
                transport_id=self.transport_id,
                lifecycle=self._lifecycle,
                state=TransportHealthState.UNAVAILABLE,
                message=str(translated),
            )

    @abstractmethod
    async def _start(self) -> None: ...

    @abstractmethod
    async def _stop(self) -> None: ...

    @abstractmethod
    async def _receive(self) -> Signal: ...

    @abstractmethod
    async def _execute(self, action: Action) -> ActionDispatchResult: ...

    @abstractmethod
    async def _health(self) -> TransportHealth: ...
