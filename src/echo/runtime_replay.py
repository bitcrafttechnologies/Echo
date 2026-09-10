"""Safe replay of recorded Signals through the normal Runtime entry path."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from echo.core.recording import RecordedSession, SignalRecord, read_recorded_session
from echo.core.runtime import Runtime
from echo.core.scheduler import SignalPriority
from echo.core.signal import Signal


REPLAY_METADATA_KEY = "echo_replay"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReplayMode(StrEnum):
    """Supported replay execution modes."""

    SIGNAL = "signal"
    SEQUENTIAL = "sequential"
    STEP = "step"


class ReplaySafetyPolicy(StrEnum):
    """Action safety policy applied while replayed Signals are dispatched.

    Phase 8C intentionally exposes only the safe policy. Runtime handlers may
    produce Action intents, but replay never invokes an external Action executor.
    """

    RECORD_ONLY = "record_only"


class ReplayState(StrEnum):
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ReplayError(RuntimeError):
    """A replay lifecycle operation failed."""


class ReplayStateError(ReplayError):
    """A replay operation is invalid for the current state or mode."""


@dataclass(slots=True, frozen=True, kw_only=True)
class ReplayedSignal:
    original_signal_id: str
    replayed_signal_id: str
    original_timestamp: datetime
    received_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_signal_id": self.original_signal_id,
            "replayed_signal_id": self.replayed_signal_id,
            "original_timestamp": self.original_timestamp.isoformat(),
            "received_at": self.received_at.isoformat(),
        }


@dataclass(slots=True, frozen=True, kw_only=True)
class ReplayStatus:
    replay_id: str
    runtime_id: str
    recording_path: str
    recording_session_id: str
    mode: ReplayMode
    safety_policy: ReplaySafetyPolicy
    state: ReplayState
    total_signals: int
    next_index: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    replayed_signals: tuple[ReplayedSignal, ...] = ()
    error: dict[str, str] | None = None

    @property
    def remaining_signals(self) -> int:
        return self.total_signals - self.next_index

    def to_dict(self) -> dict[str, Any]:
        return {
            "replay_id": self.replay_id,
            "runtime_id": self.runtime_id,
            "recording_path": self.recording_path,
            "recording_session_id": self.recording_session_id,
            "mode": self.mode.value,
            "safety_policy": self.safety_policy.value,
            "state": self.state.value,
            "total_signals": self.total_signals,
            "next_index": self.next_index,
            "remaining_signals": self.remaining_signals,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "replayed_signals": [item.to_dict() for item in self.replayed_signals],
            "error": deepcopy(self.error),
        }


class RuntimeSignalReplay:
    """One loaded recording replayed serially into a Runtime."""

    def __init__(
        self,
        runtime: Runtime,
        recording_path: str,
        session: RecordedSession,
        *,
        mode: ReplayMode,
        signal_id: str | None = None,
        safety_policy: ReplaySafetyPolicy = ReplaySafetyPolicy.RECORD_ONLY,
    ) -> None:
        if not isinstance(runtime, Runtime):
            raise TypeError("runtime must be a Runtime")
        if safety_policy is not ReplaySafetyPolicy.RECORD_ONLY:
            raise ValueError("unsupported replay safety policy")
        records = session.signals
        if mode is ReplayMode.SIGNAL:
            if not signal_id:
                raise ValueError("signal_id is required for signal replay")
            matches = tuple(
                record for record in records if record.signal_id == signal_id
            )
            if not matches:
                raise ValueError(f"recorded Signal not found: {signal_id}")
            if len(matches) > 1:
                raise ValueError(f"recording contains duplicate Signal id: {signal_id}")
            records = matches
        elif signal_id is not None:
            raise ValueError("signal_id is only valid for signal replay")

        self._runtime = runtime
        self._session = session
        self._records: tuple[SignalRecord, ...] = records
        self._status = ReplayStatus(
            replay_id=str(uuid4()),
            runtime_id=runtime.id,
            recording_path=str(Path(recording_path)),
            recording_session_id=session.start.session_id,
            mode=mode,
            safety_policy=safety_policy,
            state=ReplayState.READY,
            total_signals=len(records),
        )

    @classmethod
    def load(
        cls,
        runtime: Runtime,
        recording_path: str,
        *,
        mode: ReplayMode,
        signal_id: str | None = None,
        safety_policy: ReplaySafetyPolicy = ReplaySafetyPolicy.RECORD_ONLY,
    ) -> RuntimeSignalReplay:
        return cls(
            runtime,
            recording_path,
            read_recorded_session(recording_path),
            mode=mode,
            signal_id=signal_id,
            safety_policy=safety_policy,
        )

    @property
    def status(self) -> ReplayStatus:
        return self._status

    async def run(self) -> ReplayStatus:
        """Run a one-Signal or sequential replay to completion."""

        if self._status.mode is ReplayMode.STEP:
            raise ReplayStateError("step replay must be advanced with step()")
        self._require_ready()
        if not self._records:
            self._complete()
            return self._status
        while self._status.next_index < len(self._records):
            await self._emit_next()
        return self._status

    async def step(self) -> ReplayStatus:
        """Inject exactly one remaining Signal for a step replay."""

        if self._status.mode is not ReplayMode.STEP:
            raise ReplayStateError(
                "only step replay can be advanced one Signal at a time"
            )
        if self._status.state is ReplayState.COMPLETED:
            raise ReplayStateError("replay is already complete")
        if self._status.state is ReplayState.FAILED:
            raise ReplayStateError("replay has failed")
        if not self._records:
            self._complete()
            return self._status
        await self._emit_next()
        return self._status

    def _require_ready(self) -> None:
        if self._status.state is not ReplayState.READY:
            raise ReplayStateError("replay has already started")

    async def _emit_next(self) -> None:
        index = self._status.next_index
        record = self._records[index]
        received_at = _utc_now()
        signal = Signal(
            type=record.signal_type,
            source=record.source,
            timestamp=received_at,
            payload=deepcopy(record.payload),
            metadata={
                **deepcopy(record.metadata),
                REPLAY_METADATA_KEY: {
                    "replayed": True,
                    "replay_id": self._status.replay_id,
                    "recording_session_id": record.session_id,
                    "original_signal_id": record.signal_id,
                    "original_timestamp": record.timestamp.isoformat(),
                    "recorded_at": record.recorded_at.isoformat(),
                    "safety_policy": self._status.safety_policy.value,
                },
            },
        )
        self._status = replace(
            self._status,
            state=ReplayState.RUNNING,
            started_at=self._status.started_at or received_at,
        )
        try:
            # This is deliberately the same entry path used for live Signals.
            await self._runtime.emit(signal, priority=SignalPriority.NORMAL)
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            self._status = replace(
                self._status,
                state=ReplayState.FAILED,
                completed_at=_utc_now(),
                error={"type": type(error).__name__, "message": str(error)},
            )
            self._runtime.report_error(
                "signal_replay.emit", error, signal_id=signal.id
            )
            raise

        replayed = ReplayedSignal(
            original_signal_id=record.signal_id,
            replayed_signal_id=signal.id,
            original_timestamp=record.timestamp,
            received_at=received_at,
        )
        next_index = index + 1
        self._status = replace(
            self._status,
            next_index=next_index,
            replayed_signals=(*self._status.replayed_signals, replayed),
        )
        if next_index == len(self._records):
            self._complete()

    def _complete(self) -> None:
        now = _utc_now()
        self._status = replace(
            self._status,
            state=ReplayState.COMPLETED,
            started_at=self._status.started_at or now,
            completed_at=now,
        )
