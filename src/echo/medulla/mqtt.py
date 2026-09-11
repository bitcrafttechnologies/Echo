"""Optional reconnectable MQTT transport for lightweight Medulla devices."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum, StrEnum
import math
import ssl
from typing import Any, Protocol
from urllib.parse import quote

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


class MQTTQoS(IntEnum):
    AT_MOST_ONCE = 0
    AT_LEAST_ONCE = 1
    EXACTLY_ONCE = 2


class MQTTConnectionState(StrEnum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


def _required_topic_level(value: Any, name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be a non-empty MQTT topic level")
    if any(character in value for character in ("/", "+", "#", "\0")):
        raise ValueError(f"{name} must be one MQTT topic level without wildcards")
    return value


def _required_topic_prefix(value: Any) -> str:
    if type(value) is not str or not value or value.startswith("/") or value.endswith("/"):
        raise ValueError("prefix must be a non-empty relative MQTT topic prefix")
    levels = value.split("/")
    if any(not level or "+" in level or "#" in level or "\0" in level for level in levels):
        raise ValueError("prefix must contain non-empty MQTT topic levels without wildcards")
    return value


@dataclass(slots=True, frozen=True, kw_only=True)
class MQTTBrokerConfig:
    """Explicit broker connection configuration; secrets stay out of repr/status."""

    host: str
    client_id: str
    port: int = 1883
    tls: bool = False
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    keepalive: int = 60
    connect_timeout: float = 10.0

    def __post_init__(self) -> None:
        if type(self.host) is not str or not self.host:
            raise ValueError("host must be a non-empty broker hostname")
        if "://" in self.host:
            raise ValueError("host must not include a URI scheme; configure port and TLS explicitly")
        _required_topic_level(self.client_id, "client_id")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port must be an integer from 1 through 65535")
        if type(self.tls) is not bool:
            raise ValueError("tls must be a boolean")
        if self.username is not None and (type(self.username) is not str or not self.username):
            raise ValueError("username must be a non-empty string when supplied")
        if self.password is not None and (type(self.password) is not str or not self.password):
            raise ValueError("password must be a non-empty string when supplied")
        if self.password is not None and self.username is None:
            raise ValueError("password requires username")
        if type(self.keepalive) is not int or self.keepalive <= 0:
            raise ValueError("keepalive must be a positive integer")
        if (
            type(self.connect_timeout) not in (int, float)
            or not math.isfinite(self.connect_timeout)
            or self.connect_timeout <= 0
        ):
            raise ValueError("connect_timeout must be positive and finite")


@dataclass(slots=True, frozen=True, kw_only=True)
class MQTTTopicMap:
    """Provisional Phase 9D topic mapping for one Echo Entity."""

    entity_id: str
    prefix: str = "echo"

    def __post_init__(self) -> None:
        _required_topic_level(self.entity_id, "entity_id")
        _required_topic_prefix(self.prefix)

    @property
    def base(self) -> str:
        return f"{self.prefix}/{self.entity_id}"

    @property
    def signal_subscription(self) -> str:
        return f"{self.base}/signals/#"

    @property
    def action_prefix(self) -> str:
        return f"{self.base}/actions"

    def action_topic(self, action_type: str) -> str:
        if type(action_type) is not str or not action_type:
            raise ValueError("action_type must be a non-empty string")
        return f"{self.action_prefix}/{quote(action_type, safe='')}"

    @property
    def status_topic(self) -> str:
        return f"{self.base}/status/transport"


class MQTTMessageLike(Protocol):
    payload: bytes | str


class MQTTClientLike(Protocol):
    messages: Any

    async def subscribe(self, topic: str, *, qos: int) -> Any: ...

    async def publish(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: int,
        retain: bool,
    ) -> Any: ...


MQTTClientFactory = Callable[..., Any]


@dataclass(slots=True, frozen=True, kw_only=True)
class MQTTTransportStatus(TransportStatus):
    broker_host: str = ""
    broker_port: int = 1883
    tls: bool = False
    client_id: str = ""
    entity_id: str = ""
    signal_subscription: str = ""
    action_prefix: str = ""
    status_topic: str = ""
    qos: MQTTQoS = MQTTQoS.AT_LEAST_ONCE
    connection_state: MQTTConnectionState = MQTTConnectionState.STOPPED
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
    wire_version: int = WIRE_VERSION


class MQTTTransport(BaseTransport):
    """Reconnectable MQTT client adapter with bounded Signal/Action queues."""

    def __init__(
        self,
        broker: MQTTBrokerConfig,
        topics: MQTTTopicMap,
        *,
        transport_id: str = "mqtt",
        qos: MQTTQoS = MQTTQoS.AT_LEAST_ONCE,
        inbound_capacity: int = 100,
        outbound_capacity: int = 100,
        reconnect_initial_delay: float = 0.1,
        reconnect_max_delay: float = 5.0,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        client_factory: MQTTClientFactory | None = None,
    ) -> None:
        super().__init__(transport_id)
        if not isinstance(broker, MQTTBrokerConfig):
            raise TypeError("broker must be an MQTTBrokerConfig")
        if not isinstance(topics, MQTTTopicMap):
            raise TypeError("topics must be an MQTTTopicMap")
        if not isinstance(qos, MQTTQoS):
            raise ValueError("qos must be an MQTTQoS")
        self._broker = broker
        self._topics = topics
        self._qos = qos
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
        self._max_message_bytes = self._positive_int(max_message_bytes, "max_message_bytes")
        self._client_factory = client_factory
        self._inbound: asyncio.Queue[Signal] = asyncio.Queue(self._inbound_capacity)
        self._outbound: asyncio.Queue[tuple[str, WireMessage]] = asyncio.Queue(
            self._outbound_capacity
        )
        self._sending: tuple[str, WireMessage] | None = None
        self._retry: tuple[str, WireMessage] | None = None
        self._shutdown_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._manager_task: asyncio.Task[None] | None = None
        self._active_client: MQTTClientLike | None = None
        self._connection_state = MQTTConnectionState.STOPPED
        self._connected_at: datetime | None = None
        self._last_connected_at: datetime | None = None
        self._last_disconnected_at: datetime | None = None
        self._connections_established = 0
        self._reconnect_attempts = 0
        self._messages_received = 0
        self._messages_sent = 0
        self._messages_rejected = 0

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
            raise ValueError(f"{name} must be positive and finite")
        return float(value)

    def status(self) -> MQTTTransportStatus:
        base = super().status()
        return MQTTTransportStatus(
            transport_id=base.transport_id,
            lifecycle=base.lifecycle,
            started_at=base.started_at,
            stopped_at=base.stopped_at,
            last_error=base.last_error,
            broker_host=self._broker.host,
            broker_port=self._broker.port,
            tls=self._broker.tls,
            client_id=self._broker.client_id,
            entity_id=self._topics.entity_id,
            signal_subscription=self._topics.signal_subscription,
            action_prefix=self._topics.action_prefix,
            status_topic=self._topics.status_topic,
            qos=self._qos,
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
                + int(self._sending is not None)
                + int(self._retry is not None)
            ),
            outbound_capacity=self._outbound_capacity,
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
        self._inbound = asyncio.Queue(self._inbound_capacity)
        self._outbound = asyncio.Queue(self._outbound_capacity)
        self._sending = None
        self._retry = None
        self._shutdown_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._connection_state = MQTTConnectionState.CONNECTING
        self._manager_task = asyncio.create_task(
            self._connection_manager(),
            name=f"echo-medulla-mqtt-{self.transport_id}",
        )

    async def _stop(self) -> None:
        self._shutdown_event.set()
        task = self._manager_task
        if self._connected_event.is_set() and task is not None:
            # Best effort only; broker loss must never make shutdown fail.
            with suppress(Exception):
                await asyncio.wait_for(
                    self._publish_status(self._active_client, "stopped"),
                    timeout=min(float(self._broker.connect_timeout), 1.0),
                )
        self._connected_event.clear()
        self._manager_task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._drain(self._inbound)
        self._drain(self._outbound)
        self._sending = None
        self._retry = None
        self._active_client = None
        self._connected_at = None
        self._connection_state = MQTTConnectionState.STOPPED

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
        pending_depth = (
            self._outbound.qsize()
            + int(self._sending is not None)
            + int(self._retry is not None)
        )
        if pending_depth >= self._outbound_capacity:
            raise TransportOperationError(
                "outbound MQTT queue is full",
                transport_id=self.transport_id,
                operation=TransportOperation.EXECUTE,
                code=TransportErrorCode.QUEUE_FULL,
                retryable=True,
            )
        topic = self._topics.action_topic(action.type)
        self._outbound.put_nowait((topic, message))
        return ActionDispatchResult(
            transport_id=self.transport_id,
            action_id=action.id,
            state=ActionDispatchState.ACCEPTED,
            external_id=message.id,
            result={
                "queued": True,
                "connected": self._connected_event.is_set(),
                "topic": topic,
                "qos": int(self._qos),
            },
        )

    async def _health(self) -> TransportHealth:
        status = self.status()
        details = {
            "broker_host": status.broker_host,
            "broker_port": status.broker_port,
            "tls": status.tls,
            "client_id": status.client_id,
            "entity_id": status.entity_id,
            "signal_subscription": status.signal_subscription,
            "action_prefix": status.action_prefix,
            "status_topic": status.status_topic,
            "qos": int(status.qos),
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
                "one or more MQTT queues are full"
                if status.connected
                else "MQTT broker is disconnected; reconnect loop is active"
            ),
            details=details,
        )

    async def _connection_manager(self) -> None:
        delay = self._reconnect_initial_delay
        attempted = False
        while not self._shutdown_event.is_set():
            self._connection_state = (
                MQTTConnectionState.RECONNECTING if attempted else MQTTConnectionState.CONNECTING
            )
            if attempted:
                self._reconnect_attempts += 1
            attempted = True
            try:
                client_context = self._create_client()
                async with client_context as client:
                    self._active_client = client
                    await client.subscribe(
                        self._topics.signal_subscription, qos=int(self._qos)
                    )
                    await self._on_connected(client)
                    delay = self._reconnect_initial_delay
                    await self._run_connection(client)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._record_connection_error(error)
            finally:
                if self._connected_event.is_set():
                    self._last_disconnected_at = _utc_now()
                self._connected_event.clear()
                self._connected_at = None
                self._active_client = None
                if not self._shutdown_event.is_set():
                    self._connection_state = MQTTConnectionState.RECONNECTING
            if self._shutdown_event.is_set():
                break
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, self._reconnect_max_delay)

    def _create_client(self) -> Any:
        factory = self._client_factory or self._default_client_factory()
        tls_context = ssl.create_default_context() if self._broker.tls else None
        return factory(
            hostname=self._broker.host,
            port=self._broker.port,
            username=self._broker.username,
            password=self._broker.password,
            identifier=self._broker.client_id,
            keepalive=self._broker.keepalive,
            timeout=self._broker.connect_timeout,
            tls_context=tls_context,
        )

    @staticmethod
    def _default_client_factory() -> MQTTClientFactory:
        try:
            from aiomqtt import Client
        except ImportError as error:
            raise RuntimeError(
                "MQTTTransport requires the 'mqtt' optional dependency"
            ) from error
        return Client

    async def _on_connected(self, client: MQTTClientLike) -> None:
        now = _utc_now()
        self._connected_at = now
        self._last_connected_at = now
        self._connections_established += 1
        self._connection_state = MQTTConnectionState.CONNECTED
        self._connected_event.set()
        await self._publish_status(client, "connected")

    async def _publish_status(self, client: MQTTClientLike | None, state: str) -> None:
        if client is None:
            return
        message = make_wire_message(
            WireMessageType.STATUS,
            {
                "state": state,
                "transport_id": self.transport_id,
                "entity_id": self._topics.entity_id,
                "observed_at": _utc_now().isoformat(),
            },
        )
        await client.publish(
            self._topics.status_topic,
            encode_wire_message(message),
            qos=int(self._qos),
            # Without a configured MQTT Last Will, retaining "connected"
            # could leave a false online state after an abrupt disconnect.
            retain=False,
        )
        self._messages_sent += 1

    async def _run_connection(self, client: MQTTClientLike) -> None:
        receiver = asyncio.create_task(self._message_loop(client))
        sender = asyncio.create_task(self._publish_loop(client))
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
            raise ConnectionError("MQTT connection closed")
        finally:
            for task in (receiver, sender):
                if not task.done():
                    task.cancel()
            await asyncio.gather(receiver, sender, return_exceptions=True)

    async def _message_loop(self, client: MQTTClientLike) -> None:
        async for mqtt_message in client.messages:
            self._messages_received += 1
            try:
                message = decode_wire_message(
                    mqtt_message.payload, max_bytes=self._max_message_bytes
                )
                if message.type is not WireMessageType.SIGNAL:
                    raise WireProtocolError(
                        "unexpected_message_type",
                        "MQTT Signal topics accept only signal messages",
                        message_id=message.id,
                    )
                signal = signal_from_message(message)
                self._inbound.put_nowait(signal)
            except (WireProtocolError, asyncio.QueueFull) as error:
                self._record_rejected(error)
        raise ConnectionError("MQTT message stream closed")

    async def _publish_loop(self, client: MQTTClientLike) -> None:
        while True:
            if self._retry is not None:
                outbound = self._retry
                self._retry = None
            else:
                outbound = await self._outbound.get()
            self._sending = outbound
            topic, message = outbound
            try:
                await client.publish(
                    topic,
                    encode_wire_message(message),
                    qos=int(self._qos),
                    retain=False,
                )
            except asyncio.CancelledError:
                if not self._shutdown_event.is_set():
                    self._retry = outbound
                raise
            except Exception:
                self._retry = outbound
                raise
            else:
                self._messages_sent += 1
            finally:
                self._sending = None

    def _record_rejected(self, error: Exception) -> None:
        self._messages_rejected += 1
        message = (
            "inbound MQTT Signal queue is full"
            if isinstance(error, asyncio.QueueFull)
            else str(error)
        )
        translated = TransportValidationError(
            message,
            transport_id=self.transport_id,
            operation=TransportOperation.VALIDATE,
            code=(
                TransportErrorCode.QUEUE_FULL
                if isinstance(error, asyncio.QueueFull)
                else TransportErrorCode.INVALID_PAYLOAD
            ),
            retryable=isinstance(error, asyncio.QueueFull),
        )
        self._last_error = translated.info

    def _record_connection_error(self, error: Exception) -> None:
        message = str(error) or type(error).__name__
        if self._broker.password:
            message = message.replace(self._broker.password, "<redacted>")
        translated = TransportOperationError(
            f"MQTT connection failed: {message}",
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
