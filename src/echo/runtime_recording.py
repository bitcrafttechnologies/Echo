"""Asynchronous Runtime integration for durable Signal session recording."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from os import PathLike, fspath
from typing import Any

from echo.core.recording import (
    RECORDING_FORMAT,
    RECORDING_FORMAT_VERSION,
    JsonLinesSignalRecorder,
    RuntimeLinkage,
)
from echo.core.runtime import Runtime
from echo.core.runtime_log import RuntimeEventType
from echo.core.signal import Signal


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RecordingState(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(slots=True, frozen=True, kw_only=True)
class RecordingStatus:
    state: RecordingState
    runtime_id: str
    session_id: str | None = None
    path: str | None = None
    format: str = RECORDING_FORMAT
    version: int = RECORDING_FORMAT_VERSION
    started_at: datetime | None = None
    ended_at: datetime | None = None
    metadata: dict[str, Any] | None = None
    signals_enqueued: int = 0
    signals_written: int = 0
    signals_dropped: int = 0
    last_error: dict[str, str] | None = None

    @property
    def active(self) -> bool:
        return self.state in {
            RecordingState.STARTING,
            RecordingState.RECORDING,
            RecordingState.STOPPING,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "active": self.active,
            "runtime_id": self.runtime_id,
            "session_id": self.session_id,
            "path": self.path,
            "format": self.format,
            "version": self.version,
            "started_at": (
                self.started_at.isoformat() if self.started_at is not None else None
            ),
            "ended_at": (
                self.ended_at.isoformat() if self.ended_at is not None else None
            ),
            "metadata": deepcopy(self.metadata),
            "signals_enqueued": self.signals_enqueued,
            "signals_written": self.signals_written,
            "signals_dropped": self.signals_dropped,
            "last_error": (
                self.last_error.copy() if self.last_error is not None else None
            ),
        }


_STOP = object()


class RuntimeSessionRecorder:
    """Queue Runtime Signals and perform durable writes away from dispatch."""

    def __init__(
        self,
        runtime: Runtime,
        path: str | PathLike[str],
        *,
        metadata: Mapping[str, Any] | None = None,
        queue_capacity: int = 1024,
        durable: bool = True,
    ) -> None:
        if not isinstance(runtime, Runtime):
            raise TypeError("runtime must be a Runtime")
        if (
            isinstance(queue_capacity, bool)
            or not isinstance(queue_capacity, int)
            or queue_capacity < 1
        ):
            raise ValueError("queue_capacity must be a positive integer")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        self.runtime = runtime
        self.path = fspath(path)
        self._metadata = dict(metadata or {})
        self._writer = JsonLinesSignalRecorder(
            self.path,
            runtime_id=runtime.id,
            metadata=self._metadata,
            durable=durable,
        )
        self._queue: asyncio.Queue[tuple[Signal, RuntimeLinkage] | object] = (
            asyncio.Queue(maxsize=queue_capacity)
        )
        self._state = RecordingState.IDLE
        self._worker: asyncio.Task[None] | None = None
        self._started_at: datetime | None = None
        self._ended_at: datetime | None = None
        self._signals_enqueued = 0
        self._signals_written = 0
        self._signals_dropped = 0
        self._last_error: dict[str, str] | None = None

    @property
    def status(self) -> RecordingStatus:
        return RecordingStatus(
            state=self._state,
            runtime_id=self.runtime.id,
            session_id=self._writer.session_id,
            path=self.path,
            started_at=self._started_at,
            ended_at=self._ended_at,
            metadata=deepcopy(self._metadata),
            signals_enqueued=self._signals_enqueued,
            signals_written=self._signals_written,
            signals_dropped=self._signals_dropped,
            last_error=(
                self._last_error.copy() if self._last_error is not None else None
            ),
        )

    async def start(self) -> RecordingStatus:
        if self._state is not RecordingState.IDLE:
            raise RuntimeError("recording has already started")
        self._state = RecordingState.STARTING
        self._started_at = _utc_now()
        try:
            await asyncio.to_thread(self._writer.start, timestamp=self._started_at)
        except Exception as error:
            self._fail("signal_recording.start", error)
            self._ended_at = _utc_now()
            raise
        self._state = RecordingState.RECORDING
        try:
            self.runtime.set_signal_capture_sink(self)
        except Exception as error:
            try:
                await asyncio.to_thread(self._writer.stop, reason="error")
            except Exception:
                pass
            self._fail("signal_recording.start", error)
            self._ended_at = _utc_now()
            raise
        self._worker = asyncio.create_task(
            self._write_loop(),
            name=f"echo-recording-{self._writer.session_id}",
        )
        self.runtime.report_recording_lifecycle(
            RuntimeEventType.RECORDING_STARTED,
            self.status.to_dict(),
        )
        return self.status

    def capture(self, signal: Signal, *, linkage: RuntimeLinkage) -> None:
        """Enqueue without waiting; called synchronously from Runtime dispatch."""

        if self._state is not RecordingState.RECORDING:
            return
        try:
            self._queue.put_nowait((signal, linkage))
        except asyncio.QueueFull:
            self._signals_dropped += 1
            error = RuntimeError("recording queue is full; Signal was dropped")
            self._last_error = self._error_data(error)
            self.runtime.report_error(
                "signal_recording.queue",
                error,
                signal_id=signal.id,
            )
        else:
            self._signals_enqueued += 1

    async def stop(self, *, reason: str = "stopped") -> RecordingStatus:
        if self._state in {RecordingState.IDLE, RecordingState.STOPPED}:
            raise RuntimeError("recording is not active")
        self.runtime.clear_signal_capture_sink(self)
        failed = self._state is RecordingState.FAILED
        if not failed:
            self._state = RecordingState.STOPPING
        if self._worker is not None and not self._worker.done():
            await self._queue.put(_STOP)
            await self._worker
        try:
            await asyncio.to_thread(self._writer.stop, reason=reason)
        except Exception as error:
            self._fail("signal_recording.stop", error)
            failed = True
        self._ended_at = _utc_now()
        if not failed and self._state is not RecordingState.FAILED:
            self._state = RecordingState.STOPPED
        self.runtime.report_recording_lifecycle(
            RuntimeEventType.RECORDING_STOPPED,
            self.status.to_dict(),
        )
        return self.status

    async def _write_loop(self) -> None:
        while True:
            item = await self._queue.get()
            signal_id: str | None = None
            try:
                if item is _STOP:
                    return
                signal, linkage = item
                signal_id = signal.id
                await asyncio.to_thread(
                    self._writer.record_signal,
                    signal,
                    linkage=linkage,
                )
                self._signals_written += 1
            except Exception as error:
                if item is not _STOP:
                    self._signals_dropped += 1
                self._fail(
                    "signal_recording.write",
                    error,
                    signal_id=signal_id,
                )
                self.runtime.clear_signal_capture_sink(self)
                self._discard_pending()
                return
            finally:
                self._queue.task_done()

    def _discard_pending(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if item is not _STOP:
                self._signals_dropped += 1
            self._queue.task_done()

    def _fail(
        self,
        operation: str,
        error: BaseException,
        *,
        signal_id: str | None = None,
    ) -> None:
        self._state = RecordingState.FAILED
        self._last_error = self._error_data(error)
        self.runtime.report_error(operation, error, signal_id=signal_id)

    @staticmethod
    def _error_data(error: BaseException) -> dict[str, str]:
        return {"type": type(error).__name__, "message": str(error)}
