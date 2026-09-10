"""Safe, cancellable replay of recorded Signals through Runtime.emit()."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
import math
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
    """Recorded Signal selection mode retained from Phase 8C."""

    SIGNAL = "signal"
    SEQUENTIAL = "sequential"
    STEP = "step"  # Compatibility alias for a manual sequential replay.


class ReplayTiming(StrEnum):
    """Timing applied between selected recorded Signals."""

    REALTIME = "realtime"
    ACCELERATED = "accelerated"
    IMMEDIATE = "immediate"
    MANUAL_STEP = "manual_step"


class ReplaySafetyPolicy(StrEnum):
    """Action safety policy applied while replayed Signals are dispatched."""

    RECORD_ONLY = "record_only"


class ReplayState(StrEnum):
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ReplayError(RuntimeError):
    """A replay lifecycle operation failed."""


class ReplayStateError(ReplayError):
    """A replay operation is invalid for the current state or timing mode."""


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
    timing: ReplayTiming
    multiplier: float | None
    safety_policy: ReplaySafetyPolicy
    state: ReplayState
    total_signals: int
    next_index: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancellation_requested: bool = False
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
            "timing": self.timing.value,
            "multiplier": self.multiplier,
            "safety_policy": self.safety_policy.value,
            "state": self.state.value,
            "total_signals": self.total_signals,
            "next_index": self.next_index,
            "remaining_signals": self.remaining_signals,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "cancellation_requested": self.cancellation_requested,
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
        timing: ReplayTiming = ReplayTiming.IMMEDIATE,
        multiplier: float | None = None,
        safety_policy: ReplaySafetyPolicy = ReplaySafetyPolicy.RECORD_ONLY,
    ) -> None:
        if not isinstance(runtime, Runtime):
            raise TypeError("runtime must be a Runtime")
        if safety_policy is not ReplaySafetyPolicy.RECORD_ONLY:
            raise ValueError("unsupported replay safety policy")
        if mode is ReplayMode.STEP:
            mode = ReplayMode.SEQUENTIAL
            if timing not in {
                ReplayTiming.IMMEDIATE,
                ReplayTiming.MANUAL_STEP,
            }:
                raise ValueError("legacy step mode cannot specify another timing mode")
            timing = ReplayTiming.MANUAL_STEP
        if mode is ReplayMode.SIGNAL and timing is not ReplayTiming.IMMEDIATE:
            raise ValueError("single-Signal replay uses immediate timing")
        if timing is ReplayTiming.ACCELERATED:
            if (
                isinstance(multiplier, bool)
                or not isinstance(multiplier, (int, float))
                or not math.isfinite(float(multiplier))
                or multiplier <= 0
            ):
                raise ValueError(
                    "accelerated replay multiplier must be finite and positive"
                )
            multiplier = float(multiplier)
        elif multiplier is not None:
            raise ValueError("multiplier is only valid for accelerated replay")

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

        if timing in {ReplayTiming.REALTIME, ReplayTiming.ACCELERATED}:
            for previous, current in zip(records, records[1:]):
                if current.timestamp < previous.timestamp:
                    raise ValueError(
                        "timed replay requires non-decreasing Signal timestamps"
                    )

        self._runtime = runtime
        self._records: tuple[SignalRecord, ...] = records
        self._cancel_event = asyncio.Event()
        self._timeline_anchor: float | None = None
        self._status = ReplayStatus(
            replay_id=str(uuid4()),
            runtime_id=runtime.id,
            recording_path=str(Path(recording_path)),
            recording_session_id=session.start.session_id,
            mode=mode,
            timing=timing,
            multiplier=multiplier,
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
        timing: ReplayTiming = ReplayTiming.IMMEDIATE,
        multiplier: float | None = None,
        safety_policy: ReplaySafetyPolicy = ReplaySafetyPolicy.RECORD_ONLY,
    ) -> RuntimeSignalReplay:
        return cls(
            runtime,
            recording_path,
            read_recorded_session(recording_path),
            mode=mode,
            signal_id=signal_id,
            timing=timing,
            multiplier=multiplier,
            safety_policy=safety_policy,
        )

    @property
    def status(self) -> ReplayStatus:
        return self._status

    async def run(self) -> ReplayStatus:
        """Run an automatic replay, honoring its relative timing policy."""

        if self._status.timing is ReplayTiming.MANUAL_STEP:
            raise ReplayStateError("manual replay must be advanced with step()")
        self._require_ready()
        if not self._records:
            self._complete()
            return self._status
        self._timeline_anchor = asyncio.get_running_loop().time()
        while self._status.next_index < len(self._records):
            if self._cancel_event.is_set():
                self._cancel()
                break
            if not await self._wait_for_next_signal():
                self._cancel()
                break
            await self._emit_next()
        return self._status

    async def step(self) -> ReplayStatus:
        """Inject exactly one remaining Signal for a manual replay."""

        if self._status.timing is not ReplayTiming.MANUAL_STEP:
            raise ReplayStateError(
                "only manual replay can be advanced one Signal at a time"
            )
        self._require_active()
        if self._cancel_event.is_set():
            self._cancel()
            return self._status
        if not self._records:
            self._complete()
            return self._status
        await self._emit_next()
        return self._status

    def request_cancel(self) -> ReplayStatus:
        """Request cooperative cancellation at the next safe dispatch boundary."""

        if self._status.state in {
            ReplayState.COMPLETED,
            ReplayState.CANCELLED,
            ReplayState.FAILED,
        }:
            raise ReplayStateError("replay is already terminal")
        self._cancel_event.set()
        self._status = replace(self._status, cancellation_requested=True)
        if (
            self._status.state is ReplayState.READY
            or self._status.timing is ReplayTiming.MANUAL_STEP
        ):
            self._cancel()
        return self._status

    def _require_ready(self) -> None:
        if self._status.state is not ReplayState.READY:
            raise ReplayStateError("replay has already started")

    def _require_active(self) -> None:
        if self._status.state is ReplayState.COMPLETED:
            raise ReplayStateError("replay is already complete")
        if self._status.state is ReplayState.CANCELLED:
            raise ReplayStateError("replay is cancelled")
        if self._status.state is ReplayState.FAILED:
            raise ReplayStateError("replay has failed")

    async def _wait_for_next_signal(self) -> bool:
        index = self._status.next_index
        if index == 0 or self._status.timing is ReplayTiming.IMMEDIATE:
            return not self._cancel_event.is_set()
        first = self._records[0].timestamp
        current = self._records[index].timestamp
        recorded_offset = (current - first).total_seconds()
        divisor = self._status.multiplier or 1.0
        assert self._timeline_anchor is not None
        deadline = self._timeline_anchor + recorded_offset / divisor
        delay = deadline - asyncio.get_running_loop().time()
        if delay <= 0:
            return not self._cancel_event.is_set()
        try:
            await asyncio.wait_for(self._cancel_event.wait(), timeout=delay)
        except TimeoutError:
            return True
        return False

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
                    "timing": self._status.timing.value,
                    "multiplier": self._status.multiplier,
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

    def _cancel(self) -> None:
        self._status = replace(
            self._status,
            state=ReplayState.CANCELLED,
            completed_at=_utc_now(),
            cancellation_requested=True,
        )
