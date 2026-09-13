from __future__ import annotations

import asyncio
from collections import deque
import json
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echo import Action
from echo.medulla import (
    ActionDispatchResult,
    ActionDispatchState,
    Capability,
    CapabilityAvailability,
    CapabilityAvailabilityState,
    CapabilityEffect,
    CapabilityPermissionDecision,
    CapabilityPermissions,
    CapabilityProvider,
    CapabilityProviderHealthState,
    CapabilityProviderKind,
    CapabilityProviderLocation,
    CapabilityRegistry,
    CapabilityRisk,
    CapabilityRouter,
    CapabilityTimeout,
    MedullaNodeConnectionState,
    MedullaNodeDirectory,
    MedullaNodeEndpoint,
    MedullaNodeHealth,
    MedullaNodeIdentity,
    MedullaNodeManifest,
    MedullaNodeResource,
    MedullaNodeSecurity,
    MedullaNodeSignal,
    MedullaNodeType,
    NODE_PROTOCOL,
    NodeProtocolError,
    WebSocketTransport,
    WireMessageType,
    decode_wire_message,
    encode_wire_message,
    make_wire_message,
    node_manifest_from_message,
    node_manifest_message,
)


def make_manifest() -> MedullaNodeManifest:
    provider = CapabilityProvider(
        provider_id="bit-head-controller",
        kind=CapabilityProviderKind.WEBSOCKET,
        location=CapabilityProviderLocation.REMOTE,
        display_name="Bit Head",
    )
    return MedullaNodeManifest(
        node=MedullaNodeIdentity(
            node_id="bit-head-controller",
            type=MedullaNodeType.EMBEDDED,
            display_name="Bit Head Controller",
            metadata={"firmware": "1.2.3"},
        ),
        provider=provider,
        endpoints=(
            MedullaNodeEndpoint(
                endpoint_id="control",
                kind=CapabilityProviderKind.WEBSOCKET,
                address="wss://bit-head.local/medulla",
                metadata={"wire_version": 1},
            ),
        ),
        capabilities=(
            Capability(
                capability_id="bit-head-controller.head.rotate.v1",
                name="head.rotate",
                description="Rotate the robot head",
                provider=provider,
                input_schema={
                    "type": "object",
                    "properties": {
                        "yaw": {"type": "number"},
                        "pitch": {"type": "number"},
                    },
                    "required": ["yaw", "pitch"],
                },
                output_schema={"type": "object"},
                availability=CapabilityAvailability(
                    state=CapabilityAvailabilityState.AVAILABLE
                ),
                permissions=CapabilityPermissions(
                    risk=CapabilityRisk.PHYSICAL_MOTION,
                    required=("robot.motion",),
                ),
                effect=CapabilityEffect.STATE_CHANGING,
                timeout=CapabilityTimeout(
                    expected_seconds=0.2, maximum_seconds=2
                ),
            ),
        ),
        signals=(
            MedullaNodeSignal(
                name="imu.orientation",
                schema="orientation/v1",
                adapter="canonical-json-v1",
            ),
            MedullaNodeSignal(
                name="camera.frame",
                schema={"type": "object", "required": ["content_type", "data"]},
            ),
        ),
        resources=(
            MedullaNodeResource(
                resource_id="camera.front",
                description="Front camera",
                schema="image/frame",
                media_type="image/jpeg",
            ),
        ),
        health=MedullaNodeHealth(
            state=CapabilityProviderHealthState.HEALTHY,
            details={"temperature_c": 42.5},
        ),
        security=MedullaNodeSecurity(
            pairing_required=True,
            authentication_methods=("mutual_tls",),
            pairing={"state": "unpaired"},
        ),
    )


class AllowPermissions:
    async def __call__(
        self, action: Action, capability: Capability
    ) -> CapabilityPermissionDecision:
        return CapabilityPermissionDecision(allowed=True)


class RecordingDispatcher:
    def __init__(self) -> None:
        self.transport_ids: list[str | None] = []

    async def dispatch(
        self, action: Action, *, transport_id: str | None = None
    ) -> ActionDispatchResult:
        self.transport_ids.append(transport_id)
        return ActionDispatchResult(
            transport_id=transport_id or "missing",
            action_id=action.id,
            state=ActionDispatchState.COMPLETED,
        )


class FakeSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | bytes | BaseException] = asyncio.Queue()
        self.sent: list[str] = []

    async def recv(self) -> str | bytes:
        item = await self.incoming.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def send(self, message: str) -> None:
        self.sent.append(message)


class FakeConnection:
    def __init__(self, socket: FakeSocket | None = None, error: Exception | None = None) -> None:
        self.socket = socket
        self.error = error

    async def __aenter__(self) -> FakeSocket:
        if self.error is not None:
            raise self.error
        assert self.socket is not None
        return self.socket

    async def __aexit__(self, *args: object) -> None:
        return None


class ScriptedConnector:
    def __init__(self, outcomes: list[FakeSocket | Exception]) -> None:
        self.outcomes = deque(outcomes)

    def __call__(self, *_args: object, **_kwargs: object) -> FakeConnection:
        if not self.outcomes:
            return FakeConnection(error=ConnectionError("offline"))
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            return FakeConnection(error=outcome)
        return FakeConnection(socket=outcome)


async def eventually(predicate, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


class NodeManifestTests(unittest.TestCase):
    def test_manifest_and_wire_message_round_trip_as_plain_json(self) -> None:
        manifest = make_manifest()

        message = node_manifest_message(manifest, message_id="manifest-1")
        encoded = encode_wire_message(message)
        decoded = decode_wire_message(encoded)
        restored = node_manifest_from_message(decoded)

        self.assertEqual(restored, manifest)
        self.assertEqual(json.loads(encoded)["payload"]["manifest"]["protocol"], "medulla/1")
        self.assertEqual(restored.signals[0].signal_type, "imu.orientation")
        self.assertEqual(restored.signals[0].adapter, "canonical-json-v1")
        self.assertEqual(restored.capabilities[0].provider, restored.provider)

    def test_unsupported_versions_and_protocols_fail_safely(self) -> None:
        data = make_manifest().to_dict()
        data["protocol"] = "medulla/2"
        with self.assertRaises(NodeProtocolError) as raised:
            MedullaNodeManifest.from_dict(data)
        self.assertEqual(raised.exception.code, "unsupported_version")

        data["protocol"] = "other/1"
        with self.assertRaises(NodeProtocolError) as raised:
            MedullaNodeManifest.from_dict(data)
        self.assertEqual(raised.exception.code, "unsupported_protocol")

    def test_manifest_is_closed_json_data_and_never_loads_remote_code(self) -> None:
        data = make_manifest().to_dict()
        data["implementation"] = "remote.module:Controller"
        with self.assertRaisesRegex(NodeProtocolError, "unknown fields"):
            MedullaNodeManifest.from_dict(data)

        with self.assertRaisesRegex(ValueError, "unsupported type"):
            MedullaNodeIdentity(
                node_id="unsafe",
                type=MedullaNodeType.OTHER,
                metadata={"callable": object()},
            )

    def test_node_identity_provider_and_endpoint_ownership_are_consistent(self) -> None:
        manifest = make_manifest()
        wrong_provider = CapabilityProvider(
            provider_id="someone-else",
            kind=CapabilityProviderKind.WEBSOCKET,
            location=CapabilityProviderLocation.REMOTE,
        )
        with self.assertRaisesRegex(ValueError, "provider.id must match node.id"):
            MedullaNodeManifest(
                node=manifest.node,
                provider=wrong_provider,
                endpoints=manifest.endpoints,
            )


class NodeDirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_route_configuration_does_not_partially_register_node(self) -> None:
        manifest = make_manifest()
        registry = CapabilityRegistry()
        router = CapabilityRouter(registry, RecordingDispatcher(), AllowPermissions())
        directory = MedullaNodeDirectory(registry, router)

        with self.assertRaisesRegex(ValueError, "priority"):
            directory.connect(manifest, transport_id="node-websocket", priority=-1)

        self.assertIsNone(registry.provider(manifest.node.node_id))
        self.assertEqual(registry.inspect(), ())
        self.assertIsNone(directory.status(manifest.node.node_id))

    async def test_connect_disconnect_and_reconnect_update_registry_and_router(self) -> None:
        manifest = make_manifest()
        registry = CapabilityRegistry()
        dispatcher = RecordingDispatcher()
        router = CapabilityRouter(registry, dispatcher, AllowPermissions())
        directory = MedullaNodeDirectory(registry, router)

        connected = directory.connect(manifest, transport_id="node-websocket")
        first = await router.dispatch(Action(type="head.rotate"))
        disconnected = directory.disconnect(manifest.node.node_id, reason="link lost")
        unavailable = await router.dispatch(Action(type="head.rotate"))
        reconnected = directory.reconnect(manifest)
        second = await router.dispatch(Action(type="head.rotate"))

        self.assertEqual(connected.connection_state, MedullaNodeConnectionState.CONNECTED)
        self.assertEqual(first.state, ActionDispatchState.COMPLETED)
        self.assertEqual(disconnected.connection_state, MedullaNodeConnectionState.DISCONNECTED)
        self.assertEqual(
            registry.get(manifest.capabilities[0].capability_id).availability.state,
            CapabilityAvailabilityState.AVAILABLE,
        )
        self.assertEqual(unavailable.state, ActionDispatchState.FAILED)
        self.assertEqual(reconnected.connection_state, MedullaNodeConnectionState.CONNECTED)
        self.assertEqual(second.state, ActionDispatchState.COMPLETED)
        self.assertEqual(dispatcher.transport_ids, ["node-websocket", "node-websocket"])
        self.assertEqual(directory.to_dict()["protocol"], NODE_PROTOCOL)

    async def test_websocket_manifest_and_disconnect_hooks_drive_presence(self) -> None:
        manifest = make_manifest()
        registry = CapabilityRegistry()
        directory = MedullaNodeDirectory(registry)
        socket = FakeSocket()
        connector = ScriptedConnector([socket])
        transport = WebSocketTransport(
            "wss://bit-head.local/medulla",
            transport_id="node-websocket",
            connector=connector,
            reconnect_initial_delay=0.01,
            reconnect_max_delay=0.02,
            manifest_handler=lambda message: directory.handle_manifest_message(
                message, transport_id="node-websocket"
            ),
            node_disconnected_handler=lambda node_id: directory.disconnect(node_id),
        )
        await transport.start()
        try:
            self.assertTrue(await transport.wait_connected(timeout=1))
            hello = make_wire_message(
                WireMessageType.HELLO,
                {"node_id": manifest.node.node_id, "role": "medulla-node"},
            )
            await socket.incoming.put(encode_wire_message(hello))
            unsupported = manifest.to_dict()
            unsupported["protocol"] = "medulla/2"
            await socket.incoming.put(
                encode_wire_message(
                    make_wire_message(
                        WireMessageType.MANIFEST, {"manifest": unsupported}
                    )
                )
            )
            await eventually(lambda: transport.status().messages_rejected == 1)
            error = decode_wire_message(socket.sent[-1])
            self.assertEqual(error.type, WireMessageType.ERROR)
            self.assertEqual(error.payload["code"], "unsupported_version")
            self.assertTrue(transport.status().connected)

            await socket.incoming.put(encode_wire_message(node_manifest_message(manifest)))
            await eventually(
                lambda: registry.get(manifest.capabilities[0].capability_id)
                is not None
            )

            await socket.incoming.put(ConnectionError("link lost"))
            await eventually(
                lambda: directory.status(manifest.node.node_id).connection_state
                is MedullaNodeConnectionState.DISCONNECTED
            )

            registered = registry.get(manifest.capabilities[0].capability_id)
            self.assertEqual(
                registered.availability.state,
                CapabilityAvailabilityState.UNAVAILABLE,
            )
            self.assertEqual(
                registry.health(manifest.node.node_id).state,
                CapabilityProviderHealthState.UNAVAILABLE,
            )
        finally:
            await transport.stop()


if __name__ == "__main__":
    unittest.main()
