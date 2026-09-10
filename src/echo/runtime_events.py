"""Transport-neutral, non-blocking subscriptions to live Runtime events."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from echo.core.inspection import json_safe
from echo.core.runtime_log import RuntimeEventType, RuntimeLogEvent


class RuntimeSubscriptionError(Exception):
    """Base class for subscription validation and lifecycle failures."""

    code = "runtime_subscription_error"

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


class InvalidSubscriptionError(RuntimeSubscriptionError):
    code = "invalid_subscription"


class SubscriptionClosedError(RuntimeSubscriptionError):
    code = "subscription_closed"


class RuntimeEventCategory(StrEnum):
    """Stable event families available to external runtime consumers."""

    SIGNAL_RECEIVED = "signal.received"
    SIGNAL_ROUTED = "signal.routed"
    TASK_LIFECYCLE = "task.lifecycle"
    ACTION_LIFECYCLE = "action.lifecycle"
    STATE_CHANGED = "state.changed"
    RUNTIME_CHANGED = "runtime.changed"
    ERROR = "error"
    LOGS = "logs"


class BackpressurePolicy(StrEnum):
    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"


def _copy(value: Any) -> Any:
    try:
        return deepcopy(value)
    except Exception:
        return json_safe(value)


@dataclass(slots=True, kw_only=True, frozen=True)
class RuntimeSubscriptionRequest:
    """Subscription filters and bounded-buffer behavior."""

    categories: Iterable[RuntimeEventCategory | str] = (
        RuntimeEventCategory.LOGS,
    )
    max_queue_size: int = 100
    backpressure: BackpressurePolicy | str = BackpressurePolicy.DROP_OLDEST

    def __post_init__(self) -> None:
        if isinstance(self.max_queue_size, bool) or not isinstance(
            self.max_queue_size, int
        ):
            raise InvalidSubscriptionError(
                "max_queue_size must be a positive integer"
            )
        if self.max_queue_size <= 0:
            raise InvalidSubscriptionError(
                "max_queue_size must be a positive integer"
            )
        raw_categories: Iterable[RuntimeEventCategory | str]
        if isinstance(self.categories, (str, RuntimeEventCategory)):
            raw_categories = (self.categories,)
        else:
            raw_categories = self.categories
        try:
            categories = frozenset(
                RuntimeEventCategory(category) for category in raw_categories
            )
        except (TypeError, ValueError) as error:
            raise InvalidSubscriptionError("unknown event category") from error
        if not categories:
            raise InvalidSubscriptionError("at least one event category is required")
        try:
            backpressure = BackpressurePolicy(self.backpressure)
        except (TypeError, ValueError) as error:
            raise InvalidSubscriptionError("unknown backpressure policy") from error
        object.__setattr__(self, "categories", categories)
        object.__setattr__(self, "backpressure", backpressure)


@dataclass(slots=True, kw_only=True, frozen=True)
class RuntimeSubscriptionEvent:
    """One detached event in Runtime publication order."""

    sequence: int
    category: RuntimeEventCategory
    event: RuntimeLogEvent

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "category": self.category.value,
            "event": json_safe(self.event.to_dict()),
        }


_CLOSED = object()


class RuntimeEventSubscription:
    """One consumer's isolated bounded event queue."""

    def __init__(
        self,
        request: RuntimeSubscriptionRequest,
        remove: Callable[[str], None],
    ) -> None:
        self.id = str(uuid4())
        self.request = request
        self._queue: asyncio.Queue[RuntimeSubscriptionEvent | object] = (
            asyncio.Queue(maxsize=request.max_queue_size)
        )
        self._remove: Callable[[str], None] | None = remove
        self._closed = False
        self._dropped_count = 0

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    async def get(self) -> RuntimeSubscriptionEvent:
        """Wait for the next event without involving Runtime execution."""

        if self._closed and self._queue.empty():
            raise SubscriptionClosedError("subscription is closed")
        item = await self._queue.get()
        self._queue.task_done()
        if item is _CLOSED:
            raise SubscriptionClosedError("subscription is closed")
        assert isinstance(item, RuntimeSubscriptionEvent)
        return item

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._remove is not None:
            self._remove(self.id)
            self._remove = None
        if self._queue.full():
            self._queue.get_nowait()
            self._queue.task_done()
        self._queue.put_nowait(_CLOSED)

    def __aiter__(self) -> RuntimeEventSubscription:
        return self

    async def __anext__(self) -> RuntimeSubscriptionEvent:
        try:
            return await self.get()
        except SubscriptionClosedError as error:
            raise StopAsyncIteration from error

    def _offer(self, event: RuntimeSubscriptionEvent) -> None:
        if self._closed:
            return
        if self._queue.full():
            self._dropped_count += 1
            if self.request.backpressure is BackpressurePolicy.DROP_NEWEST:
                return
            self._queue.get_nowait()
            self._queue.task_done()
        self._queue.put_nowait(event)


class RuntimeEventBroker:
    """Fan RuntimeLogEvents into isolated subscriber queues without awaiting."""

    def __init__(self) -> None:
        self._subscriptions: dict[str, RuntimeEventSubscription] = {}
        self._sequence = 0

    @property
    def subscriber_count(self) -> int:
        return len(self._subscriptions)

    def subscribe(
        self,
        request: RuntimeSubscriptionRequest | None = None,
    ) -> RuntimeEventSubscription:
        if request is not None and not isinstance(
            request, RuntimeSubscriptionRequest
        ):
            raise InvalidSubscriptionError(
                "subscribe requires a RuntimeSubscriptionRequest"
            )
        subscription = RuntimeEventSubscription(
            request or RuntimeSubscriptionRequest(),
            self._remove,
        )
        self._subscriptions[subscription.id] = subscription
        return subscription

    def publish(self, event: RuntimeLogEvent) -> None:
        """Offer an event to every match; never wait for or trust a consumer."""

        self._sequence += 1
        category = _event_category(event.event_type)
        for subscription in tuple(self._subscriptions.values()):
            categories = subscription.request.categories
            if (
                RuntimeEventCategory.LOGS not in categories
                and category not in categories
            ):
                continue
            try:
                subscription._offer(
                    RuntimeSubscriptionEvent(
                        sequence=self._sequence,
                        category=category,
                        event=_copy_log_event(event),
                    )
                )
            except Exception:
                # Subscription implementation failures cannot enter Runtime flow.
                self._subscriptions.pop(subscription.id, None)
                continue

    def _remove(self, subscription_id: str) -> None:
        self._subscriptions.pop(subscription_id, None)


def _copy_log_event(event: RuntimeLogEvent) -> RuntimeLogEvent:
    return RuntimeLogEvent(
        event_type=event.event_type,
        severity=event.severity,
        timestamp=event.timestamp,
        entity_id=event.entity_id,
        signal_id=event.signal_id,
        task_id=event.task_id,
        action_id=event.action_id,
        metadata=_copy(event.metadata),
    )


def _event_category(event_type: RuntimeEventType) -> RuntimeEventCategory:
    if event_type is RuntimeEventType.SIGNAL_RECEIVED:
        return RuntimeEventCategory.SIGNAL_RECEIVED
    if event_type is RuntimeEventType.SIGNAL_ROUTED:
        return RuntimeEventCategory.SIGNAL_ROUTED
    if event_type in {
        RuntimeEventType.TASK_CREATED,
        RuntimeEventType.TASK_STATUS_CHANGED,
    }:
        return RuntimeEventCategory.TASK_LIFECYCLE
    if event_type in {
        RuntimeEventType.ACTION_CREATED,
        RuntimeEventType.ACTION_EXECUTED,
    }:
        return RuntimeEventCategory.ACTION_LIFECYCLE
    if event_type is RuntimeEventType.STATE_CHANGED:
        return RuntimeEventCategory.STATE_CHANGED
    if event_type in {
        RuntimeEventType.RUNTIME_STARTED,
        RuntimeEventType.RUNTIME_QUIESCING,
        RuntimeEventType.RUNTIME_STOPPED,
        RuntimeEventType.RUNTIME_RESTARTED,
        RuntimeEventType.CONFIGURATION_RELOAD,
        RuntimeEventType.RECORDING_STARTED,
        RuntimeEventType.RECORDING_STOPPED,
    }:
        return RuntimeEventCategory.RUNTIME_CHANGED
    return RuntimeEventCategory.ERROR
