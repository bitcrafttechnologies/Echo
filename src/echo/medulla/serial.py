"""Optional framed serial transport for embedded Medulla devices."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import math
import struct
from typing import Any, Protocol
import zlib

from echo.core.action import Action
from echo.core.signal import Signal
from echo.medulla.transport import (
    ActionDispatchResult,
    ActionDispatchState,
    BaseTransport,
    TransportErrorCode,
    TransportHealth,
    TransportHealthState,
    TransportLifecycle,
    TransportOperation,
    TransportOperationError,
    TransportStateError,
    TransportStatus,
    TransportValidationError,
)
from echo.medulla.wire import (
    WIRE_VERSION,
    WireMessage,
    WireMessageType,
    WireProtocolError,
    action_message,
    decode_wire_message,
    encode_wire_message,
    signal_from_message,
)


SERIAL_FRAME_VERSION = 1
SERIAL_FRAME_DELIMITER = 0x7E
SERIAL_FRAME_ESCAPE = 0x7D
SERIAL_FRAME_ESCAPE_XOR = 0x20
DEFAULT_SERIAL_MAX_PAYLOAD_BYTES = 16_384
_SERIAL_HEADER_SIZE = 3
_SERIAL_CRC_SIZE = 4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SerialFrameError(ValueError):
    """A rejected serial frame that is safe to expose as transport status."""


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_number(value: Any, name: str) -> float:
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be positive and finite")
    return float(value)


def _escape_frame_body(body: bytes) -> bytes:
    escaped = bytearray()
    for value in body:
        if value in (SERIAL_FRAME_DELIMITER, SERIAL_FRAME_ESCAPE):
            escaped.append(SERIAL_FRAME_ESCAPE)
            escaped.append(value ^ SERIAL_FRAME_ESCAPE_XOR)
        else:
            escaped.append(value)
    return bytes(escaped)


def encode_serial_frame(
    payload: bytes,
    *,
    max_payload_bytes: int = DEFAULT_SERIAL_MAX_PAYLOAD_BYTES,
) -> bytes:
    """Encode one payload as a delimiter/escape/length/CRC protected frame."""

    _positive_int(max_payload_bytes, "max_payload_bytes")
    if max_payload_bytes > 65_535:
        raise ValueError("max_payload_bytes cannot exceed 65535")
    if type(payload) is not bytes:
        raise TypeError("serial frame payload must be bytes")
    if len(payload) > max_payload_bytes:
        raise SerialFrameError("serial frame payload exceeds size limit")
    header = bytes((SERIAL_FRAME_VERSION,)) + struct.pack(">H", len(payload))
    protected = header + payload
    crc = struct.pack(">I", zlib.crc32(protected) & 0xFFFFFFFF)
    delimiter = bytes((SERIAL_FRAME_DELIMITER,))
    return delimiter + _escape_frame_body(protected + crc) + delimiter


def encode_serial_message(
    message: WireMessage,
    *,
    max_payload_bytes: int = DEFAULT_SERIAL_MAX_PAYLOAD_BYTES,
) -> bytes:
    if not isinstance(message, WireMessage):
        raise TypeError("message must be a WireMessage")
    return encode_serial_frame(
        encode_wire_message(message).encode("utf-8"),
        max_payload_bytes=max_payload_bytes,
    )


class SerialFrameParser:
    """Incremental resynchronizing parser with no serial-library dependency."""

    def __init__(
        self,
        *,
        max_payload_bytes: int = DEFAULT_SERIAL_MAX_PAYLOAD_BYTES,
    ) -> None:
        self.max_payload_bytes = _positive_int(
            max_payload_bytes, "max_payload_bytes"
        )
        if self.max_payload_bytes > 65_535:
            raise ValueError("max_payload_bytes cannot exceed 65535")
        self.frames_accepted = 0
        self.frames_rejected = 0
        self.bytes_discarded = 0
        self.last_error: str | None = None
        self._in_frame = False
        self._encoded = bytearray()
        self._max_encoded_body = 2 * (
            _SERIAL_HEADER_SIZE + self.max_payload_bytes + _SERIAL_CRC_SIZE
        )

    def feed(self, data: bytes) -> tuple[bytes, ...]:
        if type(data) is not bytes:
            raise TypeError("serial parser input must be bytes")
        payloads: list[bytes] = []
        for value in data:
            if value == SERIAL_FRAME_DELIMITER:
                if self._in_frame and self._encoded:
                    try:
                        payloads.append(self._decode_candidate(bytes(self._encoded)))
                    except SerialFrameError as error:
                        self._reject(str(error))
                    else:
                        self.frames_accepted += 1
                self._encoded.clear()
                self._in_frame = True
                continue
            if not self._in_frame:
                self.bytes_discarded += 1
                continue
            self._encoded.append(value)
            if len(self._encoded) > self._max_encoded_body:
                self.bytes_discarded += len(self._encoded)
                self._encoded.clear()
                self._in_frame = False
                self._reject("encoded serial frame exceeds size limit")
        return tuple(payloads)

    def reset(self) -> None:
        self._in_frame = False
        self._encoded.clear()

    def _decode_candidate(self, encoded: bytes) -> bytes:
        body = bytearray()
        index = 0
        while index < len(encoded):
            value = encoded[index]
            if value != SERIAL_FRAME_ESCAPE:
                body.append(value)
                index += 1
                continue
            index += 1
            if index >= len(encoded):
                raise SerialFrameError("serial frame ends with an escape byte")
            escaped = encoded[index] ^ SERIAL_FRAME_ESCAPE_XOR
            if escaped not in (SERIAL_FRAME_DELIMITER, SERIAL_FRAME_ESCAPE):
                raise SerialFrameError("serial frame contains an invalid escape sequence")
            body.append(escaped)
            index += 1

        minimum = _SERIAL_HEADER_SIZE + _SERIAL_CRC_SIZE
        if len(body) < minimum:
            raise SerialFrameError("serial frame is shorter than its header and CRC")
        version = body[0]
        if version != SERIAL_FRAME_VERSION:
            raise SerialFrameError(f"unsupported serial frame version: {version}")
        payload_length = struct.unpack(">H", body[1:3])[0]
        if payload_length > self.max_payload_bytes:
            raise SerialFrameError("serial frame declares an oversized payload")
        expected_size = _SERIAL_HEADER_SIZE + payload_length + _SERIAL_CRC_SIZE
        if len(body) != expected_size:
            raise SerialFrameError("serial frame length does not match its payload")
        protected = bytes(body[:-_SERIAL_CRC_SIZE])
        expected_crc = struct.unpack(">I", body[-_SERIAL_CRC_SIZE:])[0]
        actual_crc = zlib.crc32(protected) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise SerialFrameError("serial frame CRC does not match")
        return bytes(body[_SERIAL_HEADER_SIZE:-_SERIAL_CRC_SIZE])

    def _reject(self, message: str) -> None:
        self.frames_rejected += 1
        self.last_error = message


@dataclass(slots=True, frozen=True, kw_only=True)
class SerialPortConfig:
    port: str
    baudrate: int
    read_timeout: float = 0.2
    write_timeout: float = 1.0

    def __post_init__(self) -> None:
        if type(self.port) is not str or not self.port:
            raise ValueError("port must be a non-empty serial device path or name")
        if "\0" in self.port:
            raise ValueError("port must not contain a null byte")
        _positive_int(self.baudrate, "baudrate")
        _positive_number(self.read_timeout, "read_timeout")
        _positive_number(self.write_timeout, "write_timeout")


class SerialConnectionState(StrEnum):
    STOPPED = "stopped"
    OPENING = "opening"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class SerialLike(Protocol):
    def read(self, size: int) -> bytes: ...
    def write(self, data: bytes) -> int: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...


SerialFactory = Callable[..., SerialLike]


@dataclass(slots=True, frozen=True, kw_only=True)
class SerialTransportStatus(TransportStatus):
    port: str = ""
    baudrate: int = 0
    connection_state: SerialConnectionState = SerialConnectionState.STOPPED
    connected: bool = False
    connected_at: datetime | None = None
    last_connected_at: datetime | None = None
    last_disconnected_at: datetime | None = None
    connections_established: int = 0
    reconnect_attempts: int = 0
    frames_received: int = 0
    frames_sent: int = 0
    frames_rejected: int = 0
    noise_bytes_discarded: int = 0
    inbound_depth: int = 0
    inbound_capacity: int = 1
    outbound_depth: int = 0
    outbound_capacity: int = 1
    max_payload_bytes: int = DEFAULT_SERIAL_MAX_PAYLOAD_BYTES
    frame_version: int = SERIAL_FRAME_VERSION
    wire_version: int = WIRE_VERSION
    last_frame_error: str | None = None


class SerialTransport(BaseTransport):
    """Reconnectable serial adapter that isolates framing and payload noise."""

    def __init__(
        self,
        config: SerialPortConfig,
        *,
        transport_id: str = "serial",
        inbound_capacity: int = 100,
        outbound_capacity: int = 100,
        read_chunk_size: int = 256,
        max_payload_bytes: int = DEFAULT_SERIAL_MAX_PAYLOAD_BYTES,
        reconnect_initial_delay: float = 0.1,
        reconnect_max_delay: float = 5.0,
        serial_factory: SerialFactory | None = None,
    ) -> None:
        super().__init__(transport_id)
        if not isinstance(config, SerialPortConfig):
            raise TypeError("config must be a SerialPortConfig")
        self._config = config
        self._inbound_capacity = _positive_int(inbound_capacity, "inbound_capacity")
        self._outbound_capacity = _positive_int(outbound_capacity, "outbound_capacity")
        self._read_chunk_size = _positive_int(read_chunk_size, "read_chunk_size")
        self._max_payload_bytes = _positive_int(max_payload_bytes, "max_payload_bytes")
        if self._max_payload_bytes > 65_535:
            raise ValueError("max_payload_bytes cannot exceed 65535")
        self._reconnect_initial_delay = _positive_number(
            reconnect_initial_delay, "reconnect_initial_delay"
        )
        self._reconnect_max_delay = _positive_number(
            reconnect_max_delay, "reconnect_max_delay"
        )
        if self._reconnect_max_delay < self._reconnect_initial_delay:
            raise ValueError("reconnect_max_delay must be >= reconnect_initial_delay")
        self._serial_factory = serial_factory
        self._parser = SerialFrameParser(max_payload_bytes=self._max_payload_bytes)
        self._inbound: asyncio.Queue[Signal] = asyncio.Queue(self._inbound_capacity)
        self._outbound: asyncio.Queue[tuple[WireMessage, bytes]] = asyncio.Queue(
            self._outbound_capacity
        )
        self._sending: tuple[WireMessage, bytes] | None = None
        self._retry: tuple[WireMessage, bytes] | None = None
        self._shutdown_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._manager_task: asyncio.Task[None] | None = None
        self._active_serial: SerialLike | None = None
        self._connection_state = SerialConnectionState.STOPPED
        self._connected_at: datetime | None = None
        self._last_connected_at: datetime | None = None
        self._last_disconnected_at: datetime | None = None
        self._connections_established = 0
        self._reconnect_attempts = 0
        self._frames_received = 0
        self._frames_sent = 0
        self._payloads_rejected = 0
        self._last_frame_error: str | None = None

    def status(self) -> SerialTransportStatus:
        base = super().status()
        return SerialTransportStatus(
            transport_id=base.transport_id,
            lifecycle=base.lifecycle,
            started_at=base.started_at,
            stopped_at=base.stopped_at,
            last_error=base.last_error,
            port=self._config.port,
            baudrate=self._config.baudrate,
            connection_state=self._connection_state,
            connected=self._connected_event.is_set(),
            connected_at=self._connected_at,
            last_connected_at=self._last_connected_at,
            last_disconnected_at=self._last_disconnected_at,
            connections_established=self._connections_established,
            reconnect_attempts=self._reconnect_attempts,
            frames_received=self._frames_received,
            frames_sent=self._frames_sent,
            frames_rejected=self._parser.frames_rejected + self._payloads_rejected,
            noise_bytes_discarded=self._parser.bytes_discarded,
            inbound_depth=self._inbound.qsize(),
            inbound_capacity=self._inbound_capacity,
            outbound_depth=(
                self._outbound.qsize()
                + int(self._sending is not None)
                + int(self._retry is not None)
            ),
            outbound_capacity=self._outbound_capacity,
            max_payload_bytes=self._max_payload_bytes,
            last_frame_error=self._last_frame_error or self._parser.last_error,
        )

    async def wait_connected(self, *, timeout: float | None = None) -> bool:
        if self.status().lifecycle is not TransportLifecycle.RUNNING:
            return False
        try:
            if timeout is None:
                await self._connected_event.wait()
            else:
                await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return True

    async def _start(self) -> None:
        self._parser = SerialFrameParser(max_payload_bytes=self._max_payload_bytes)
        self._inbound = asyncio.Queue(self._inbound_capacity)
        self._outbound = asyncio.Queue(self._outbound_capacity)
        self._sending = None
        self._retry = None
        self._shutdown_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._connection_state = SerialConnectionState.OPENING
        self._last_frame_error = None
        self._manager_task = asyncio.create_task(
            self._connection_manager(),
            name=f"echo-medulla-serial-{self.transport_id}",
        )

    async def _stop(self) -> None:
        self._shutdown_event.set()
        self._connected_event.clear()
        task = self._manager_task
        self._manager_task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._drain(self._inbound)
        self._drain(self._outbound)
        self._sending = None
        self._retry = None
        self._active_serial = None
        self._connected_at = None
        self._connection_state = SerialConnectionState.STOPPED

    async def _receive(self) -> Signal:
        item = asyncio.create_task(self._inbound.get())
        stopped = asyncio.create_task(self._shutdown_event.wait())
        try:
            done, _ = await asyncio.wait(
                {item, stopped}, return_when=asyncio.FIRST_COMPLETED
            )
            if item in done:
                return item.result()
            raise TransportStateError(
                "transport stopped while receiving",
                transport_id=self.transport_id,
                operation=TransportOperation.RECEIVE,
                code=TransportErrorCode.INVALID_STATE,
            )
        finally:
            for task in (item, stopped):
                if not task.done():
                    task.cancel()
            await asyncio.gather(item, stopped, return_exceptions=True)

    async def _execute(self, action: Action) -> ActionDispatchResult:
        message = action_message(action)
        frame = encode_serial_message(
            message, max_payload_bytes=self._max_payload_bytes
        )
        pending_depth = (
            self._outbound.qsize()
            + int(self._sending is not None)
            + int(self._retry is not None)
        )
        if pending_depth >= self._outbound_capacity:
            raise TransportOperationError(
                "outbound serial queue is full",
                transport_id=self.transport_id,
                operation=TransportOperation.EXECUTE,
                code=TransportErrorCode.QUEUE_FULL,
                retryable=True,
            )
        self._outbound.put_nowait((message, frame))
        return ActionDispatchResult(
            transport_id=self.transport_id,
            action_id=action.id,
            state=ActionDispatchState.ACCEPTED,
            external_id=message.id,
            result={
                "queued": True,
                "connected": self._connected_event.is_set(),
                "frame_bytes": len(frame),
            },
        )

    async def _health(self) -> TransportHealth:
        status = self.status()
        details = {
            "port": status.port,
            "baudrate": status.baudrate,
            "connection_state": status.connection_state.value,
            "connected": status.connected,
            "connections_established": status.connections_established,
            "reconnect_attempts": status.reconnect_attempts,
            "frames_received": status.frames_received,
            "frames_sent": status.frames_sent,
            "frames_rejected": status.frames_rejected,
            "noise_bytes_discarded": status.noise_bytes_discarded,
            "inbound_depth": status.inbound_depth,
            "inbound_capacity": status.inbound_capacity,
            "outbound_depth": status.outbound_depth,
            "outbound_capacity": status.outbound_capacity,
            "max_payload_bytes": status.max_payload_bytes,
            "frame_version": status.frame_version,
            "wire_version": status.wire_version,
        }
        if status.lifecycle is not TransportLifecycle.RUNNING:
            return TransportHealth(
                transport_id=self.transport_id,
                lifecycle=status.lifecycle,
                state=TransportHealthState.UNAVAILABLE,
                message=f"transport is {status.lifecycle.value}",
                details=details,
            )
        saturated = (
            status.inbound_depth >= status.inbound_capacity
            or status.outbound_depth >= status.outbound_capacity
        )
        if status.connected and not saturated:
            return TransportHealth(
                transport_id=self.transport_id,
                lifecycle=status.lifecycle,
                state=TransportHealthState.HEALTHY,
                details=details,
            )
        return TransportHealth(
            transport_id=self.transport_id,
            lifecycle=status.lifecycle,
            state=TransportHealthState.DEGRADED,
            message=(
                "one or more serial queues are full"
                if status.connected
                else "serial device is disconnected; reconnect loop is active"
            ),
            details=details,
        )

    async def _connection_manager(self) -> None:
        delay = self._reconnect_initial_delay
        attempted = False
        while not self._shutdown_event.is_set():
            self._connection_state = (
                SerialConnectionState.RECONNECTING
                if attempted
                else SerialConnectionState.OPENING
            )
            if attempted:
                self._reconnect_attempts += 1
            attempted = True
            connection: SerialLike | None = None
            try:
                connection = await self._open_connection()
                self._active_serial = connection
                self._on_connected()
                delay = self._reconnect_initial_delay
                await self._run_connection(connection)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._record_connection_error(error)
            finally:
                if self._connected_event.is_set():
                    self._last_disconnected_at = _utc_now()
                self._connected_event.clear()
                self._connected_at = None
                self._active_serial = None
                self._parser.reset()
                if connection is not None:
                    with suppress(Exception):
                        await asyncio.to_thread(connection.close)
                if not self._shutdown_event.is_set():
                    self._connection_state = SerialConnectionState.RECONNECTING
            if self._shutdown_event.is_set():
                break
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, self._reconnect_max_delay)

    async def _open_connection(self) -> SerialLike:
        factory = self._serial_factory or self._default_serial_factory()
        return await asyncio.to_thread(
            factory,
            port=self._config.port,
            baudrate=self._config.baudrate,
            timeout=self._config.read_timeout,
            write_timeout=self._config.write_timeout,
        )

    @staticmethod
    def _default_serial_factory() -> SerialFactory:
        try:
            from serial import Serial
        except ImportError as error:
            raise RuntimeError(
                "SerialTransport requires the 'serial' optional dependency"
            ) from error
        return Serial

    def _on_connected(self) -> None:
        now = _utc_now()
        self._connected_at = now
        self._last_connected_at = now
        self._connections_established += 1
        self._connection_state = SerialConnectionState.CONNECTED
        self._connected_event.set()

    async def _run_connection(self, connection: SerialLike) -> None:
        reader = asyncio.create_task(self._read_loop(connection))
        writer = asyncio.create_task(self._write_loop(connection))
        try:
            done, _ = await asyncio.wait(
                {reader, writer}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task.cancelled():
                    raise asyncio.CancelledError
                exception = task.exception()
                if exception is not None:
                    raise exception
            raise ConnectionError("serial connection closed")
        finally:
            for task in (reader, writer):
                if not task.done():
                    task.cancel()
            await asyncio.gather(reader, writer, return_exceptions=True)

    async def _read_loop(self, connection: SerialLike) -> None:
        while True:
            chunk = await asyncio.to_thread(connection.read, self._read_chunk_size)
            if type(chunk) is not bytes:
                raise OSError("serial read must return bytes")
            if not chunk:
                continue
            rejected_before = self._parser.frames_rejected
            payloads = self._parser.feed(chunk)
            if self._parser.frames_rejected != rejected_before:
                self._record_frame_error(
                    self._parser.last_error or "malformed serial frame"
                )
            for payload in payloads:
                self._frames_received += 1
                try:
                    message = decode_wire_message(
                        payload, max_bytes=self._max_payload_bytes
                    )
                    if message.type is not WireMessageType.SIGNAL:
                        raise WireProtocolError(
                            "unexpected_message_type",
                            "serial ingress accepts only signal messages",
                            message_id=message.id,
                        )
                    signal = signal_from_message(message)
                    self._inbound.put_nowait(signal)
                except (WireProtocolError, asyncio.QueueFull) as error:
                    self._payloads_rejected += 1
                    self._record_frame_error(
                        "inbound serial Signal queue is full"
                        if isinstance(error, asyncio.QueueFull)
                        else str(error)
                    )

    async def _write_loop(self, connection: SerialLike) -> None:
        while True:
            if self._retry is not None:
                outbound = self._retry
                self._retry = None
            else:
                outbound = await self._outbound.get()
            self._sending = outbound
            try:
                await asyncio.to_thread(self._write_all, connection, outbound[1])
            except asyncio.CancelledError:
                if not self._shutdown_event.is_set():
                    self._retry = outbound
                raise
            except Exception:
                self._retry = outbound
                raise
            else:
                self._frames_sent += 1
            finally:
                self._sending = None

    @staticmethod
    def _write_all(connection: SerialLike, frame: bytes) -> None:
        offset = 0
        while offset < len(frame):
            written = connection.write(frame[offset:])
            if type(written) is not int or written <= 0:
                raise OSError("serial write made no progress")
            if written > len(frame) - offset:
                raise OSError("serial write reported an invalid byte count")
            offset += written
        connection.flush()

    def _record_frame_error(self, message: str) -> None:
        self._last_frame_error = message
        translated = TransportValidationError(
            message,
            transport_id=self.transport_id,
            operation=TransportOperation.VALIDATE,
            code=TransportErrorCode.INVALID_PAYLOAD,
        )
        self._last_error = translated.info

    def _record_connection_error(self, error: Exception) -> None:
        translated = TransportOperationError(
            f"serial connection failed: {str(error) or type(error).__name__}",
            transport_id=self.transport_id,
            operation=TransportOperation.RECEIVE,
            code=TransportErrorCode.CONNECTION_FAILED,
            retryable=True,
        )
        self._last_error = translated.info

    @staticmethod
    def _drain(queue: asyncio.Queue[Any]) -> None:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return
