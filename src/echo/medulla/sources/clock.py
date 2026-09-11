"""Local clock observations normalized as Echo Signals."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from echo.core.signal import Signal
from echo.medulla.local import LocalQueueTransport, QueueOfferResult


class ClockSignalSource:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        source_id: str = "local-clock",
    ) -> None:
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("source_id must be a non-empty string")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._source_id = source_id

    def observe(self) -> Signal:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return Signal(
            type="time.observed",
            source=self._source_id,
            timestamp=now,
            payload={
                "iso8601": now.isoformat(),
                "unix_timestamp": now.timestamp(),
                "timezone": str(now.tzinfo),
            },
        )

    async def publish(self, transport: LocalQueueTransport) -> QueueOfferResult:
        return await transport.publish_signal(self.observe())
