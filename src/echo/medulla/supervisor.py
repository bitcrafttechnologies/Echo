"""Lifecycle and routing composition at Echo's Medulla boundary."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol, runtime_checkable

from echo.core.action import Action
from echo.core.signal import Signal
from echo.medulla.transport import (
    ActionDispatchResult,
    ActionDispatchState,
    Transport,
    TransportError,
    TransportErrorCode,
    TransportErrorInfo,
    TransportHealth,
    TransportHealthState,
    TransportLifecycle,
    TransportOperation,
    TransportStatus,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@runtime_checkable
class SignalTarget(Protocol):
    """The narrow inward port required by Medulla.

    ``echo.core.Runtime`` satisfies this protocol without Core importing or
    otherwise knowing about Medulla.
    """

    async def emit(self, signal: Signal) -> None: ...


class MedullaLifecycle(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(slots=True, frozen=True, kw_only=True)
class MedullaFailure:
    """A contained transport or inward-delivery failure."""

    transport_id: str
    operation: TransportOperation
    code: TransportErrorCode
    message: str
    retryable: bool = False
    occurred_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.transport_id:
            raise ValueError("transport_id must not be empty")
        if not self.message:
            raise ValueError("message must not be empty")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")

    def to_transport_error(self) -> TransportErrorInfo:
        return TransportErrorInfo(
            transport_id=self.transport_id,
            operation=self.operation,
            code=self.code,
            message=self.message,
            retryable=self.retryable,
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class MedullaStatus:
    lifecycle: MedullaLifecycle
    transports: tuple[TransportStatus, ...]
    receiving_transport_ids: tuple[str, ...]
    recent_failures: tuple[MedullaFailure, ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class MedullaHealth:
    state: TransportHealthState
    checked_at: datetime
    transports: tuple[TransportHealth, ...]
    receiving_transport_ids: tuple[str, ...]


class MedullaSupervisor:
    """Own transport lifecycle, inbound pumps, health, and explicit dispatch.

    This class contains boundary failures instead of allowing transport I/O to
    terminate Echo Core. It provides no discovery, trust, cognition, automatic
    Action execution, retry, or capability-routing policy.
    """

    def __init__(
        self,
        signal_target: SignalTarget,
        transports: Iterable[Transport],
        *,
        default_transport_id: str | None = None,
        failure_history_size: int = 100,
    ) -> None:
        if not isinstance(signal_target, SignalTarget):
            raise TypeError("signal_target must implement SignalTarget")
        if type(failure_history_size) is not int or failure_history_size < 1:
            raise ValueError("failure_history_size must be a positive integer")

        indexed: dict[str, Transport] = {}
        for transport in transports:
            if not isinstance(transport, Transport):
                raise TypeError("every transport must implement Transport")
            if transport.transport_id in indexed:
                raise ValueError(
                    f"duplicate transport id: {transport.transport_id}"
                )
            indexed[transport.transport_id] = transport
        if not indexed:
            raise ValueError("at least one transport is required")
        if default_transport_id is None and len(indexed) == 1:
            default_transport_id = next(iter(indexed))
        if default_transport_id is not None and default_transport_id not in indexed:
            raise ValueError("default_transport_id must name a configured transport")

        self._signal_target = signal_target
        self._transports = indexed
        self._default_transport_id = default_transport_id
        self._failures: deque[MedullaFailure] = deque(maxlen=failure_history_size)
        self._receivers: dict[str, asyncio.Task[None]] = {}
        self._lifecycle = MedullaLifecycle.STOPPED
        self._lifecycle_lock = asyncio.Lock()

    @property
    def default_transport_id(self) -> str | None:
        return self._default_transport_id

    def transport(self, transport_id: str) -> Transport | None:
        return self._transports.get(transport_id)

    def status(self) -> MedullaStatus:
        receiving = tuple(
            transport_id
            for transport_id, task in self._receivers.items()
            if not task.done()
        )
        return MedullaStatus(
            lifecycle=self._lifecycle,
            transports=tuple(
                transport.status() for transport in self._transports.values()
            ),
            receiving_transport_ids=receiving,
            recent_failures=tuple(self._failures),
        )

    async def start(self) -> MedullaStatus:
        """Start each transport independently and attach an inbound pump."""

        async with self._lifecycle_lock:
            if self._lifecycle is MedullaLifecycle.RUNNING:
                return self.status()
            if self._lifecycle in {
                MedullaLifecycle.STARTING,
                MedullaLifecycle.STOPPING,
            }:
                return self.status()
            self._lifecycle = MedullaLifecycle.STARTING
            self._receivers.clear()
            for transport_id, transport in self._transports.items():
                try:
                    status = await transport.start()
                except asyncio.CancelledError:
                    self._lifecycle = MedullaLifecycle.FAILED
                    raise
                except Exception as error:
                    self._record_failure(
                        transport_id, TransportOperation.START, error
                    )
                    continue
                if status.lifecycle is TransportLifecycle.RUNNING:
                    self._receivers[transport_id] = asyncio.create_task(
                        self._receive_loop(transport),
                        name=f"echo-medulla-receive-{transport_id}",
                    )
            self._lifecycle = (
                MedullaLifecycle.RUNNING
                if self._receivers
                else MedullaLifecycle.FAILED
            )
            return self.status()

    async def stop(self) -> MedullaStatus:
        """Cancel inbound pumps, then stop every transport independently."""

        async with self._lifecycle_lock:
            if self._lifecycle is MedullaLifecycle.STOPPED:
                return self.status()
            self._lifecycle = MedullaLifecycle.STOPPING
            tasks = tuple(self._receivers.values())
            self._receivers.clear()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for transport_id, transport in reversed(tuple(self._transports.items())):
                try:
                    await transport.stop()
                except asyncio.CancelledError:
                    self._lifecycle = MedullaLifecycle.FAILED
                    raise
                except Exception as error:
                    self._record_failure(
                        transport_id, TransportOperation.STOP, error
                    )
            self._lifecycle = MedullaLifecycle.STOPPED
            return self.status()

    async def dispatch(
        self,
        action: Action,
        *,
        transport_id: str | None = None,
    ) -> ActionDispatchResult:
        """Send one already-approved Action through an explicit transport.

        Failure is returned as structured data. Calling this method never
        changes Runtime Action history and never makes an authorization choice.
        """

        selected_id = transport_id or self._default_transport_id
        if selected_id is None:
            return self._failed_dispatch(
                action,
                transport_id="unselected",
                code=TransportErrorCode.UNAVAILABLE,
                message="transport_id is required when no default is configured",
            )
        transport = self._transports.get(selected_id)
        if transport is None:
            return self._failed_dispatch(
                action,
                transport_id=selected_id,
                code=TransportErrorCode.UNAVAILABLE,
                message=f"transport is not configured: {selected_id}",
            )
        if self._lifecycle is not MedullaLifecycle.RUNNING:
            return self._failed_dispatch(
                action,
                transport_id=selected_id,
                code=TransportErrorCode.INVALID_STATE,
                message=f"Medulla is {self._lifecycle.value}",
            )
        try:
            return await transport.execute(action)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failure = self._record_failure(
                selected_id, TransportOperation.EXECUTE, error
            )
            return ActionDispatchResult(
                transport_id=selected_id,
                action_id=action.id,
                state=ActionDispatchState.FAILED,
                error=failure.to_transport_error(),
            )

    async def health(self) -> MedullaHealth:
        """Return aggregate health without propagating a probe failure."""

        observations = await asyncio.gather(
            *(transport.health() for transport in self._transports.values()),
            return_exceptions=True,
        )
        health: list[TransportHealth] = []
        for (transport_id, transport), result in zip(
            self._transports.items(), observations, strict=True
        ):
            if isinstance(result, BaseException):
                failure = self._record_failure(
                    transport_id, TransportOperation.HEALTH, result
                )
                health.append(
                    TransportHealth(
                        transport_id=transport_id,
                        lifecycle=transport.status().lifecycle,
                        state=TransportHealthState.UNAVAILABLE,
                        message=failure.message,
                    )
                )
            else:
                health.append(result)

        receiving = self.status().receiving_transport_ids
        running_ids = {
            item.transport_id
            for item in health
            if item.lifecycle is TransportLifecycle.RUNNING
        }
        if not health or all(
            item.state is TransportHealthState.UNAVAILABLE for item in health
        ):
            state = TransportHealthState.UNAVAILABLE
        elif (
            self._lifecycle is MedullaLifecycle.RUNNING
            and all(item.state is TransportHealthState.HEALTHY for item in health)
            and running_ids == set(receiving)
        ):
            state = TransportHealthState.HEALTHY
        else:
            state = TransportHealthState.DEGRADED
        return MedullaHealth(
            state=state,
            checked_at=_utc_now(),
            transports=tuple(health),
            receiving_transport_ids=receiving,
        )

    async def __aenter__(self) -> MedullaSupervisor:
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()

    async def _receive_loop(self, transport: Transport) -> None:
        while True:
            try:
                signal = await transport.receive()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._record_failure(
                    transport.transport_id, TransportOperation.RECEIVE, error
                )
                return
            try:
                await self._signal_target.emit(signal)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Handler/runtime failures are observations at this boundary.
                # The transport remains able to deliver later Signals.
                self._record_failure(
                    transport.transport_id, TransportOperation.RECEIVE, error
                )

    def _record_failure(
        self,
        transport_id: str,
        operation: TransportOperation,
        error: BaseException,
    ) -> MedullaFailure:
        if isinstance(error, TransportError):
            failure = MedullaFailure(
                transport_id=transport_id,
                operation=operation,
                code=error.code,
                message=str(error),
                retryable=error.retryable,
            )
        else:
            failure = MedullaFailure(
                transport_id=transport_id,
                operation=operation,
                code=TransportErrorCode.INTERNAL,
                message=str(error) or type(error).__name__,
            )
        self._failures.append(failure)
        return failure

    def _failed_dispatch(
        self,
        action: Action,
        *,
        transport_id: str,
        code: TransportErrorCode,
        message: str,
    ) -> ActionDispatchResult:
        failure = MedullaFailure(
            transport_id=transport_id,
            operation=TransportOperation.EXECUTE,
            code=code,
            message=message,
        )
        self._failures.append(failure)
        return ActionDispatchResult(
            transport_id=transport_id,
            action_id=action.id,
            state=ActionDispatchState.FAILED,
            error=failure.to_transport_error(),
        )
