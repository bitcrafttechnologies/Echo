"""Versioned, durable JSON Lines records for Runtime Signal sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import math
import os
from os import PathLike, fspath
from typing import Any, TextIO
from uuid import uuid4

from echo.core.signal import Signal


RECORDING_FORMAT = "echo.signal-session"
RECORDING_FORMAT_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SignalRecordingError(Exception):
    """Base failure for Signal session recording."""


class RecordingSerializationError(SignalRecordingError, ValueError):
    """A value cannot be represented safely in a recording."""


class RecordingFormatError(SignalRecordingError, ValueError):
    """A stored record does not conform to the supported format."""


class RecorderStateError(SignalRecordingError, RuntimeError):
    """A recorder operation is invalid for its lifecycle state."""


class RecordKind(str, Enum):
    SESSION_STARTED = "session.started"
    SIGNAL = "signal"
    SESSION_ENDED = "session.ended"


def _required_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise RecordingFormatError(f"{path} must be a non-empty string")
    return value


def _timestamp(value: Any, path: str) -> datetime:
    if not isinstance(value, str):
        raise RecordingFormatError(f"{path} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise RecordingFormatError(f"{path} must be an ISO-8601 string") from error
    if parsed.tzinfo is None:
        raise RecordingFormatError(f"{path} must be timezone-aware")
    return parsed


def _safe_json(value: Any, path: str = "value") -> Any:
    """Return a detached JSON value, rejecting conversions and executable codecs."""

    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise RecordingSerializationError(f"{path} must be finite")
        return value
    if type(value) is list:
        return [_safe_json(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if type(value) is dict:
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise RecordingSerializationError(f"{path} keys must be strings")
            copied[key] = _safe_json(item, f"{path}.{key}")
        return copied
    raise RecordingSerializationError(
        f"{path} contains unsupported type "
        f"{type(value).__module__}.{type(value).__qualname__}"
    )


def _record_prefix(kind: RecordKind, session_id: str) -> dict[str, Any]:
    return {
        "format": RECORDING_FORMAT,
        "version": RECORDING_FORMAT_VERSION,
        "record_type": kind.value,
        "session_id": session_id,
    }


def _validate_prefix(data: dict[str, Any], kind: RecordKind | None = None) -> RecordKind:
    if data.get("format") != RECORDING_FORMAT:
        raise RecordingFormatError(f"unsupported recording format: {data.get('format')!r}")
    if data.get("version") != RECORDING_FORMAT_VERSION:
        raise RecordingFormatError(
            f"unsupported recording version: {data.get('version')!r}"
        )
    try:
        actual_kind = RecordKind(data.get("record_type"))
    except (TypeError, ValueError) as error:
        raise RecordingFormatError(
            f"unsupported record type: {data.get('record_type')!r}"
        ) from error
    if kind is not None and actual_kind is not kind:
        raise RecordingFormatError(f"expected {kind.value} record")
    _required_string(data.get("session_id"), "session_id")
    return actual_kind


@dataclass(slots=True, frozen=True, kw_only=True)
class RuntimeLinkage:
    """Optional Runtime-owned causal IDs associated with one Signal."""

    runtime_id: str | None = None
    entity_ids: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()
    action_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.runtime_id is not None:
            _required_string(self.runtime_id, "runtime_id")
        for name, value in (
            ("entity_ids", self.entity_ids),
            ("task_ids", self.task_ids),
            ("action_ids", self.action_ids),
        ):
            if not isinstance(value, tuple) or any(
                not isinstance(item, str) or not item for item in value
            ):
                raise RecordingFormatError(f"{name} must contain non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.runtime_id is not None:
            data["runtime_id"] = self.runtime_id
        if self.entity_ids:
            data["entity_ids"] = list(self.entity_ids)
        if self.task_ids:
            data["task_ids"] = list(self.task_ids)
        if self.action_ids:
            data["action_ids"] = list(self.action_ids)
        return data

    @classmethod
    def from_dict(cls, data: Any) -> RuntimeLinkage:
        if not isinstance(data, dict):
            raise RecordingFormatError("linkage must be an object")

        def ids(name: str) -> tuple[str, ...]:
            value = data.get(name, [])
            if not isinstance(value, list):
                raise RecordingFormatError(f"linkage.{name} must be an array")
            return tuple(value)

        runtime_id = data.get("runtime_id")
        if runtime_id is not None and not isinstance(runtime_id, str):
            raise RecordingFormatError("linkage.runtime_id must be a string")
        return cls(
            runtime_id=runtime_id,
            entity_ids=ids("entity_ids"),
            task_ids=ids("task_ids"),
            action_ids=ids("action_ids"),
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class SessionStartedRecord:
    session_id: str
    timestamp: datetime
    runtime_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_string(self.session_id, "session_id")
        if self.timestamp.tzinfo is None:
            raise RecordingFormatError("timestamp must be timezone-aware")
        if self.runtime_id is not None:
            _required_string(self.runtime_id, "runtime_id")
        metadata = _safe_json(self.metadata, "metadata")
        if not isinstance(metadata, dict):
            raise RecordingFormatError("metadata must be an object")
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        data = _record_prefix(RecordKind.SESSION_STARTED, self.session_id)
        data.update(
            {
                "timestamp": self.timestamp.isoformat(),
                "runtime_id": self.runtime_id,
                "metadata": _safe_json(self.metadata, "metadata"),
            }
        )
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionStartedRecord:
        _validate_prefix(data, RecordKind.SESSION_STARTED)
        runtime_id = data.get("runtime_id")
        if runtime_id is not None:
            _required_string(runtime_id, "runtime_id")
        metadata = _safe_json(data.get("metadata", {}), "metadata")
        if not isinstance(metadata, dict):
            raise RecordingFormatError("metadata must be an object")
        return cls(
            session_id=_required_string(data.get("session_id"), "session_id"),
            timestamp=_timestamp(data.get("timestamp"), "timestamp"),
            runtime_id=runtime_id,
            metadata=metadata,
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class SignalRecord:
    session_id: str
    recorded_at: datetime
    signal_id: str
    signal_type: str
    source: str
    timestamp: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    linkage: RuntimeLinkage | None = None

    def __post_init__(self) -> None:
        _required_string(self.session_id, "session_id")
        _required_string(self.signal_id, "signal_id")
        _required_string(self.signal_type, "signal_type")
        _required_string(self.source, "source")
        if self.recorded_at.tzinfo is None:
            raise RecordingFormatError("recorded_at must be timezone-aware")
        if self.timestamp.tzinfo is None:
            raise RecordingFormatError("timestamp must be timezone-aware")
        payload = _safe_json(self.payload, "signal.payload")
        metadata = _safe_json(self.metadata, "signal.metadata")
        if not isinstance(payload, dict) or not isinstance(metadata, dict):
            raise RecordingFormatError("signal payload and metadata must be objects")
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "metadata", metadata)

    @classmethod
    def from_signal(
        cls,
        signal: Signal,
        *,
        session_id: str,
        recorded_at: datetime | None = None,
        linkage: RuntimeLinkage | None = None,
    ) -> SignalRecord:
        return cls(
            session_id=session_id,
            recorded_at=recorded_at or _utc_now(),
            signal_id=signal.id,
            signal_type=signal.type,
            source=signal.source,
            timestamp=signal.timestamp,
            payload=_safe_json(signal.payload, "signal.payload"),
            metadata=_safe_json(signal.metadata, "signal.metadata"),
            linkage=linkage,
        )

    def to_dict(self) -> dict[str, Any]:
        data = _record_prefix(RecordKind.SIGNAL, self.session_id)
        data.update(
            {
                "recorded_at": self.recorded_at.isoformat(),
                "signal": {
                    "id": self.signal_id,
                    "type": self.signal_type,
                    "source": self.source,
                    "timestamp": self.timestamp.isoformat(),
                    "payload": _safe_json(self.payload, "signal.payload"),
                    "metadata": _safe_json(self.metadata, "signal.metadata"),
                },
            }
        )
        if self.linkage is not None:
            data["linkage"] = self.linkage.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SignalRecord:
        _validate_prefix(data, RecordKind.SIGNAL)
        signal = data.get("signal")
        if not isinstance(signal, dict):
            raise RecordingFormatError("signal must be an object")
        payload = _safe_json(signal.get("payload", {}), "signal.payload")
        metadata = _safe_json(signal.get("metadata", {}), "signal.metadata")
        if not isinstance(payload, dict) or not isinstance(metadata, dict):
            raise RecordingFormatError("signal payload and metadata must be objects")
        linkage_data = data.get("linkage")
        return cls(
            session_id=_required_string(data.get("session_id"), "session_id"),
            recorded_at=_timestamp(data.get("recorded_at"), "recorded_at"),
            signal_id=_required_string(signal.get("id"), "signal.id"),
            signal_type=_required_string(signal.get("type"), "signal.type"),
            source=_required_string(signal.get("source"), "signal.source"),
            timestamp=_timestamp(signal.get("timestamp"), "signal.timestamp"),
            payload=payload,
            metadata=metadata,
            linkage=(
                None
                if linkage_data is None
                else RuntimeLinkage.from_dict(linkage_data)
            ),
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class SessionEndedRecord:
    session_id: str
    timestamp: datetime
    signal_count: int
    reason: str = "stopped"

    def __post_init__(self) -> None:
        _required_string(self.session_id, "session_id")
        _required_string(self.reason, "reason")
        if self.timestamp.tzinfo is None:
            raise RecordingFormatError("timestamp must be timezone-aware")
        if type(self.signal_count) is not int or self.signal_count < 0:
            raise RecordingFormatError("signal_count must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        data = _record_prefix(RecordKind.SESSION_ENDED, self.session_id)
        data.update(
            {
                "timestamp": self.timestamp.isoformat(),
                "signal_count": self.signal_count,
                "reason": self.reason,
            }
        )
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionEndedRecord:
        _validate_prefix(data, RecordKind.SESSION_ENDED)
        signal_count = data.get("signal_count")
        if type(signal_count) is not int or signal_count < 0:
            raise RecordingFormatError("signal_count must be a non-negative integer")
        return cls(
            session_id=_required_string(data.get("session_id"), "session_id"),
            timestamp=_timestamp(data.get("timestamp"), "timestamp"),
            signal_count=signal_count,
            reason=_required_string(data.get("reason"), "reason"),
        )


SessionRecord = SessionStartedRecord | SignalRecord | SessionEndedRecord


def decode_record(data: Any) -> SessionRecord:
    """Validate and decode one already-parsed JSON Lines object."""

    if not isinstance(data, dict):
        raise RecordingFormatError("record must be a JSON object")
    kind = _validate_prefix(data)
    if kind is RecordKind.SESSION_STARTED:
        return SessionStartedRecord.from_dict(data)
    if kind is RecordKind.SIGNAL:
        return SignalRecord.from_dict(data)
    return SessionEndedRecord.from_dict(data)


@dataclass(slots=True, frozen=True, kw_only=True)
class RecordedSession:
    start: SessionStartedRecord
    signals: tuple[SignalRecord, ...]
    end: SessionEndedRecord | None

    @property
    def completed(self) -> bool:
        return self.end is not None


def read_recorded_session(path: str | PathLike[str]) -> RecordedSession:
    """Read and validate a session without injecting any Signal into a Runtime."""

    records: list[SessionRecord] = []
    with open(fspath(path), encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                records.append(decode_record(data))
            except (json.JSONDecodeError, SignalRecordingError) as error:
                raise RecordingFormatError(
                    f"invalid record on line {line_number}: {error}"
                ) from error
    if not records or not isinstance(records[0], SessionStartedRecord):
        raise RecordingFormatError("recording must begin with session.started")
    if any(isinstance(record, SessionStartedRecord) for record in records[1:]):
        raise RecordingFormatError(
            "recording contains more than one session.started record"
        )
    endings = [record for record in records if isinstance(record, SessionEndedRecord)]
    if len(endings) > 1 or (endings and records[-1] is not endings[0]):
        raise RecordingFormatError("session.ended must occur once at the end")
    start = records[0]
    signals = tuple(
        record for record in records[1:] if isinstance(record, SignalRecord)
    )
    end = endings[0] if endings else None
    if any(record.session_id != start.session_id for record in records):
        raise RecordingFormatError("all records must use the same session_id")
    if end is not None and end.signal_count != len(signals):
        raise RecordingFormatError("session.ended signal_count does not match Signal records")
    return RecordedSession(start=start, signals=signals, end=end)


class JsonLinesSignalRecorder:
    """Write one crash-tolerant, human-inspectable Signal session file."""

    def __init__(
        self,
        path: str | PathLike[str],
        *,
        runtime_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        durable: bool = True,
    ) -> None:
        self.path = fspath(path)
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("recording path must not be empty")
        self.session_id = session_id or str(uuid4())
        _required_string(self.session_id, "session_id")
        if runtime_id is not None:
            _required_string(runtime_id, "runtime_id")
        self.runtime_id = runtime_id
        self.metadata = _safe_json({} if metadata is None else metadata, "metadata")
        if not isinstance(self.metadata, dict):
            raise RecordingSerializationError("metadata must be an object")
        if not isinstance(durable, bool):
            raise ValueError("durable must be a boolean")
        self.durable = durable
        self._stream: TextIO | None = None
        self._signal_count = 0
        self._ended = False

    def start(self, *, timestamp: datetime | None = None) -> SessionStartedRecord:
        if self._stream is not None or self._ended:
            raise RecorderStateError("recording has already started")
        record = SessionStartedRecord(
            session_id=self.session_id,
            timestamp=timestamp or _utc_now(),
            runtime_id=self.runtime_id,
            metadata=self.metadata,
        )
        # Exclusive creation prevents accidentally mixing or overwriting sessions.
        self._stream = open(self.path, "x", encoding="utf-8", newline="\n")
        try:
            self._write(record)
        except BaseException:
            self._stream.close()
            self._stream = None
            raise
        return record

    def record_signal(
        self,
        signal: Signal,
        *,
        recorded_at: datetime | None = None,
        linkage: RuntimeLinkage | None = None,
    ) -> SignalRecord:
        if self._stream is None or self._ended:
            raise RecorderStateError("recording is not active")
        record = SignalRecord.from_signal(
            signal,
            session_id=self.session_id,
            recorded_at=recorded_at,
            linkage=linkage,
        )
        self._write(record)
        self._signal_count += 1
        return record

    def stop(
        self,
        *,
        timestamp: datetime | None = None,
        reason: str = "stopped",
    ) -> SessionEndedRecord:
        if self._stream is None or self._ended:
            raise RecorderStateError("recording is not active")
        record = SessionEndedRecord(
            session_id=self.session_id,
            timestamp=timestamp or _utc_now(),
            signal_count=self._signal_count,
            reason=_required_string(reason, "reason"),
        )
        try:
            self._write(record)
        finally:
            self._stream.close()
            self._stream = None
            self._ended = True
        return record

    def _write(self, record: SessionRecord) -> None:
        assert self._stream is not None
        line = json.dumps(
            record.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
        self._stream.write(line + "\n")
        self._stream.flush()
        if self.durable:
            os.fsync(self._stream.fileno())

    def __enter__(self) -> JsonLinesSignalRecorder:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._stream is not None and not self._ended:
            self.stop(reason="error" if exc_type is not None else "stopped")
