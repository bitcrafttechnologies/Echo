"""Echo-side WebSocket listener for explicitly configured standalone nodes."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from inspect import isawaitable
from typing import Any, Awaitable, Callable, Mapping, Protocol
from urllib.parse import urlsplit

from echo.core.action import Action
from echo.core.signal import Signal
from echo.medulla.capability import (
    CapabilityAvailabilityState,
    CapabilityProviderHealthState,
    CapabilityRegistry,
)
from echo.medulla.node import (
    MedullaNodeDirectory,
    NodeProtocolError,
    node_manifest_from_message,
)
from echo.medulla.supervisor import SignalTarget
from echo.medulla.wire import (
    WireMessageType,
    action_message,
    decode_wire_message,
    encode_wire_message,
    make_wire_message,
    signal_from_message,
)
from medulla_protocol import (
    AuthorizedRequirements,
    ConnectionNegotiationState,
    ConnectionRequirements,
    NodeApprovalDecision,
    NodeApprovalMode,
    NodeActionResult,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RemoteNodeReachability(StrEnum):
    REACHABLE = "reachable"
    HEALTHY = "healthy"
    UNREACHABLE = "unreachable"


class NodeLifecycleEventType(StrEnum):
    NODE_DISCOVERED = "NodeDiscovered"
    NODE_AVAILABLE = "NodeAvailable"
    NODE_COMPATIBLE = "NodeCompatible"
    NODE_APPROVAL_REQUESTED = "NodeApprovalRequested"
    NODE_APPROVED = "NodeApproved"
    NODE_DECLINED = "NodeDeclined"
    NODE_BLOCKED = "NodeBlocked"
    NODE_REQUIREMENTS_AUTHORIZED = "NodeRequirementsAuthorized"
    NODE_AUTHENTICATION_FAILED = "NodeAuthenticationFailed"
    NODE_CONNECTED = "NodeConnected"
    NODE_DISCONNECTED = "NodeDisconnected"
    NODE_UNAVAILABLE = "NodeUnavailable"
    NODE_RECONNECTED = "NodeReconnected"


@dataclass(slots=True, frozen=True, kw_only=True)
class NodeLifecycleEvent:
    type: NodeLifecycleEventType
    node_id: str
    display_name: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_now)

    def to_signal(self) -> Signal:
        return Signal(
            type=self.type.value,
            source="medulla",
            timestamp=self.timestamp,
            payload={
                "node_id": self.node_id,
                "display_name": self.display_name,
                **self.details,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "node_id": self.node_id,
            "display_name": self.display_name,
            "details": dict(self.details),
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(slots=True, frozen=True, kw_only=True)
class NodeApprovalRequest:
    node_id: str
    display_name: str
    provides: tuple[str, ...]
    requires: tuple[str, ...]

    def render(self) -> str:
        provided = "\n".join(f"- {item}" for item in self.provides) or "- (none)"
        required = "\n".join(f"- {item}" for item in self.requires) or "- (none)"
        return (
            f"New Medulla Node:\n\n{self.display_name}\n\n"
            f"Provides:\n{provided}\n\nRequires:\n{required}\n\nConnect?"
        )


class NodeAuthorizationError(PermissionError):
    def __init__(self, state: ConnectionNegotiationState, message: str) -> None:
        self.state = state
        super().__init__(message)


@dataclass(slots=True, frozen=True, kw_only=True)
class NodeCandidateContext:
    current_tasks: tuple[str, ...] = ()
    current_goals: tuple[str, ...] = ()
    current_embodiment: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    available_providers: tuple[str, ...] = ()
    environment: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True, kw_only=True)
class AutonomousApproval:
    decision: NodeApprovalDecision
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", NodeApprovalDecision(self.decision))
        if self.decision is NodeApprovalDecision.BLOCK:
            raise ValueError("autonomous evaluation may approve or decline, never block")
        if type(self.reason) is not str or not self.reason.strip():
            raise ValueError("autonomous decision reason must not be empty")


class AutonomousNodeEvaluator(Protocol):
    async def evaluate(
        self,
        request: NodeApprovalRequest,
        context: NodeCandidateContext,
    ) -> AutonomousApproval: ...


class RequirementAuthorizationPolicy(Protocol):
    def authorize(
        self,
        node_id: str,
        requirements: ConnectionRequirements,
    ) -> AuthorizedRequirements | None: ...


class StaticRequirementAuthorizationPolicy:
    """Human-configured, deterministic grants keyed by exact node ID."""

    def __init__(self, grants: Mapping[str, AuthorizedRequirements]) -> None:
        if not isinstance(grants, Mapping):
            raise TypeError("grants must be a mapping")
        self._grants = dict(grants)
        if any(
            type(node_id) is not str
            or not node_id
            or not isinstance(grant, AuthorizedRequirements)
            for node_id, grant in self._grants.items()
        ):
            raise ValueError("grants must map node IDs to AuthorizedRequirements")

    def authorize(
        self,
        node_id: str,
        requirements: ConnectionRequirements,
    ) -> AuthorizedRequirements | None:
        grant = self._grants.get(node_id)
        if grant is None or grant.validate_for(requirements) is not None:
            return None
        return grant


@dataclass(slots=True, frozen=True, kw_only=True)
class RemoteNodeSessionStatus:
    node_id: str
    reachability: RemoteNodeReachability
    connected_at: datetime | None
    last_seen_at: datetime | None
    negotiation_state: ConnectionNegotiationState | None
    negotiation_history: tuple[ConnectionNegotiationState, ...] = ()
    first_seen_at: datetime | None = None
    active_at: datetime | None = None
    disconnected_at: datetime | None = None
    approval_mode: NodeApprovalMode = NodeApprovalMode.MANUAL
    decision_reason: str | None = None
    granted_scopes: dict[str, Any] | None = None


class EchoNodeWebSocketHost:
    """Accept node sessions while leaving discovery and authorization separate."""

    def __init__(
        self,
        endpoint: str,
        *,
        registry: CapabilityRegistry | None = None,
        signal_target: SignalTarget | None = None,
        approval_mode: NodeApprovalMode = NodeApprovalMode.MANUAL,
        autonomous_evaluator: AutonomousNodeEvaluator | None = None,
        authorization_policy: RequirementAuthorizationPolicy | None = None,
        candidate_context: (
            Callable[[], NodeCandidateContext | Awaitable[NodeCandidateContext]] | None
        ) = None,
        max_message_bytes: int = 1_048_576,
    ) -> None:
        parsed = urlsplit(endpoint)
        if parsed.scheme != "ws" or not parsed.hostname or parsed.port is None:
            raise ValueError("endpoint must be a ws:// URL with an explicit port")
        self._endpoint = endpoint
        self._host = parsed.hostname
        self._port = parsed.port
        self._path = parsed.path or "/"
        self.registry = registry or CapabilityRegistry()
        self.directory = MedullaNodeDirectory(self.registry)
        if signal_target is not None and not isinstance(signal_target, SignalTarget):
            raise TypeError("signal_target must implement SignalTarget")
        if type(max_message_bytes) is not int or max_message_bytes < 1:
            raise ValueError("max_message_bytes must be a positive integer")
        self._signal_target = signal_target
        self._approval_mode = NodeApprovalMode(approval_mode)
        self._autonomous_evaluator = autonomous_evaluator
        self._authorization_policy = authorization_policy
        self._candidate_context = candidate_context
        self._max_message_bytes = max_message_bytes
        self._server: Any | None = None
        self._sockets: dict[str, Any] = {}
        self._sessions: dict[str, RemoteNodeSessionStatus] = {}
        self._manifests: dict[str, Any] = {}
        self._signals: asyncio.Queue[Signal] = asyncio.Queue(256)
        self._pending: dict[
            str, tuple[str, str, asyncio.Future[NodeActionResult]]
        ] = {}
        self._authorization_pending: dict[
            str, tuple[
                str,
                asyncio.Future[tuple[bool, ConnectionNegotiationState, str | None]],
                asyncio.Event,
            ]
        ] = {}
        self._approval_requests: dict[str, NodeApprovalRequest] = {}
        self._blocked: set[str] = set()
        self._events: asyncio.Queue[NodeLifecycleEvent] = asyncio.Queue(256)
        self._event_history: deque[NodeLifecycleEvent] = deque(maxlen=256)
        self._announcements: asyncio.Queue[str] = asyncio.Queue(64)
        self._seen_counts: dict[str, int] = {}
        self._decision_reasons: dict[str, str] = {}
        self._authorized_summaries: dict[str, dict[str, Any]] = {}
        self._decision_tasks: set[asyncio.Task[None]] = set()
        self._session_errors: dict[str, str] = {}
        self._connected = asyncio.Condition()

    @property
    def endpoint(self) -> str:
        if self._server is None or not getattr(self._server, "sockets", None):
            return self._endpoint
        port = self._server.sockets[0].getsockname()[1]
        return f"ws://{self._host}:{port}{self._path}"

    async def start(self) -> None:
        if self._server is not None:
            return
        try:
            from websockets.asyncio.server import serve
        except ImportError as error:
            raise RuntimeError("Echo node host requires the 'websocket' extra") from error
        self._server = await serve(
            self._handle_connection,
            self._host,
            self._port,
            max_size=self._max_message_bytes,
            ping_interval=None,
        )

    async def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        for _node_id, _action_id, future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("Echo node host stopped"))
        self._pending.clear()
        for _node_id, future, activation_complete in self._authorization_pending.values():
            if not future.done():
                future.set_exception(ConnectionError("Echo node host stopped"))
            activation_complete.set()
        self._authorization_pending.clear()
        for task in self._decision_tasks:
            task.cancel()
        if self._decision_tasks:
            await asyncio.gather(*self._decision_tasks, return_exceptions=True)
        self._decision_tasks.clear()

    async def wait_for_node(self, node_id: str, timeout: float = 5.0) -> bool:
        async def wait() -> None:
            async with self._connected:
                await self._connected.wait_for(lambda: node_id in self._sockets)
        try:
            await asyncio.wait_for(wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def wait_for_active(self, node_id: str, timeout: float = 5.0) -> bool:
        async def wait() -> None:
            async with self._connected:
                await self._connected.wait_for(
                    lambda: self.status(node_id).negotiation_state
                    is ConnectionNegotiationState.ACTIVE
                )
        try:
            await asyncio.wait_for(wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def execute(
        self,
        node_id: str,
        action: Action,
        *,
        timeout: float = 10.0,
    ) -> NodeActionResult:
        if self.status(node_id).negotiation_state is not ConnectionNegotiationState.ACTIVE:
            raise NodeAuthorizationError(
                ConnectionNegotiationState.UNSATISFIED,
                f"node is not active: {node_id}",
            )
        socket = self._sockets.get(node_id)
        if socket is None:
            detail = self._session_errors.get(node_id)
            suffix = f" ({detail})" if detail else ""
            raise ConnectionError(f"node is unreachable: {node_id}{suffix}")
        message = action_message(action)
        future = asyncio.get_running_loop().create_future()
        self._pending[message.id] = (node_id, action.id, future)
        try:
            await socket.send(encode_wire_message(message))
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(message.id, None)

    async def execute_capability(
        self,
        action: Action,
        *,
        timeout: float = 10.0,
    ) -> NodeActionResult:
        """Execute through the first active node advertising an exact capability."""

        candidates = []
        for capability in self.registry.find(action.type):
            node_id = capability.provider.provider_id
            health = self.registry.health(node_id)
            if (
                self.status(node_id).negotiation_state is ConnectionNegotiationState.ACTIVE
                and capability.availability.state
                in {
                    CapabilityAvailabilityState.AVAILABLE,
                    CapabilityAvailabilityState.DEGRADED,
                }
                and health is not None
                and health.state is not CapabilityProviderHealthState.UNAVAILABLE
            ):
                candidates.append(node_id)
        if not candidates:
            raise ConnectionError(f"no active Medulla Node provides {action.type}")
        return await self.execute(sorted(candidates)[0], action, timeout=timeout)

    async def receive_signal(self) -> Signal:
        return await self._signals.get()

    async def receive_lifecycle_event(self) -> NodeLifecycleEvent:
        return await self._events.get()

    async def receive_announcement(self) -> str:
        return await self._announcements.get()

    @property
    def lifecycle_events(self) -> tuple[NodeLifecycleEvent, ...]:
        return tuple(self._event_history)

    @property
    def node_ids(self) -> tuple[str, ...]:
        """Every observed candidate, including declined, blocked, and offline nodes."""

        return tuple(sorted(set(self._sessions) | set(self._manifests)))

    def inspect_resources(self) -> tuple[dict[str, Any], ...]:
        """Return declared resources with their live provider connection state."""

        resources: list[dict[str, Any]] = []
        for node_id in self.node_ids:
            manifest = self._manifests.get(node_id)
            if manifest is None:
                continue
            status = self.status(node_id)
            connected = (
                status.negotiation_state is ConnectionNegotiationState.ACTIVE
                and status.reachability is not RemoteNodeReachability.UNREACHABLE
            )
            for resource in manifest.resources:
                resources.append(
                    {
                        **resource.to_dict(),
                        "node_id": node_id,
                        "provider_id": manifest.provider.provider_id,
                        "provider_connected": connected,
                    }
                )
        return tuple(resources)

    def approval_request(self, node_id: str) -> NodeApprovalRequest | None:
        return self._approval_requests.get(node_id)

    def is_blocked(self, node_id: str) -> bool:
        return node_id in self._blocked

    async def decide(
        self,
        node_id: str,
        decision: NodeApprovalDecision,
        *,
        reason: str | None = None,
    ) -> RemoteNodeSessionStatus:
        decision = NodeApprovalDecision(decision)
        if decision is NodeApprovalDecision.APPROVE:
            return await self.approve(node_id, reason=reason)
        if decision is NodeApprovalDecision.DECLINE:
            return await self.decline(node_id, reason=reason)
        return await self.block(node_id, reason=reason)

    async def approve(
        self,
        node_id: str,
        *,
        reason: str | None = None,
    ) -> RemoteNodeSessionStatus:
        self._require_offer(node_id)
        if node_id in self._blocked:
            raise NodeAuthorizationError(
                ConnectionNegotiationState.BLOCKED,
                f"node is blocked: {node_id}",
            )
        decision_reason = self._decision_reason(reason, "approved manually")
        self._decision_reasons[node_id] = decision_reason
        self._transition(node_id, ConnectionNegotiationState.APPROVED)
        await self._publish_event(
            NodeLifecycleEventType.NODE_APPROVED,
            node_id,
            details={"approval_mode": self._approval_mode.value, "reason": decision_reason},
        )
        await self._send_approval_decision(node_id, NodeApprovalDecision.APPROVE)
        return self.status(node_id)

    async def decline(
        self,
        node_id: str,
        *,
        reason: str | None = None,
    ) -> RemoteNodeSessionStatus:
        self._require_offer(node_id)
        decision_reason = self._decision_reason(reason, "declined manually")
        self._decision_reasons[node_id] = decision_reason
        self._transition(node_id, ConnectionNegotiationState.DECLINED)
        await self._publish_event(
            NodeLifecycleEventType.NODE_DECLINED,
            node_id,
            details={"approval_mode": self._approval_mode.value, "reason": decision_reason},
        )
        await self._send_approval_decision(node_id, NodeApprovalDecision.DECLINE)
        return self.status(node_id)

    async def reconsider(self, node_id: str) -> NodeApprovalRequest:
        self._require_offer(node_id)
        if node_id in self._blocked:
            raise NodeAuthorizationError(
                ConnectionNegotiationState.BLOCKED,
                f"node is blocked: {node_id}",
            )
        self._transition(node_id, ConnectionNegotiationState.AWAITING_APPROVAL)
        await self._publish_event(NodeLifecycleEventType.NODE_APPROVAL_REQUESTED, node_id)
        return self._approval_requests[node_id]

    async def reevaluate(
        self,
        node_id: str,
        *,
        context: NodeCandidateContext | None = None,
    ) -> RemoteNodeSessionStatus:
        self._require_offer(node_id)
        if self._approval_mode is not NodeApprovalMode.AUTONOMOUS:
            raise NodeAuthorizationError(
                ConnectionNegotiationState.UNSATISFIED,
                "reevaluation requires autonomous approval mode",
            )
        await self.reconsider(node_id)
        await self._autonomous_decide(node_id, context=context)
        return self.status(node_id)

    async def block(
        self,
        node_id: str,
        *,
        reason: str | None = None,
    ) -> RemoteNodeSessionStatus:
        self._require_offer(node_id)
        decision_reason = self._decision_reason(reason, "blocked manually")
        self._decision_reasons[node_id] = decision_reason
        self._blocked.add(node_id)
        self._transition(node_id, ConnectionNegotiationState.BLOCKED)
        await self._publish_event(
            NodeLifecycleEventType.NODE_BLOCKED,
            node_id,
            details={"approval_mode": self._approval_mode.value, "reason": decision_reason},
        )
        socket = self._sockets.get(node_id)
        if socket is not None:
            await self._send_approval_decision(node_id, NodeApprovalDecision.BLOCK)
            await socket.close(code=1008, reason="node blocked")
        return self.status(node_id)

    def unblock(self, node_id: str) -> bool:
        if node_id not in self._blocked:
            return False
        self._blocked.remove(node_id)
        return True

    async def authorize(
        self,
        node_id: str,
        authorization: AuthorizedRequirements,
        *,
        timeout: float = 10.0,
    ) -> RemoteNodeSessionStatus:
        if not isinstance(authorization, AuthorizedRequirements):
            raise TypeError("authorization must be AuthorizedRequirements")
        if self.status(node_id).negotiation_state is not ConnectionNegotiationState.APPROVED:
            raise NodeAuthorizationError(
                ConnectionNegotiationState.UNSATISFIED,
                "node approval is required before requirement authorization",
            )
        requirements = self.requirements(node_id)
        if requirements is None:
            raise ConnectionError(f"node manifest is unavailable: {node_id}")
        failure = authorization.validate_for(requirements)
        if failure is not None:
            state, message = failure
            self._transition(node_id, state)
            if state is ConnectionNegotiationState.AUTH_FAILED:
                await self._publish_event(
                    NodeLifecycleEventType.NODE_AUTHENTICATION_FAILED,
                    node_id,
                    details={"reason": message},
                )
            raise NodeAuthorizationError(state, message)
        socket = self._sockets.get(node_id)
        if socket is None:
            raise ConnectionError(f"node is unreachable: {node_id}")
        self._transition(node_id, ConnectionNegotiationState.REQUIREMENTS_AUTHORIZED)
        await self._publish_event(
            NodeLifecycleEventType.NODE_REQUIREMENTS_AUTHORIZED,
            node_id,
            details=authorization.public_summary(),
        )
        message = make_wire_message(
            WireMessageType.AUTHORIZATION,
            {"authorization": authorization.to_dict()},
        )
        future = asyncio.get_running_loop().create_future()
        activation_complete = asyncio.Event()
        self._authorization_pending[message.id] = (node_id, future, activation_complete)
        try:
            await socket.send(encode_wire_message(message))
            accepted, state, error = await asyncio.wait_for(future, timeout)
            if not accepted:
                self._transition(node_id, state)
                await self._publish_event(
                    NodeLifecycleEventType.NODE_AUTHENTICATION_FAILED,
                    node_id,
                    details={"reason": error or "node rejected authorization"},
                )
                raise NodeAuthorizationError(state, error or "node rejected authorization")
            self._transition(node_id, ConnectionNegotiationState.AUTHENTICATED)
            manifest = self._manifests[node_id]
            self.directory.connect(manifest, transport_id=f"node:{node_id}")
            self._authorized_summaries[node_id] = authorization.public_summary()
            self._transition(node_id, ConnectionNegotiationState.ACTIVE)
            async with self._connected:
                self._connected.notify_all()
            await self._publish_event(NodeLifecycleEventType.NODE_CONNECTED, node_id)
            display_name = manifest.node.display_name or manifest.node.node_id
            reason = self._decision_reasons.get(node_id)
            announcement = f"{display_name} connected."
            if self._approval_mode is NodeApprovalMode.AUTONOMOUS and reason:
                announcement = f"I connected to {display_name} because {reason}."
            self._offer_bounded(self._announcements, announcement)
            return self.status(node_id)
        finally:
            # The per-socket receive loop waits here after an accepted result,
            # so it cannot process a queued Signal until registration and the
            # host-side ACTIVE transition are complete.
            activation_complete.set()
            self._authorization_pending.pop(message.id, None)

    def status(self, node_id: str) -> RemoteNodeSessionStatus:
        return self._sessions.get(node_id) or RemoteNodeSessionStatus(
            node_id=node_id,
            reachability=RemoteNodeReachability.UNREACHABLE,
            connected_at=None,
            last_seen_at=None,
            negotiation_state=None,
        )

    def manifest(self, node_id: str):
        return self._manifests.get(node_id)

    def requirements(self, node_id: str) -> ConnectionRequirements | None:
        manifest = self._manifests.get(node_id)
        return manifest.connection_requirements if manifest is not None else None

    def inspect_node(self, node_id: str) -> dict[str, Any]:
        status = self.status(node_id)
        manifest = self._manifests.get(node_id)
        request = self._approval_requests.get(node_id)
        return {
            "node_id": node_id,
            "first_seen_at": status.first_seen_at.isoformat() if status.first_seen_at else None,
            "last_seen_at": status.last_seen_at.isoformat() if status.last_seen_at else None,
            "active_at": status.active_at.isoformat() if status.active_at else None,
            "disconnected_at": status.disconnected_at.isoformat() if status.disconnected_at else None,
            "reachability": status.reachability.value,
            "negotiation_state": status.negotiation_state.value if status.negotiation_state else None,
            "approval_mode": status.approval_mode.value,
            "decision_reason": status.decision_reason,
            "granted_scopes": status.granted_scopes,
            "advertised_manifest": manifest.to_dict() if manifest is not None else None,
            "approval_request": (
                {
                    "provides": list(request.provides),
                    "requires": list(request.requires),
                }
                if request is not None else None
            ),
            "events": [
                event.to_dict() for event in self._event_history if event.node_id == node_id
            ],
            "last_session_error": self._session_errors.get(node_id),
        }

    async def _handle_connection(self, socket: Any) -> None:
        request = getattr(socket, "request", None)
        if request is not None and urlsplit(request.path).path != self._path:
            await socket.close(code=1008, reason="unexpected node path")
            return
        node_id: str | None = None
        try:
            async for raw in socket:
                message = decode_wire_message(raw, max_bytes=self._max_message_bytes)
                if message.type is WireMessageType.HELLO:
                    claimed = message.payload.get("node_id")
                    if type(claimed) is not str or not claimed:
                        raise ValueError("hello.node_id must be a non-empty string")
                    existing_socket = self._sockets.get(claimed)
                    if existing_socket is not None and existing_socket is not socket:
                        await socket.close(code=1008, reason="duplicate node id")
                        return
                    node_id = claimed
                    self._seen_counts[node_id] = self._seen_counts.get(node_id, 0) + 1
                    self._transition(
                        node_id,
                        ConnectionNegotiationState.CONNECTED_TRANSPORT,
                        reset=True,
                    )
                    await self._publish_event(NodeLifecycleEventType.NODE_DISCOVERED, node_id)
                elif message.type is WireMessageType.MANIFEST:
                    try:
                        manifest = node_manifest_from_message(message)
                    except NodeProtocolError:
                        if node_id is not None:
                            self._transition(node_id, ConnectionNegotiationState.INCOMPATIBLE)
                        raise
                    if node_id is not None and manifest.node.node_id != node_id:
                        self._transition(node_id, ConnectionNegotiationState.INCOMPATIBLE)
                        raise ValueError("manifest identity does not match hello")
                    node_id = manifest.node.node_id
                    existing_socket = self._sockets.get(node_id)
                    if existing_socket is not None and existing_socket is not socket:
                        await socket.close(code=1008, reason="duplicate node id")
                        return
                    previous_manifest = self._manifests.get(node_id)
                    if previous_manifest is not None and not self._same_node_contract(
                        previous_manifest, manifest
                    ):
                        self._transition(node_id, ConnectionNegotiationState.INCOMPATIBLE)
                        await socket.close(code=1008, reason="node manifest changed unexpectedly")
                        return
                    self._transition(node_id, ConnectionNegotiationState.MANIFEST_RECEIVED)
                    try:
                        self.directory.validate_manifest(manifest)
                    except NodeProtocolError:
                        self._transition(node_id, ConnectionNegotiationState.INCOMPATIBLE)
                        raise
                    self._transition(node_id, ConnectionNegotiationState.COMPATIBLE)
                    self._manifests[node_id] = manifest
                    await self._publish_event(NodeLifecycleEventType.NODE_COMPATIBLE, node_id)
                    self._sockets[node_id] = socket
                    await self._publish_event(NodeLifecycleEventType.NODE_AVAILABLE, node_id)
                    if self._seen_counts.get(node_id, 0) > 1:
                        await self._publish_event(NodeLifecycleEventType.NODE_RECONNECTED, node_id)
                    self._transition(node_id, ConnectionNegotiationState.REQUIREMENTS_RECEIVED)
                    self._approval_requests[node_id] = self._make_approval_request(manifest)
                    if node_id in self._blocked:
                        self._transition(node_id, ConnectionNegotiationState.BLOCKED)
                        await socket.close(code=1008, reason="node blocked")
                        return
                    self._transition(node_id, ConnectionNegotiationState.AWAITING_APPROVAL)
                    await self._publish_event(
                        NodeLifecycleEventType.NODE_APPROVAL_REQUESTED,
                        node_id,
                    )
                    async with self._connected:
                        self._connected.notify_all()
                    if self._approval_mode is NodeApprovalMode.AUTONOMOUS:
                        task = asyncio.create_task(
                            self._autonomous_decide(node_id),
                            name=f"medulla-autonomous-approval-{node_id}",
                        )
                        self._decision_tasks.add(task)
                        task.add_done_callback(self._decision_tasks.discard)
                elif message.type is WireMessageType.SIGNAL:
                    if self.status(node_id or "").negotiation_state is not ConnectionNegotiationState.ACTIVE:
                        raise PermissionError("node sent a Signal before authorization")
                    signal = signal_from_message(message)
                    await self._signals.put(signal)
                    if self._signal_target is not None:
                        await self._signal_target.emit(signal)
                    self._touch(node_id)
                elif message.type is WireMessageType.RESULT:
                    reply_id = message.payload.get("in_reply_to")
                    result_data = message.payload.get("action_result")
                    if type(reply_id) is not str or type(result_data) is not dict:
                        raise ValueError("result must contain in_reply_to and action_result")
                    pending = self._pending.get(reply_id)
                    result = NodeActionResult.from_dict(result_data)
                    if pending is not None:
                        expected_node, expected_action, future = pending
                        if node_id != expected_node or result.node_id != expected_node:
                            raise ValueError("result node does not match pending Action")
                        if result.action_id != expected_action:
                            raise ValueError("result action does not match pending Action")
                        if not future.done():
                            future.set_result(result)
                    self._touch(node_id)
                elif message.type is WireMessageType.AUTHORIZATION_RESULT:
                    reply_id = message.payload.get("in_reply_to")
                    accepted = message.payload.get("accepted")
                    state_value = message.payload.get("state")
                    error = message.payload.get("error")
                    if (
                        type(reply_id) is not str
                        or type(accepted) is not bool
                        or (error is not None and type(error) is not str)
                    ):
                        raise ValueError("invalid authorization result")
                    state = ConnectionNegotiationState(state_value)
                    pending = self._authorization_pending.get(reply_id)
                    if pending is not None:
                        expected_node, future, activation_complete = pending
                        if node_id != expected_node:
                            raise ValueError("authorization result node mismatch")
                        if not future.done():
                            future.set_result((accepted, state, error))
                        if accepted:
                            await activation_complete.wait()
                    self._touch(node_id)
                elif message.type is WireMessageType.HEARTBEAT:
                    kind = message.payload.get("kind", "ping")
                    if kind == "ping":
                        response = make_wire_message(
                            WireMessageType.HEARTBEAT,
                            {"kind": "pong", "sent_at": _now().isoformat()},
                            message_id=message.id,
                        )
                        await socket.send(encode_wire_message(response))
                    elif kind != "pong":
                        raise ValueError("invalid heartbeat kind")
                    self._touch(node_id, healthy=True)
                elif message.type is WireMessageType.STATUS:
                    self._touch(node_id, healthy=message.payload.get("health") == "healthy")
                elif message.type is WireMessageType.ERROR:
                    self._touch(node_id)
                else:
                    raise ValueError(f"unsupported node message: {message.type.value}")
        except Exception as error:
            # A malformed or abruptly closed peer is contained to its session.
            if node_id is not None:
                self._session_errors[node_id] = f"{type(error).__name__}: {error}"
        finally:
            if node_id is not None and self._sockets.get(node_id) is socket:
                self._sockets.pop(node_id, None)
                self.directory.disconnect(node_id, reason="node transport disconnected")
                previous = self._sessions.get(node_id)
                self._sessions[node_id] = RemoteNodeSessionStatus(
                    node_id=node_id,
                    reachability=RemoteNodeReachability.UNREACHABLE,
                    connected_at=previous.connected_at if previous else None,
                    last_seen_at=_now(),
                    negotiation_state=(previous.negotiation_state if previous else None),
                    negotiation_history=(previous.negotiation_history if previous else ()),
                    first_seen_at=(previous.first_seen_at if previous else None),
                    active_at=(previous.active_at if previous else None),
                    disconnected_at=_now(),
                    approval_mode=self._approval_mode,
                    decision_reason=self._decision_reasons.get(node_id),
                    granted_scopes=self._authorized_summaries.get(node_id),
                )
                for pending_node, future, activation_complete in self._authorization_pending.values():
                    if pending_node == node_id and not future.done():
                        future.set_exception(ConnectionError("node disconnected during authorization"))
                    if pending_node == node_id:
                        activation_complete.set()
                for pending_node, _action_id, future in self._pending.values():
                    if pending_node == node_id and not future.done():
                        future.set_exception(ConnectionError("node disconnected during Action"))
                await self._publish_event(NodeLifecycleEventType.NODE_DISCONNECTED, node_id)
                await self._publish_event(NodeLifecycleEventType.NODE_UNAVAILABLE, node_id)

    def _touch(self, node_id: str | None, *, healthy: bool = False) -> None:
        if node_id is None:
            return
        previous = self._sessions.get(node_id)
        self._sessions[node_id] = RemoteNodeSessionStatus(
            node_id=node_id,
            reachability=(RemoteNodeReachability.HEALTHY if healthy else RemoteNodeReachability.REACHABLE),
            connected_at=previous.connected_at if previous else _now(),
            last_seen_at=_now(),
            negotiation_state=(previous.negotiation_state if previous else None),
            negotiation_history=(previous.negotiation_history if previous else ()),
            first_seen_at=(previous.first_seen_at if previous else _now()),
            active_at=(previous.active_at if previous else None),
            disconnected_at=(previous.disconnected_at if previous else None),
            approval_mode=self._approval_mode,
            decision_reason=self._decision_reasons.get(node_id),
            granted_scopes=self._authorized_summaries.get(node_id),
        )

    def _transition(
        self,
        node_id: str,
        state: ConnectionNegotiationState,
        *,
        reset: bool = False,
    ) -> None:
        previous = self._sessions.get(node_id)
        history = previous.negotiation_history if previous and not reset else ()
        if not history or history[-1] is not state:
            history = (*history, state)
        self._sessions[node_id] = RemoteNodeSessionStatus(
            node_id=node_id,
            reachability=(previous.reachability if previous else RemoteNodeReachability.REACHABLE),
            connected_at=(
                _now()
                if reset
                else previous.connected_at if previous else _now()
            ),
            last_seen_at=_now(),
            negotiation_state=state,
            negotiation_history=history,
            first_seen_at=(previous.first_seen_at if previous and previous.first_seen_at else _now()),
            active_at=(
                _now()
                if state is ConnectionNegotiationState.ACTIVE
                else previous.active_at if previous else None
            ),
            disconnected_at=(previous.disconnected_at if previous else None),
            approval_mode=self._approval_mode,
            decision_reason=self._decision_reasons.get(node_id),
            granted_scopes=self._authorized_summaries.get(node_id),
        )

    def _require_offer(self, node_id: str) -> None:
        if type(node_id) is not str or not node_id:
            raise ValueError("node_id must not be empty")
        if node_id not in self._approval_requests:
            raise KeyError(f"unknown node candidate: {node_id}")

    async def _send_approval_decision(
        self,
        node_id: str,
        decision: NodeApprovalDecision,
    ) -> None:
        socket = self._sockets.get(node_id)
        if socket is None:
            raise ConnectionError(f"node is unreachable: {node_id}")
        await socket.send(
            encode_wire_message(
                make_wire_message(
                    WireMessageType.APPROVAL_DECISION,
                    {"decision": decision.value},
                )
            )
        )

    @staticmethod
    def _decision_reason(reason: str | None, fallback: str) -> str:
        value = fallback if reason is None else reason
        if type(value) is not str or not value.strip():
            raise ValueError("decision reason must not be empty")
        return value.strip()

    async def _publish_event(
        self,
        event_type: NodeLifecycleEventType,
        node_id: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        manifest = self._manifests.get(node_id)
        display_name = (
            manifest.node.display_name or manifest.node.node_id
            if manifest is not None
            else node_id
        )
        event = NodeLifecycleEvent(
            type=event_type,
            node_id=node_id,
            display_name=display_name,
            details=dict(details or {}),
        )
        self._event_history.append(event)
        self._offer_bounded(self._events, event)
        if self._signal_target is not None:
            try:
                await self._signal_target.emit(event.to_signal())
            except Exception:
                # Observability delivery cannot grant authority or break containment.
                pass

    @staticmethod
    def _offer_bounded(queue: asyncio.Queue[Any], item: Any) -> None:
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(item)

    def _make_approval_request(self, manifest: Any) -> NodeApprovalRequest:
        requirements = manifest.connection_requirements
        provides = tuple(
            [item.name for item in manifest.capabilities]
            + [f"Signal: {item.name}" for item in manifest.signals]
            + [f"Resource: {item.resource_id}" for item in manifest.resources]
        )
        required: list[str] = []
        if requirements.identity is not None:
            required.extend(item.value for item in requirements.identity.scopes)
        if requirements.metadata is not None:
            required.extend(f"metadata.{item}" for item in requirements.metadata.keys)
        required.extend(f"credential.{item.credential_id}" for item in requirements.credentials)
        required.extend(requirements.permissions)
        required.extend(f"protocol.{item}" for item in requirements.protocol_features)
        required.extend(f"capability.{item}" for item in requirements.entity_capabilities)
        if requirements.session is not None:
            required.extend(f"session.{item}" for item in requirements.session.features)
        return NodeApprovalRequest(
            node_id=manifest.node.node_id,
            display_name=manifest.node.display_name or manifest.node.node_id,
            provides=provides,
            requires=tuple(required),
        )

    async def _autonomous_decide(
        self,
        node_id: str,
        *,
        context: NodeCandidateContext | None = None,
    ) -> None:
        if self._autonomous_evaluator is None:
            return
        if context is None:
            context = NodeCandidateContext()
            if self._candidate_context is not None:
                supplied = self._candidate_context()
                context = await supplied if isawaitable(supplied) else supplied
        if not isinstance(context, NodeCandidateContext):
            raise TypeError("candidate context provider must return NodeCandidateContext")
        try:
            decision = await self._autonomous_evaluator.evaluate(
                self._approval_requests[node_id], context
            )
        except Exception:
            try:
                await self.decline(node_id, reason="autonomous evaluation failed")
            except (ConnectionError, KeyError, NodeAuthorizationError):
                pass
            return
        if not isinstance(decision, AutonomousApproval):
            raise TypeError("autonomous evaluator must return AutonomousApproval")
        try:
            if decision.decision is NodeApprovalDecision.DECLINE:
                await self.decline(node_id, reason=decision.reason)
                return
            await self.approve(node_id, reason=decision.reason)
        except (ConnectionError, KeyError, NodeAuthorizationError):
            return
        requirements = self.requirements(node_id)
        authorization = (
            self._authorization_policy.authorize(node_id, requirements)
            if self._authorization_policy is not None and requirements is not None
            else None
        )
        if authorization is None:
            return
        try:
            await self.authorize(node_id, authorization)
        except (ConnectionError, NodeAuthorizationError):
            return

    @staticmethod
    def _same_node_contract(previous: Any, current: Any) -> bool:
        before = previous.to_dict()
        after = current.to_dict()
        before.pop("health", None)
        after.pop("health", None)
        return before == after


__all__ = [
    "AutonomousApproval", "AutonomousNodeEvaluator", "EchoNodeWebSocketHost",
    "NodeApprovalRequest", "NodeAuthorizationError", "NodeCandidateContext",
    "NodeLifecycleEvent", "NodeLifecycleEventType", "RemoteNodeReachability",
    "RemoteNodeSessionStatus", "RequirementAuthorizationPolicy",
    "StaticRequirementAuthorizationPolicy",
]
