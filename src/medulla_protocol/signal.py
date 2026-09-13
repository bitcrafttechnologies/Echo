"""Signal declarations and normalized standalone-node event envelopes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from medulla_protocol.action import _json, _optional_text, _text, _timestamp
from medulla_protocol.manifest import MedullaNodeSignal, NodeSignal


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True, frozen=True, kw_only=True)
class AdapterEvent:
    signal_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    resource_id: str | None = None
    correlation_id: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        _text(self.signal_type, "adapter_event.signal_type")
        _text(self.id, "adapter_event.id")
        _optional_text(self.resource_id, "adapter_event.resource_id")
        _optional_text(self.correlation_id, "adapter_event.correlation_id")
        _timestamp(self.timestamp, "adapter_event.timestamp")
        payload = _json(self.payload, "adapter_event.payload")
        if type(payload) is not dict:
            raise ValueError("adapter_event.payload must be an object")
        object.__setattr__(self, "payload", payload)


@dataclass(slots=True, frozen=True, kw_only=True)
class NodeSignalEnvelope:
    node_id: str
    provider_id: str
    adapter_id: str
    signal_type: str
    sequence: int
    payload: dict[str, Any] = field(default_factory=dict)
    resource_id: str | None = None
    correlation_id: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        for name, value in (
            ("node_id", self.node_id), ("provider_id", self.provider_id),
            ("adapter_id", self.adapter_id), ("signal_type", self.signal_type),
            ("id", self.id),
        ):
            _text(value, f"signal.{name}")
        _optional_text(self.resource_id, "signal.resource_id")
        _optional_text(self.correlation_id, "signal.correlation_id")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("signal.sequence must be a positive integer")
        _timestamp(self.timestamp, "signal.timestamp")
        payload = _json(self.payload, "signal.payload")
        if type(payload) is not dict:
            raise ValueError("signal.payload must be an object")
        object.__setattr__(self, "payload", payload)

    def to_dict(self) -> dict[str, Any]:
        """Return the Echo-compatible Signal shape with explicit provenance."""

        return {
            "id": self.id,
            "type": self.signal_type,
            "source": self.node_id,
            "timestamp": self.timestamp.isoformat(),
            "payload": _json(self.payload, "signal.payload"),
            "metadata": {
                "node_id": self.node_id,
                "provider_id": self.provider_id,
                "adapter_id": self.adapter_id,
                "resource_id": self.resource_id,
                "signal_type": self.signal_type,
                "correlation_id": self.correlation_id,
                "sequence": self.sequence,
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NodeSignalEnvelope:
        allowed = {"id", "type", "source", "timestamp", "payload", "metadata"}
        if type(data) is not dict or set(data) - allowed:
            raise ValueError("signal envelope must be a closed ordinary object")
        metadata = data.get("metadata")
        if type(metadata) is not dict:
            raise ValueError("signal.metadata must be an object")
        expected = {"node_id", "provider_id", "adapter_id", "resource_id", "signal_type", "correlation_id", "sequence"}
        if set(metadata) != expected:
            raise ValueError("signal.metadata has an invalid provenance shape")
        timestamp = data.get("timestamp")
        if type(timestamp) is not str:
            raise ValueError("signal.timestamp must be an ISO-8601 string")
        if data.get("type") != metadata.get("signal_type") or data.get("source") != metadata.get("node_id"):
            raise ValueError("signal envelope does not match its provenance")
        return cls(
            id=data.get("id"), signal_type=data.get("type"), node_id=metadata.get("node_id"),
            provider_id=metadata.get("provider_id"), adapter_id=metadata.get("adapter_id"),
            resource_id=metadata.get("resource_id"), correlation_id=metadata.get("correlation_id"),
            sequence=metadata.get("sequence"), payload=data.get("payload", {}),
            timestamp=datetime.fromisoformat(timestamp),
        )


NormalizedSignal = NodeSignalEnvelope

__all__ = [
    "AdapterEvent", "MedullaNodeSignal", "NodeSignal", "NodeSignalEnvelope",
    "NormalizedSignal",
]
