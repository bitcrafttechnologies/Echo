"""Bounded, in-memory history for processed Signals."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from echo.core.signal import Signal


def _copy_mapping(value: dict[str, Any]) -> dict[str, Any]:
    try:
        return deepcopy(value)
    except Exception:
        return value.copy()


@dataclass(slots=True, kw_only=True, frozen=True)
class SignalRoutingResult:
    """Summary of how a Runtime processed one Signal."""

    status: str = "pending"
    entity_ids: tuple[str, ...] = ()
    handler_count: int = 0
    task_ids: tuple[str, ...] = ()
    task_statuses: dict[str, str] = field(default_factory=dict)
    error: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "entity_ids": list(self.entity_ids),
            "handler_count": self.handler_count,
            "task_ids": list(self.task_ids),
            "task_statuses": self.task_statuses.copy(),
            "error": self.error.copy() if self.error is not None else None,
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class SignalHistoryEntry:
    """Safe snapshot of a Signal and its routing result."""

    id: str
    type: str
    source: str
    timestamp: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    routing_result: SignalRoutingResult = field(default_factory=SignalRoutingResult)

    @classmethod
    def from_signal(cls, signal: Signal) -> SignalHistoryEntry:
        return cls(
            id=signal.id,
            type=signal.type,
            source=signal.source,
            timestamp=signal.timestamp,
            payload=_copy_mapping(signal.payload),
            metadata=_copy_mapping(signal.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "payload": _copy_mapping(self.payload),
            "metadata": _copy_mapping(self.metadata),
            "routing_result": self.routing_result.to_dict(),
        }


class SignalHistory:
    """Insertion-ordered Signal snapshots with oldest-first eviction."""

    def __init__(self, max_size: int = 1000) -> None:
        if isinstance(max_size, bool) or not isinstance(max_size, int) or max_size <= 0:
            raise ValueError("signal history max_size must be a positive integer")
        self.max_size = max_size
        self._entries: OrderedDict[str, SignalHistoryEntry] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def resize(self, max_size: int) -> None:
        """Change retention in place, evicting oldest entries if reduced."""

        if isinstance(max_size, bool) or not isinstance(max_size, int) or max_size <= 0:
            raise ValueError("signal history max_size must be a positive integer")
        self.max_size = max_size
        while len(self._entries) > self.max_size:
            self._entries.popitem(last=False)

    def record(self, signal: Signal) -> SignalHistoryEntry:
        """Snapshot a Signal, evicting the oldest entry when full."""

        entry = SignalHistoryEntry.from_signal(signal)
        self._entries.pop(signal.id, None)
        self._entries[signal.id] = entry
        while len(self._entries) > self.max_size:
            self._entries.popitem(last=False)
        return self._copy_entry(entry)

    def set_routing_result(
        self,
        signal_id: str,
        routing_result: SignalRoutingResult,
    ) -> SignalHistoryEntry | None:
        entry = self._entries.get(signal_id)
        if entry is None:
            return None
        updated = replace(entry, routing_result=self._copy_routing(routing_result))
        self._entries[signal_id] = updated
        return self._copy_entry(updated)

    def latest(self, limit: int | None = None) -> tuple[SignalHistoryEntry, ...]:
        """Return newest entries first."""

        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
                raise ValueError("signal history limit must be a non-negative integer")
            if limit == 0:
                return ()
        entries = reversed(self._entries.values())
        if limit is not None:
            entries = iter(list(entries)[:limit])
        return tuple(self._copy_entry(entry) for entry in entries)

    def get(self, signal_id: str) -> SignalHistoryEntry | None:
        """Return one entry, or None when the ID is unknown or evicted."""

        entry = self._entries.get(signal_id)
        return self._copy_entry(entry) if entry is not None else None

    def filter(
        self,
        *,
        signal_type: str | None = None,
        source: str | None = None,
    ) -> tuple[SignalHistoryEntry, ...]:
        """Return matching entries newest first."""

        return tuple(
            self._copy_entry(entry)
            for entry in reversed(self._entries.values())
            if (signal_type is None or entry.type == signal_type)
            and (source is None or entry.source == source)
        )

    @staticmethod
    def _copy_routing(result: SignalRoutingResult) -> SignalRoutingResult:
        return replace(
            result,
            task_statuses=result.task_statuses.copy(),
            error=result.error.copy() if result.error is not None else None,
        )

    @classmethod
    def _copy_entry(cls, entry: SignalHistoryEntry) -> SignalHistoryEntry:
        return replace(
            entry,
            payload=_copy_mapping(entry.payload),
            metadata=_copy_mapping(entry.metadata),
            routing_result=cls._copy_routing(entry.routing_result),
        )
