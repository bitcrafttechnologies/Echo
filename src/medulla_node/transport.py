"""Reconnectable standalone-node client for the existing Medulla WebSocket wire protocol."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import time
from typing import Any, Protocol, TYPE_CHECKING
from urllib.parse import urlsplit

from medulla_node.config import EchoConnectionConfig
from medulla_protocol import (
    AuthorizedRequirements,
    ConnectionNegotiationState,
    DEFAULT_MAX_MESSAGE_BYTES,
    NodeAction,
    WireMessage,
    WireMessageType,
    WireProtocolError,
    decode_wire_message,
    encode_wire_message,
    make_wire_message,
    node_manifest_message,
)

if TYPE_CHECKING:
    from medulla_node.runtime import MedullaNodeRuntime


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NodeTransportState(StrEnum):
    STARTING = "starting"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"


class NodeReachability(StrEnum):
    REACHABLE = "reachable"
    HEALTHY = "healthy"
    UNREACHABLE = "unreachable"


class WebSocketLike(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...


@dataclass(slots=True, frozen=True, kw_only=True)
class NodeTransportStatus:
    state: NodeTransportState
    reachability: NodeReachability
    endpoint: str
    connected_at: datetime | None
    last_disconnected_at: datetime | None
    last_heartbeat_at: datetime | None
    connections: int
    reconnect_attempts: int
    messages_sent: int
    messages_received: int
    negotiation_state: ConnectionNegotiationState
    authorization: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "reachability": self.reachability.value,
            "endpoint": self.endpoint,
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "last_disconnected_at": self.last_disconnected_at.isoformat() if self.last_disconnected_at else None,
            "last_heartbeat_at": self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None,
            "connections": self.connections,
            "reconnect_attempts": self.reconnect_attempts,
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
            "negotiation_state": self.negotiation_state.value,
            "authorization": self.authorization,
        }


class NodeWebSocketTransport:
    def __init__(
        self,
        runtime: MedullaNodeRuntime,
        config: EchoConnectionConfig,
        *,
        connector: Any | None = None,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    ) -> None:
        if not isinstance(config, EchoConnectionConfig):
            raise TypeError("config must be EchoConnectionConfig")
        parsed = urlsplit(config.endpoint)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise ValueError("Echo endpoint must be an absolute WebSocket URL")
        self._runtime = runtime
        self._config = config
        self._connector = connector
        self._max_message_bytes = max_message_bytes
        self._state = NodeTransportState.STOPPED
        self._connected = asyncio.Event()
        self._shutdown = asyncio.Event()
        self._outbound: asyncio.Queue[WireMessage] = asyncio.Queue(256)
        self._manager: asyncio.Task[None] | None = None
        self._signals: asyncio.Task[None] | None = None
        self._retry_message: WireMessage | None = None
        self._connected_at: datetime | None = None
        self._last_disconnected_at: datetime | None = None
        self._last_heartbeat_at: datetime | None = None
        self._last_pong = 0.0
        self._heartbeat_confirmed = False
        self._connections = 0
        self._reconnect_attempts = 0
        self._messages_sent = 0
        self._messages_received = 0
        self._negotiation_state = ConnectionNegotiationState.CONNECTED_TRANSPORT
        self._authorization: AuthorizedRequirements | None = None
        self._active = asyncio.Event()

    async def start(self) -> None:
        if self._state is not NodeTransportState.STOPPED:
            return
        self._state = NodeTransportState.STARTING
        self._shutdown = asyncio.Event()
        self._connected = asyncio.Event()
        self._active = asyncio.Event()
        self._manager = asyncio.create_task(self._connection_manager(), name="medulla-node-websocket")
        self._signals = asyncio.create_task(self._signal_pump(), name="medulla-node-signals")

    async def stop(self) -> None:
        self._shutdown.set()
        self._connected.clear()
        tasks = [task for task in (self._manager, self._signals) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._manager = None
        self._signals = None
        self._retry_message = None
        self._authorization = None
        while not self._outbound.empty():
            self._outbound.get_nowait()
        self._state = NodeTransportState.STOPPED

    async def wait_connected(self, timeout: float | None = None) -> bool:
        try:
            if timeout is None:
                await self._connected.wait()
            else:
                await asyncio.wait_for(self._connected.wait(), timeout)
        except TimeoutError:
            return False
        return True

    def status(self) -> NodeTransportStatus:
        if self._state is NodeTransportState.CONNECTED:
            reachability = (
                NodeReachability.HEALTHY
                if self._heartbeat_confirmed
                and time.monotonic() - self._last_pong <= self._config.heartbeat_timeout
                else NodeReachability.REACHABLE
            )
        else:
            reachability = NodeReachability.UNREACHABLE
        return NodeTransportStatus(
            state=self._state,
            reachability=reachability,
            endpoint=self._config.endpoint,
            connected_at=self._connected_at,
            last_disconnected_at=self._last_disconnected_at,
            last_heartbeat_at=self._last_heartbeat_at,
            connections=self._connections,
            reconnect_attempts=self._reconnect_attempts,
            messages_sent=self._messages_sent,
            messages_received=self._messages_received,
            negotiation_state=self._negotiation_state,
            authorization=(self._authorization.public_summary() if self._authorization else None),
        )

    async def wait_active(self, timeout: float | None = None) -> bool:
        try:
            if timeout is None:
                await self._active.wait()
            else:
                await asyncio.wait_for(self._active.wait(), timeout)
        except TimeoutError:
            return False
        return True

    @property
    def authorization(self) -> AuthorizedRequirements | None:
        return self._authorization

    async def _connection_manager(self) -> None:
        delay = self._config.reconnect_initial_delay
        attempted = False
        while not self._shutdown.is_set():
            self._state = NodeTransportState.RECONNECTING if attempted else NodeTransportState.CONNECTING
            if attempted:
                self._reconnect_attempts += 1
            attempted = True
            try:
                connector = self._connector or self._default_connector()
                connection = connector(
                    self._config.endpoint,
                    open_timeout=10.0,
                    close_timeout=10.0,
                    max_size=self._max_message_bytes,
                    ping_interval=None,
                )
                async with connection as socket:
                    await self._connected_session(socket)
                    delay = self._config.reconnect_initial_delay
                    await self._run_session(socket)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                if self._connected.is_set():
                    self._last_disconnected_at = _now()
                self._connected.clear()
                self._active.clear()
                self._authorization = None
                self._retry_message = None
                while not self._outbound.empty():
                    self._outbound.get_nowait()
                self._connected_at = None
                if not self._shutdown.is_set():
                    self._state = NodeTransportState.DISCONNECTED
            if self._shutdown.is_set():
                break
            self._state = NodeTransportState.RECONNECTING
            try:
                await asyncio.wait_for(self._shutdown.wait(), delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, self._config.reconnect_max_delay)

    async def _connected_session(self, socket: WebSocketLike) -> None:
        self._state = NodeTransportState.CONNECTED
        self._connected_at = _now()
        self._connections += 1
        self._last_pong = time.monotonic()
        self._heartbeat_confirmed = False
        self._active.clear()
        self._authorization = None
        self._negotiation_state = ConnectionNegotiationState.AWAITING_APPROVAL
        for message in (
            make_wire_message(WireMessageType.HELLO, {"node_id": self._runtime.config.node_id, "role": "medulla-node"}),
            node_manifest_message(self._runtime.manifest()),
            make_wire_message(WireMessageType.STATUS, {"node_id": self._runtime.config.node_id, "reachability": "reachable", "health": "healthy"}),
        ):
            await socket.send(encode_wire_message(message))
            self._messages_sent += 1
        self._connected.set()

    async def _run_session(self, socket: WebSocketLike) -> None:
        tasks = {
            asyncio.create_task(self._receive_loop(socket)),
            asyncio.create_task(self._send_loop(socket)),
            asyncio.create_task(self._heartbeat_loop()),
        }
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.cancelled():
                    raise asyncio.CancelledError
                error = task.exception()
                if error is not None:
                    raise error
            raise ConnectionError("WebSocket session ended")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _receive_loop(self, socket: WebSocketLike) -> None:
        while True:
            message = decode_wire_message(await socket.recv(), max_bytes=self._max_message_bytes)
            self._messages_received += 1
            if message.type is WireMessageType.ACTION:
                await self._handle_action(message)
            elif message.type is WireMessageType.APPROVAL_DECISION:
                await self._handle_approval_decision(message)
            elif message.type is WireMessageType.AUTHORIZATION:
                await self._handle_authorization(message)
            elif message.type is WireMessageType.HEARTBEAT:
                await self._handle_heartbeat(message)
            elif message.type is WireMessageType.ERROR:
                continue
            else:
                raise WireProtocolError("unsupported_message", f"node cannot receive {message.type.value}", message_id=message.id)

    async def _handle_action(self, message: WireMessage) -> None:
        if set(message.payload) != {"action"} or type(message.payload.get("action")) is not dict:
            raise WireProtocolError("invalid_action", "Action payload must contain one action object", message_id=message.id)
        try:
            action = NodeAction.from_echo_dict(message.payload["action"])
        except (TypeError, ValueError) as error:
            raise WireProtocolError("invalid_action", str(error), message_id=message.id) from error
        if not self._active.is_set():
            from medulla_protocol import NodeActionError, NodeActionErrorCode, NodeActionResult, NodeActionResultState
            result = NodeActionResult(
                action_id=action.id,
                state=NodeActionResultState.REJECTED,
                node_id=self._runtime.config.node_id,
                provider_id=self._runtime.config.provider_id,
                error=NodeActionError(
                    code=NodeActionErrorCode.AUTHORIZATION_REQUIRED,
                    message="node session is not active",
                ),
            )
            await self._outbound.put(
                make_wire_message(
                    WireMessageType.RESULT,
                    {"in_reply_to": message.id, "action_result": result.to_dict()},
                )
            )
            return
        result = await self._runtime.execute(action)
        await self._outbound.put(
            make_wire_message(
                WireMessageType.RESULT,
                {"in_reply_to": message.id, "action_result": result.to_dict()},
            )
        )

    async def _handle_authorization(self, message: WireMessage) -> None:
        if set(message.payload) != {"authorization"} or type(message.payload.get("authorization")) is not dict:
            raise WireProtocolError(
                "invalid_authorization",
                "authorization payload must contain one authorization object",
                message_id=message.id,
            )
        if self._negotiation_state is not ConnectionNegotiationState.APPROVED:
            await self._outbound.put(
                make_wire_message(
                    WireMessageType.AUTHORIZATION_RESULT,
                    {
                        "in_reply_to": message.id,
                        "accepted": False,
                        "state": ConnectionNegotiationState.UNSATISFIED.value,
                        "error": "node approval is required before authorization",
                    },
                )
            )
            return
        try:
            authorization = AuthorizedRequirements.from_dict(message.payload["authorization"])
            failure = authorization.validate_for(
                self._runtime.manifest().connection_requirements
            )
        except (TypeError, ValueError) as error:
            failure = (ConnectionNegotiationState.AUTH_FAILED, str(error))
            authorization = None
        accepted = failure is None and authorization is not None
        if accepted:
            self._authorization = authorization
            self._negotiation_state = ConnectionNegotiationState.ACTIVE
            state = ConnectionNegotiationState.ACTIVE
            error_message = None
        else:
            state, error_message = failure
            self._negotiation_state = state
            self._active.clear()
        await self._outbound.put(
            make_wire_message(
                WireMessageType.AUTHORIZATION_RESULT,
                {
                    "in_reply_to": message.id,
                    "accepted": accepted,
                    "state": state.value,
                    "error": error_message,
                },
            )
        )
        # The authorization result must precede every Signal from the newly
        # active session. The FIFO outbound queue preserves that wire order.
        if accepted:
            self._active.set()

    async def _handle_approval_decision(self, message: WireMessage) -> None:
        from medulla_protocol import NodeApprovalDecision

        if set(message.payload) != {"decision"}:
            raise WireProtocolError(
                "invalid_approval_decision",
                "approval decision must contain only decision",
                message_id=message.id,
            )
        try:
            decision = NodeApprovalDecision(message.payload.get("decision"))
        except (TypeError, ValueError) as error:
            raise WireProtocolError(
                "invalid_approval_decision",
                "unknown approval decision",
                message_id=message.id,
            ) from error
        self._authorization = None
        self._active.clear()
        self._negotiation_state = {
            NodeApprovalDecision.APPROVE: ConnectionNegotiationState.APPROVED,
            NodeApprovalDecision.DECLINE: ConnectionNegotiationState.DECLINED,
            NodeApprovalDecision.BLOCK: ConnectionNegotiationState.BLOCKED,
        }[decision]

    async def _handle_heartbeat(self, message: WireMessage) -> None:
        if set(message.payload) - {"sent_at", "kind"}:
            raise WireProtocolError("invalid_payload", "heartbeat contains unknown fields", message_id=message.id)
        kind = message.payload.get("kind", "ping")
        if kind == "pong":
            self._last_pong = time.monotonic()
            self._heartbeat_confirmed = True
            self._last_heartbeat_at = _now()
        elif kind == "ping":
            await self._outbound.put(
                make_wire_message(WireMessageType.HEARTBEAT, {"kind": "pong", "sent_at": _now().isoformat()}, message_id=message.id)
            )
        else:
            raise WireProtocolError("invalid_payload", "heartbeat.kind must be ping or pong", message_id=message.id)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self._config.heartbeat_interval)
            if time.monotonic() - self._last_pong > self._config.heartbeat_timeout:
                raise TimeoutError("Echo heartbeat timed out")
            self._last_heartbeat_at = _now()
            await self._outbound.put(
                make_wire_message(WireMessageType.HEARTBEAT, {"kind": "ping", "sent_at": _now().isoformat()})
            )

    async def _send_loop(self, socket: WebSocketLike) -> None:
        while True:
            message = self._retry_message or await self._outbound.get()
            self._retry_message = None
            try:
                await socket.send(encode_wire_message(message))
            except BaseException:
                self._retry_message = message
                raise
            self._messages_sent += 1

    async def _signal_pump(self) -> None:
        while True:
            signal = await self._runtime.receive_signal()
            if not self._active.is_set():
                continue
            await self._outbound.put(make_wire_message(WireMessageType.SIGNAL, {"signal": signal.to_dict()}))

    @staticmethod
    def _default_connector():
        try:
            from websockets.asyncio.client import connect
        except ImportError as error:
            raise RuntimeError("Node WebSocket transport requires the 'websocket' extra") from error
        return connect


__all__ = [
    "NodeReachability", "NodeTransportState", "NodeTransportStatus",
    "NodeWebSocketTransport",
]
