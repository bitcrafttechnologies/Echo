"""Signals: timestamped descriptions of things that happened."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Mapping
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True, kw_only=True)
class Signal:
    """A serializable event delivered to Entity handlers.

    Domain-specific signals may subclass this class and expose typed
    constructors while storing their wire representation in ``payload``.
    """

    type: str
    id: str = field(default_factory=lambda: str(uuid4()))
    source: str = "internal"
    timestamp: datetime = field(default_factory=_utc_now)
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.type:
            raise ValueError("signal type must not be empty")
        if self.timestamp.tzinfo is None:
            raise ValueError("signal timestamp must be timezone-aware")
        self.payload = dict(self.payload)
        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        """Return the stable transport representation of this signal."""

        return {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload.copy(),
            "metadata": self.metadata.copy(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Signal:
        """Restore a signal from its transport representation."""

        timestamp = data.get("timestamp")
        if not isinstance(timestamp, str):
            raise ValueError("signal timestamp must be an ISO-8601 string")
        return cls(
            id=str(data.get("id", uuid4())),
            type=str(data["type"]),
            source=str(data.get("source", "internal")),
            timestamp=datetime.fromisoformat(timestamp),
            payload=dict(data.get("payload", {})),
            metadata=dict(data.get("metadata", {})),
        )

    def to_json(self) -> str:
        """Serialize the signal to JSON."""

        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, value: str) -> Signal:
        """Restore a signal from JSON."""

        data = json.loads(value)
        if not isinstance(data, dict):
            raise ValueError("serialized signal must contain an object")
        return cls.from_dict(data)
