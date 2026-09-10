"""Priority scheduling for inbound signals."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import IntEnum
from itertools import count

from echo.core.signal import Signal


class SignalPriority(IntEnum):
    CRITICAL = 0
    HIGH = 10
    NORMAL = 20
    BACKGROUND = 30
    PERIODIC = 40


@dataclass(order=True, slots=True)
class _ScheduledSignal:
    priority: int
    sequence: int
    signal: Signal = field(compare=False)


class Scheduler:
    """An async, stable priority queue of Signals."""

    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue[_ScheduledSignal] = asyncio.PriorityQueue()
        self._sequence = count()

    async def schedule(
        self,
        signal: Signal,
        priority: SignalPriority | int | str = SignalPriority.NORMAL,
    ) -> None:
        await self._queue.put(
            _ScheduledSignal(
                priority=self._normalize_priority(priority),
                sequence=next(self._sequence),
                signal=signal,
            )
        )

    async def next_signal(self) -> Signal:
        return (await self._queue.get()).signal

    def task_done(self) -> None:
        self._queue.task_done()

    def empty(self) -> bool:
        return self._queue.empty()

    def __len__(self) -> int:
        return self._queue.qsize()

    def inspect(self) -> dict[str, object]:
        """Return a read-only summary without consuming queued Signals."""

        scheduled = sorted(tuple(self._queue._queue))
        return {
            "queue_size": len(scheduled),
            "empty": not scheduled,
            "queued_signals": [
                {
                    **item.signal.to_dict(),
                    "priority": item.priority,
                    "sequence": item.sequence,
                }
                for item in scheduled
            ],
        }

    @staticmethod
    def _normalize_priority(priority: SignalPriority | int | str) -> int:
        if isinstance(priority, str):
            try:
                return int(SignalPriority[priority.upper()])
            except KeyError as error:
                raise ValueError(f"unknown signal priority: {priority}") from error
        return int(priority)
