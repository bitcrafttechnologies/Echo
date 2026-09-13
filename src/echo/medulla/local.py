"""Bounded same-process reference transport for Medulla."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, TypeVar

from echo.core.action import Action
from echo.core.signal import Signal
from echo.medulla.transport import (
    ActionDispatchResult,
    ActionDispatchState,
    BaseTransport,
    TransportErrorCode,
    TransportHealth,
    TransportHealthState,
    TransportLifecycle,
    TransportOperation,
    TransportOperationError,
    TransportStatus,
    TransportValidationError,
    external_signal_from_dict,
    validate_inbound_signal,
    validate_outbound_action,
)


class QueueOverflowPolicy(str, Enum):
    """Non-blocking behavior when a LocalQueueTransport queue is full."""

    REJECT_NEWEST = "reject_newest"
    DROP_OLDEST = "drop_oldest"


class LocalQueueFullError(TransportOperationError):
    """A bounded local queue rejected its newest item."""


@dataclass(slots=True, frozen=True, kw_only=True)
class QueueOfferResult:
    accepted: bool
    depth: int
    capacity: int
    dropped_id: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class LocalQueueTransportStatus(TransportStatus):
    inbound_depth: int = 0
    inbound_capacity: int = 1
    outbound_depth: int = 0
    outbound_capacity: int = 1
    inbound_rejected: int = 0
    inbound_dropped: int = 0
    outbound_rejected: int = 0
    outbound_dropped: int = 0
    inbound_discarded_on_stop: int = 0
    outbound_discarded_on_stop: int = 0
    inbound_overflow: QueueOverflowPolicy = QueueOverflowPolicy.REJECT_NEWEST
    outbound_overflow: QueueOverflowPolicy = QueueOverflowPolicy.REJECT_NEWEST


_QueueItem = TypeVar("_QueueItem")


class LocalQueueTransport(BaseTransport):
    """A bounded, same-event-loop transport with explicit overflow policy.

    External producers call ``publish_signal()`` and Medulla receives through
    the standard ``receive()`` operation. Echo/Medulla calls ``execute()`` and
    an external consumer awaits ``receive_action()``. All queue offers are
    non-blocking: full queues either reject the newest item with a structured
    error or discard exactly one oldest item, according to configuration.
    """

    def __init__(
        self,
        transport_id: str = "local",
        *,
        inbound_capacity: int = 100,
        outbound_capacity: int = 100,
        inbound_overflow: QueueOverflowPolicy = QueueOverflowPolicy.REJECT_NEWEST,
        outbound_overflow: QueueOverflowPolicy = QueueOverflowPolicy.REJECT_NEWEST,
    ) -> None:
        super().__init__(transport_id)
        self._inbound_capacity = self._capacity(inbound_capacity, "inbound_capacity")
        self._outbound_capacity = self._capacity(outbound_capacity, "outbound_capacity")
        self._inbound_overflow = self._policy(inbound_overflow, "inbound_overflow")
        self._outbound_overflow = self._policy(outbound_overflow, "outbound_overflow")
        self._inbound: asyncio.Queue[Signal] = asyncio.Queue(self._inbound_capacity)
        self._outbound: asyncio.Queue[Action] = asyncio.Queue(self._outbound_capacity)
        self._shutdown_event = asyncio.Event()
        self._inbound_rejected = 0
        self._inbound_dropped = 0
        self._outbound_rejected = 0
        self._outbound_dropped = 0
        self._inbound_discarded_on_stop = 0
        self._outbound_discarded_on_stop = 0

    @staticmethod
    def _capacity(value: Any, name: str) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _policy(value: Any, name: str) -> QueueOverflowPolicy:
        if not isinstance(value, QueueOverflowPolicy):
            raise ValueError(f"{name} must be a QueueOverflowPolicy")
        return value

    def status(self) -> LocalQueueTransportStatus:
        base = super().status()
        return LocalQueueTransportStatus(
            transport_id=base.transport_id,
            lifecycle=base.lifecycle,
            started_at=base.started_at,
            stopped_at=base.stopped_at,
            last_error=base.last_error,
            inbound_depth=self._inbound.qsize(),
            inbound_capacity=self._inbound_capacity,
            outbound_depth=self._outbound.qsize(),
            outbound_capacity=self._outbound_capacity,
            inbound_rejected=self._inbound_rejected,
            inbound_dropped=self._inbound_dropped,
            outbound_rejected=self._outbound_rejected,
            outbound_dropped=self._outbound_dropped,
            inbound_discarded_on_stop=self._inbound_discarded_on_stop,
            outbound_discarded_on_stop=self._outbound_discarded_on_stop,
            inbound_overflow=self._inbound_overflow,
            outbound_overflow=self._outbound_overflow,
        )

    async def publish_signal(
        self, signal: Signal | Mapping[str, Any]
    ) -> QueueOfferResult:
        """Offer one external Signal without waiting for queue capacity."""

        if self.status().lifecycle is not TransportLifecycle.RUNNING:
            raise self._state_error(TransportOperation.RECEIVE)
        try:
            if isinstance(signal, Signal):
                safe_signal = validate_inbound_signal(signal)
            else:
                safe_signal = external_signal_from_dict(signal)
        except ValueError as error:
            translated = TransportValidationError(
                str(error),
                transport_id=self.transport_id,
                operation=TransportOperation.VALIDATE,
                code=TransportErrorCode.INVALID_PAYLOAD,
            )
            self._last_error = translated.info
            raise translated from error
        return self._offer(
            self._inbound,
            safe_signal,
            item_id=safe_signal.id,
            policy=self._inbound_overflow,
            operation=TransportOperation.RECEIVE,
            direction="inbound",
        )

    async def receive_action(self) -> Action:
        """Wait for one Action accepted for an external local consumer."""

        if self.status().lifecycle is not TransportLifecycle.RUNNING:
            raise self._state_error(TransportOperation.EXECUTE)
        action = await self._wait_for_item(self._outbound, TransportOperation.EXECUTE)
        return validate_outbound_action(action)

    async def _start(self) -> None:
        # A new generation cannot expose stale work retained across a stop.
        self._inbound = asyncio.Queue(self._inbound_capacity)
        self._outbound = asyncio.Queue(self._outbound_capacity)
        self._shutdown_event = asyncio.Event()

    async def _stop(self) -> None:
        # Every pending waiter races this generation-specific event and wakes.
        self._shutdown_event.set()
        self._inbound_discarded_on_stop += self._drain(self._inbound)
        self._outbound_discarded_on_stop += self._drain(self._outbound)

    async def _receive(self) -> Signal:
        return await self._wait_for_item(self._inbound, TransportOperation.RECEIVE)

    async def _execute(self, action: Action) -> ActionDispatchResult:
        offer = self._offer(
            self._outbound,
            action,
            item_id=action.id,
            policy=self._outbound_overflow,
            operation=TransportOperation.EXECUTE,
            direction="outbound",
        )
        return ActionDispatchResult(
            transport_id=self.transport_id,
            action_id=action.id,
            state=ActionDispatchState.ACCEPTED,
            result={
                "queued": offer.accepted,
                "queue_depth": offer.depth,
                "dropped_id": offer.dropped_id,
            },
        )

    async def _health(self) -> TransportHealth:
        status = self.status()
        details = {
            "inbound_depth": status.inbound_depth,
            "inbound_capacity": status.inbound_capacity,
            "outbound_depth": status.outbound_depth,
            "outbound_capacity": status.outbound_capacity,
            "inbound_rejected": status.inbound_rejected,
            "inbound_dropped": status.inbound_dropped,
            "outbound_rejected": status.outbound_rejected,
            "outbound_dropped": status.outbound_dropped,
            "inbound_discarded_on_stop": status.inbound_discarded_on_stop,
            "outbound_discarded_on_stop": status.outbound_discarded_on_stop,
            "inbound_overflow": status.inbound_overflow.value,
            "outbound_overflow": status.outbound_overflow.value,
        }
        if status.lifecycle is not TransportLifecycle.RUNNING:
            return TransportHealth(
                transport_id=self.transport_id,
                lifecycle=status.lifecycle,
                state=TransportHealthState.UNAVAILABLE,
                message=f"transport is {status.lifecycle.value}",
                details=details,
            )
        saturated = (
            status.inbound_depth == status.inbound_capacity
            or status.outbound_depth == status.outbound_capacity
        )
        return TransportHealth(
            transport_id=self.transport_id,
            lifecycle=status.lifecycle,
            state=(
                TransportHealthState.DEGRADED
                if saturated
                else TransportHealthState.HEALTHY
            ),
            message="one or more queues are full" if saturated else None,
            details=details,
        )

    def _offer(
        self,
        queue: asyncio.Queue[_QueueItem],
        item: _QueueItem,
        *,
        item_id: str,
        policy: QueueOverflowPolicy,
        operation: TransportOperation,
        direction: str,
    ) -> QueueOfferResult:
        dropped_id: str | None = None
        if queue.full():
            if policy is QueueOverflowPolicy.REJECT_NEWEST:
                if direction == "inbound":
                    self._inbound_rejected += 1
                else:
                    self._outbound_rejected += 1
                error = LocalQueueFullError(
                    f"{direction} queue is full",
                    transport_id=self.transport_id,
                    operation=operation,
                    code=TransportErrorCode.QUEUE_FULL,
                    retryable=True,
                )
                self._last_error = error.info
                raise error
            dropped = queue.get_nowait()
            dropped_id = dropped.id
            if direction == "inbound":
                self._inbound_dropped += 1
            else:
                self._outbound_dropped += 1
        queue.put_nowait(item)
        return QueueOfferResult(
            accepted=True,
            depth=queue.qsize(),
            capacity=queue.maxsize,
            dropped_id=dropped_id,
        )

    async def _wait_for_item(
        self,
        queue: asyncio.Queue[_QueueItem],
        operation: TransportOperation,
    ) -> _QueueItem:
        shutdown_event = self._shutdown_event
        item_task = asyncio.create_task(queue.get())
        shutdown_task = asyncio.create_task(shutdown_event.wait())
        try:
            done, _ = await asyncio.wait(
                (item_task, shutdown_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if shutdown_task in done:
                raise self._state_error(operation)
            return item_task.result()
        finally:
            for task in (item_task, shutdown_task):
                if not task.done():
                    task.cancel()
            for task in (item_task, shutdown_task):
                with suppress(asyncio.CancelledError):
                    await task

    @staticmethod
    def _drain(queue: asyncio.Queue[Any]) -> int:
        discarded = 0
        while not queue.empty():
            queue.get_nowait()
            discarded += 1
        return discarded
