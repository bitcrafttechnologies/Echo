"""Reconnectable WebSocket client transport for Medulla."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import inspect
import math
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

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
)
from echo.medulla.wire import (
    DEFAULT_MAX_MESSAGE_BYTES,
    WIRE_VERSION,
    WireMessage,
    WireMessageType,
    WireProtocolError,
    action_message,
    decode_wire_message,
    encode_wire_message,
    make_wire_message,
    signal_from_message,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WebSocketConnectionState(StrEnum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class WebSocketLike(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...


Connector = Callable[..., Any]
AuthenticationHeaders = Callable[
    [], Mapping[str, str] | Awaitable[Mapping[str, str]]
]
ManifestHandler = Callable[[WireMessage], Any | Awaitable[Any]]
NodeDisconnectedHandler = Callable[[str], Any | Awaitable[Any]]


@dataclass(slots=True, frozen=True, kw_only=True)
class WebSocketTransportStatus(TransportStatus):
    endpoint: str = ""
    connection_state: WebSocketConnectionState = WebSocketConnectionState.STOPPED
    connected: bool = False
    connected_at: datetime | None = None
    last_connected_at: datetime | None = None
    last_disconnected_at: datetime | None = None
    connections_established: int = 0
    reconnect_attempts: int = 0
    messages_received: int = 0
    messages_sent: int = 0
    messages_rejected: int = 0
    inbound_depth: int = 0
    inbound_capacity: int = 1
    outbound_depth: int = 0
    outbound_capacity: int = 1
    peer_node_id: str | None = None
    wire_version: int = WIRE_VERSION


class WebSocketTransport(BaseTransport):
    """Client-side Medulla transport with contained automatic reconnects.

    Starting this transport starts its connection manager; it does not require
    the remote endpoint to be available. Authentication is deliberately only
    a header-provider hook. Pairing, identity, trust, and discovery remain
    outside Phase 9C.
    """

    def __init__(
        self,
        uri: str,
        *,
        transport_id: str = "websocket",
        node_id: str = "echo",
        inbound_capacity: int = 100,
        outbound_capacity: int = 100,
        reconnect_initial_delay: float = 0.1,
        reconnect_max_delay: float = 5.0,
        connect_timeout: float = 10.0,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        authentication_headers: AuthenticationHeaders | None = None,
        manifest_handler: ManifestHandler | None = None,
        node_disconnected_handler: NodeDisconnectedHandler | None = None,
        connector: Connector | None = None,
    ) -> None:
        super().__init__(transport_id)
        self._uri, self._endpoint = self._validate_uri(uri)
        self._node_id = self._required_string(node_id, "node_id")
        self._inbound_capacity = self._positive_int(inbound_capacity, "inbound_capacity")
        self._outbound_capacity = self._positive_int(outbound_capacity, "outbound_capacity")
        self._reconnect_initial_delay = self._positive_number(
            reconnect_initial_delay, "reconnect_initial_delay"
        )
        self._reconnect_max_delay = self._positive_number(
            reconnect_max_delay, "reconnect_max_delay"
        )
        if self._reconnect_max_delay < self._reconnect_initial_delay:
            raise ValueError("reconnect_max_delay must be >= reconnect_initial_delay")
        self._connect_timeout = self._positive_number(connect_timeout, "connect_timeout")
        self._max_message_bytes = self._positive_int(max_message_bytes, "max_message_bytes")
        self._authentication_headers = authentication_headers
        if manifest_handler is not None and not callable(manifest_handler):
            raise TypeError("manifest_handler must be callable")
        if node_disconnected_handler is not None and not callable(
            node_disconnected_handler
        ):
            raise TypeError("node_disconnected_handler must be callable")
        self._manifest_handler = manifest_handler
        self._node_disconnected_handler = node_disconnected_handler
        self._connector = connector
        self._inbound: asyncio.Queue[Signal] = asyncio.Queue(self._inbound_capacity)
        self._outbound: asyncio.Queue[WireMessage] = asyncio.Queue(self._outbound_capacity)
        self._sending_message: WireMessage | None = None
        self._retry_message: WireMessage | None = None
        self._shutdown_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._manager_task: asyncio.Task[None] | None = None
        self._connection_state = WebSocketConnectionState.STOPPED
        self._connected_at: datetime | None = None
        self._last_connected_at: datetime | None = None
        self._last_disconnected_at: datetime | None = None
        self._connections_established = 0
        self._reconnect_attempts = 0
        self._messages_received = 0
        self._messages_sent = 0
        self._messages_rejected = 0
        self._peer_node_id: str | None = None

    @staticmethod
    def _required_string(value: Any, name: str) -> str:
        if type(value) is not str or not value:
            raise ValueError(f"{name} must be a non-empty string")
        return value

    @staticmethod
    def _positive_int(value: Any, name: str) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _positive_number(value: Any, name: str) -> float:
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{name} must be positive")
        return float(value)

    @staticmethod
    def _validate_uri(uri: Any) -> tuple[str, str]:
        if type(uri) is not str or not uri:
            raise ValueError("uri must be a non-empty WebSocket URI")
        parsed = urlsplit(uri)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise ValueError("uri must use ws:// or wss:// and include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("uri must not contain credentials; use authentication_headers")
        if parsed.fragment:
            raise ValueError("uri must not contain a fragment")
        endpoint = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        return uri, endpoint

    def status(self) -> WebSocketTransportStatus:
        base = super().status()
        return WebSocketTransportStatus(
            transport_id=base.transport_id,
            lifecycle=base.lifecycle,
            started_at=base.started_at,
            stopped_at=base.stopped_at,
            last_error=base.last_error,
            endpoint=self._endpoint,
            connection_state=self._connection_state,
            connected=self._connected_event.is_set(),
            connected_at=self._connected_at,
            last_connected_at=self._last_connected_at,
            last_disconnected_at=self._last_disconnected_at,
            connections_established=self._connections_established,
            reconnect_attempts=self._reconnect_attempts,
            messages_received=self._messages_received,
            messages_sent=self._messages_sent,
            messages_rejected=self._messages_rejected,
            inbound_depth=self._inbound.qsize(),
            inbound_capacity=self._inbound_capacity,
            outbound_depth=(
                self._outbound.qsize()
                + int(self._sending_message is not None)
                + int(self._retry_message is not None)
            ),
            outbound_capacity=self._outbound_capacity,
            peer_node_id=self._peer_node_id,
        )

    async def wait_connected(self, *, timeout: float | None = None) -> bool:
        """Wait for a connection without turning failure into an exception."""

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
        self._inbound = asyncio.Queue(self._inbound_capacity)
        self._outbound = asyncio.Queue(self._outbound_capacity)
        self._sending_message = None
        self._retry_message = None
        self._shutdown_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._connection_state = WebSocketConnectionState.CONNECTING
        self._peer_node_id = None
        self._manager_task = asyncio.create_task(
            self._connection_manager(),
            name=f"echo-medulla-websocket-{self.transport_id}",
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
        self._sending_message = None
        self._retry_message = None
        self._connected_at = None
        self._peer_node_id = None
        self._connection_state = WebSocketConnectionState.STOPPED

    async def _receive(self) -> Signal:
        item = asyncio.create_task(self._inbound.get())
        stopped = asyncio.create_task(self._shutdown_event.wait())
        try:
            done, _ = await asyncio.wait(
                {item, stopped}, return_when=asyncio.FIRST_COMPLETED
            )
            if item in done:
                return item.result()
            item.cancel()
            with suppress(asyncio.CancelledError):
                await item
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
        pending_depth = (
            self._outbound.qsize()
            + int(self._sending_message is not None)
            + int(self._retry_message is not None)
        )
        if pending_depth >= self._outbound_capacity:
            raise TransportOperationError(
                "outbound WebSocket queue is full",
                transport_id=self.transport_id,
                operation=TransportOperation.EXECUTE,
                code=TransportErrorCode.QUEUE_FULL,
                retryable=True,
            )
        self._outbound.put_nowait(message)
        return ActionDispatchResult(
            transport_id=self.transport_id,
            action_id=action.id,
            state=ActionDispatchState.ACCEPTED,
            external_id=message.id,
            result={
                "queued": True,
                "connected": self._connected_event.is_set(),
                "queue_depth": self._outbound.qsize(),
            },
        )

    async def _health(self) -> TransportHealth:
        status = self.status()
        details = {
            "endpoint": status.endpoint,
            "connection_state": status.connection_state.value,
            "connected": status.connected,
            "connections_established": status.connections_established,
            "reconnect_attempts": status.reconnect_attempts,
            "messages_received": status.messages_received,
            "messages_sent": status.messages_sent,
            "messages_rejected": status.messages_rejected,
            "inbound_depth": status.inbound_depth,
            "inbound_capacity": status.inbound_capacity,
            "outbound_depth": status.outbound_depth,
            "outbound_capacity": status.outbound_capacity,
            "peer_node_id": status.peer_node_id,
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
        if status.connected:
            return TransportHealth(
                transport_id=self.transport_id,
                lifecycle=status.lifecycle,
                state=TransportHealthState.DEGRADED,
                message="one or more WebSocket queues are full",
                details=details,
            )
        return TransportHealth(
            transport_id=self.transport_id,
            lifecycle=status.lifecycle,
            state=TransportHealthState.DEGRADED,
            message="WebSocket is disconnected; reconnect loop is active",
            details=details,
        )

    async def _connection_manager(self) -> None:
        delay = self._reconnect_initial_delay
        attempted = False
        while not self._shutdown_event.is_set():
            self._connection_state = (
                WebSocketConnectionState.RECONNECTING
                if attempted
                else WebSocketConnectionState.CONNECTING
            )
            if attempted:
                self._reconnect_attempts += 1
            attempted = True
            try:
                headers = await self._authentication_header_values()
                connector = self._connector or self._default_connector()
                connection = connector(
                    self._uri,
                    additional_headers=headers or None,
                    open_timeout=self._connect_timeout,
                    close_timeout=self._connect_timeout,
                    max_size=self._max_message_bytes,
                    ping_interval=None,
                )
                async with connection as socket:
                    await self._on_connected(socket)
                    delay = self._reconnect_initial_delay
                    await self._run_connection(socket)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._record_connection_error(error)
            finally:
                disconnected_node_id = self._peer_node_id
                if self._connected_event.is_set():
                    self._last_disconnected_at = _utc_now()
                self._connected_event.clear()
                self._connected_at = None
                self._peer_node_id = None
                if (
                    disconnected_node_id is not None
                    and self._node_disconnected_handler is not None
                ):
                    try:
                        handled = self._node_disconnected_handler(disconnected_node_id)
                        if inspect.isawaitable(handled):
                            await handled
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        self._record_connection_error(error)
                if not self._shutdown_event.is_set():
                    self._connection_state = WebSocketConnectionState.RECONNECTING
            if self._shutdown_event.is_set():
                break
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, self._reconnect_max_delay)

    @staticmethod
    def _default_connector() -> Connector:
        try:
            from websockets.asyncio.client import connect
        except ImportError as error:
            raise RuntimeError(
                "WebSocketTransport requires the 'websocket' optional dependency"
            ) from error
        return connect

    async def _authentication_header_values(self) -> dict[str, str]:
        if self._authentication_headers is None:
            return {}
        provided = self._authentication_headers()
        if inspect.isawaitable(provided):
            provided = await provided
        if not isinstance(provided, Mapping):
            raise ValueError("authentication_headers must return a mapping")
        headers: dict[str, str] = {}
        for key, value in provided.items():
            if type(key) is not str or not key or type(value) is not str or not value:
                raise ValueError("authentication headers must be non-empty strings")
            if "\r" in key or "\n" in key or "\r" in value or "\n" in value:
                raise ValueError("authentication headers must not contain newlines")
            headers[key] = value
        return headers

    async def _on_connected(self, socket: WebSocketLike) -> None:
        now = _utc_now()
        self._connected_at = now
        self._last_connected_at = now
        self._connections_established += 1
        self._connection_state = WebSocketConnectionState.CONNECTED
        self._connected_event.set()
        hello = make_wire_message(
            WireMessageType.HELLO,
            {"node_id": self._node_id, "role": "echo"},
        )
        await socket.send(encode_wire_message(hello))
        self._messages_sent += 1

    async def _run_connection(self, socket: WebSocketLike) -> None:
        receiver = asyncio.create_task(self._socket_receive_loop(socket))
        sender = asyncio.create_task(self._socket_send_loop(socket))
        try:
            done, _ = await asyncio.wait(
                {receiver, sender}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task.cancelled():
                    raise asyncio.CancelledError
                exception = task.exception()
                if exception is not None:
                    raise exception
            raise ConnectionError("WebSocket connection closed")
        finally:
            for task in (receiver, sender):
                if not task.done():
                    task.cancel()
            await asyncio.gather(receiver, sender, return_exceptions=True)

    async def _socket_receive_loop(self, socket: WebSocketLike) -> None:
        while True:
            raw = await socket.recv()
            try:
                message = decode_wire_message(raw, max_bytes=self._max_message_bytes)
                await self._handle_incoming(socket, message)
                self._messages_received += 1
            except WireProtocolError as error:
                self._messages_rejected += 1
                await self._send_protocol_error(socket, error)

    async def _handle_incoming(
        self, socket: WebSocketLike, message: WireMessage
    ) -> None:
        if message.type is WireMessageType.SIGNAL:
            signal = signal_from_message(message)
            try:
                self._inbound.put_nowait(signal)
            except asyncio.QueueFull as error:
                raise WireProtocolError(
                    "queue_full",
                    "inbound Signal queue is full",
                    message_id=message.id,
                ) from error
            return
        if message.type is WireMessageType.HELLO:
            if set(message.payload) - {"node_id", "role"}:
                raise WireProtocolError(
                    "invalid_payload", "hello contains unknown fields", message_id=message.id
                )
            try:
                self._peer_node_id = self._required_string(
                    message.payload.get("node_id"), "hello.node_id"
                )
                if "role" in message.payload:
                    self._required_string(message.payload["role"], "hello.role")
            except ValueError as error:
                raise WireProtocolError(
                    "invalid_payload", str(error), message_id=message.id
                ) from error
            return
        if message.type is WireMessageType.HEARTBEAT:
            if set(message.payload) - {"sent_at", "kind"}:
                raise WireProtocolError(
                    "invalid_payload", "heartbeat contains unknown fields", message_id=message.id
                )
            kind = message.payload.get("kind", "ping")
            if kind not in {"ping", "pong"}:
                raise WireProtocolError(
                    "invalid_payload",
                    "heartbeat.kind must be 'ping' or 'pong'",
                    message_id=message.id,
                )
            if kind == "pong":
                return
            response = make_wire_message(
                WireMessageType.HEARTBEAT,
                {"sent_at": _utc_now().isoformat(), "kind": "pong"},
                message_id=message.id,
            )
            await socket.send(encode_wire_message(response))
            self._messages_sent += 1
            return
        if message.type is WireMessageType.MANIFEST:
            if self._manifest_handler is None:
                raise WireProtocolError(
                    "unsupported_message",
                    "incoming manifest messages require a manifest handler",
                    message_id=message.id,
                )
            manifest = message.payload.get("manifest")
            if type(manifest) is not dict or type(manifest.get("node")) is not dict:
                raise WireProtocolError(
                    "invalid_payload",
                    "manifest must contain a node object",
                    message_id=message.id,
                )
            manifest_node_id = manifest["node"].get("id")
            if type(manifest_node_id) is not str or not manifest_node_id:
                raise WireProtocolError(
                    "invalid_payload",
                    "manifest.node.id must be a non-empty string",
                    message_id=message.id,
                )
            if (
                self._peer_node_id is not None
                and self._peer_node_id != manifest_node_id
            ):
                raise WireProtocolError(
                    "identity_mismatch",
                    "manifest node id does not match peer hello",
                    message_id=message.id,
                )
            try:
                handled = self._manifest_handler(message)
                if inspect.isawaitable(handled):
                    await handled
            except asyncio.CancelledError:
                raise
            except Exception as error:
                error_code = getattr(error, "code", "invalid_manifest")
                if type(error_code) is not str or not error_code:
                    error_code = "invalid_manifest"
                raise WireProtocolError(
                    error_code, str(error), message_id=message.id
                ) from error
            self._peer_node_id = manifest_node_id
            return
        if message.type in {
            WireMessageType.RESULT,
            WireMessageType.STATUS,
            WireMessageType.ERROR,
        }:
            # Reserved, safely decoded protocol observations. Phase 9C does not
            # attach capability, result-correlation, or discovery semantics.
            return
        raise WireProtocolError(
            "unsupported_message",
            f"incoming {message.type.value!r} messages are not supported",
            message_id=message.id,
        )

    async def _send_protocol_error(
        self, socket: WebSocketLike, error: WireProtocolError
    ) -> None:
        payload: dict[str, Any] = {
            "code": error.code,
            "message": str(error),
            "retryable": error.code == "queue_full",
        }
        if error.message_id is not None:
            payload["in_reply_to"] = error.message_id
        response = make_wire_message(WireMessageType.ERROR, payload)
        await socket.send(encode_wire_message(response))
        self._messages_sent += 1

    async def _socket_send_loop(self, socket: WebSocketLike) -> None:
        while True:
            if self._retry_message is not None:
                message = self._retry_message
                self._retry_message = None
            else:
                message = await self._outbound.get()
            self._sending_message = message
            try:
                await socket.send(encode_wire_message(message))
            except asyncio.CancelledError:
                if not self._shutdown_event.is_set():
                    self._retry_message = message
                raise
            except Exception:
                self._retry_message = message
                raise
            else:
                self._messages_sent += 1
            finally:
                self._sending_message = None

    def _record_connection_error(self, error: Exception) -> None:
        safe_message = (str(error) or type(error).__name__).replace(
            self._uri, self._endpoint
        )
        translated = TransportOperationError(
            f"WebSocket connection failed: {safe_message}",
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
