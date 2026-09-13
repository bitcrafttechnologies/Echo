from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echo import Action, Signal
from echo.medulla import (
    ActionDispatchState,
    MQTTBrokerConfig,
    MQTTConnectionState,
    MQTTQoS,
    MQTTTopicMap,
    MQTTTransport,
    TransportErrorCode,
    TransportHealthState,
    TransportLifecycle,
    WireMessageType,
    decode_wire_message,
    encode_wire_message,
    make_wire_message,
)


class FakeMQTTMessage:
    def __init__(self, payload: bytes | str) -> None:
        self.payload = payload


class FakeMQTTClient:
    def __init__(self, *, enter_error: Exception | None = None) -> None:
        self.enter_error = enter_error
        self.incoming: asyncio.Queue[FakeMQTTMessage | BaseException] = asyncio.Queue()
        self.subscriptions: list[tuple[str, int]] = []
        self.publications: list[tuple[str, bytes | str, int, bool]] = []
        self.publish_error: Exception | None = None

    async def __aenter__(self) -> FakeMQTTClient:
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    @property
    def messages(self) -> FakeMQTTClient:
        return self

    def __aiter__(self) -> FakeMQTTClient:
        return self

    async def __anext__(self) -> FakeMQTTMessage:
        item = await self.incoming.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def subscribe(self, topic: str, *, qos: int) -> None:
        self.subscriptions.append((topic, qos))

    async def publish(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: int,
        retain: bool,
    ) -> None:
        if self.publish_error is not None:
            error = self.publish_error
            self.publish_error = None
            raise error
        self.publications.append((topic, payload, qos, retain))


class FailActionPublishClient(FakeMQTTClient):
    async def publish(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: int,
        retain: bool,
    ) -> None:
        if decode_wire_message(payload).type is WireMessageType.ACTION:
            raise ConnectionError("dropped during Action publish")
        await super().publish(topic, payload, qos=qos, retain=retain)


class ScriptedClientFactory:
    def __init__(self, clients: list[FakeMQTTClient]) -> None:
        self.clients = deque(clients)
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> FakeMQTTClient:
        self.calls.append(kwargs)
        if not self.clients:
            return FakeMQTTClient(enter_error=ConnectionError("no scripted broker"))
        return self.clients.popleft()


async def eventually(predicate, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


class MQTTConfigurationTests(unittest.TestCase):
    def test_topic_mapping_is_explicit_and_action_types_are_one_level(self) -> None:
        topics = MQTTTopicMap(entity_id="bit", prefix="lab/echo")

        self.assertEqual(topics.signal_subscription, "lab/echo/bit/signals/#")
        self.assertEqual(topics.action_prefix, "lab/echo/bit/actions")
        self.assertEqual(topics.action_topic("screen/show+text"), "lab/echo/bit/actions/screen%2Fshow%2Btext")
        self.assertEqual(topics.status_topic, "lab/echo/bit/status/transport")

        with self.assertRaisesRegex(ValueError, "without wildcards"):
            MQTTTopicMap(entity_id="+")

    def test_broker_configuration_is_explicit_and_secret_safe(self) -> None:
        config = MQTTBrokerConfig(
            host="broker.example",
            port=8883,
            client_id="echo-bit",
            tls=True,
            username="echo",
            password="secret",
        )

        self.assertEqual(config.host, "broker.example")
        self.assertEqual(config.port, 8883)
        self.assertNotIn("secret", repr(config))

        with self.assertRaisesRegex(ValueError, "URI scheme"):
            MQTTBrokerConfig(host="mqtt://broker.example", client_id="echo-bit")


class MQTTTransportTests(unittest.IsolatedAsyncioTestCase):
    def create_transport(
        self,
        factory: ScriptedClientFactory,
        **kwargs: object,
    ) -> MQTTTransport:
        return MQTTTransport(
            MQTTBrokerConfig(
                host="broker.example",
                port=8883,
                client_id="echo-bit",
                tls=True,
                username="device",
                password="secret",
            ),
            MQTTTopicMap(entity_id="bit"),
            client_factory=factory,
            reconnect_initial_delay=0.01,
            reconnect_max_delay=0.02,
            **kwargs,
        )

    async def test_signal_in_action_out_and_status_topic(self) -> None:
        client = FakeMQTTClient()
        factory = ScriptedClientFactory([client])
        transport = self.create_transport(factory)
        await transport.start()
        self.assertTrue(await transport.wait_connected(timeout=1))

        signal = Signal(
            id="sensor-1",
            type="air.temperature",
            source="mqtt-sensor",
            payload={"celsius": 22.5},
        )
        wire_signal = make_wire_message(
            WireMessageType.SIGNAL, {"signal": signal.to_dict()}
        )
        await client.incoming.put(FakeMQTTMessage(encode_wire_message(wire_signal)))
        received = await asyncio.wait_for(transport.receive(), timeout=1)

        result = await transport.execute(
            Action(id="action-1", type="display/show", parameters={"text": "Hi"})
        )
        await eventually(lambda: len(client.publications) >= 2)
        status_publish, action_publish = client.publications[:2]
        action_wire = decode_wire_message(action_publish[1])

        self.assertEqual(client.subscriptions, [("echo/bit/signals/#", 1)])
        self.assertEqual(status_publish[0], "echo/bit/status/transport")
        self.assertFalse(status_publish[3])
        self.assertEqual(action_publish[0], "echo/bit/actions/display%2Fshow")
        self.assertFalse(action_publish[3])
        self.assertEqual(action_wire.type, WireMessageType.ACTION)
        self.assertEqual(received.id, "sensor-1")
        self.assertEqual(result.state, ActionDispatchState.ACCEPTED)
        await transport.stop()

    async def test_explicit_broker_options_are_passed_without_status_secret(self) -> None:
        client = FakeMQTTClient()
        factory = ScriptedClientFactory([client])
        transport = self.create_transport(factory)
        await transport.start()
        self.assertTrue(await transport.wait_connected(timeout=1))

        call = factory.calls[0]
        self.assertEqual(call["hostname"], "broker.example")
        self.assertEqual(call["port"], 8883)
        self.assertEqual(call["identifier"], "echo-bit")
        self.assertEqual(call["username"], "device")
        self.assertEqual(call["password"], "secret")
        self.assertIsNotNone(call["tls_context"])
        self.assertNotIn("secret", repr(transport.status()))
        await transport.stop()

    async def test_initial_broker_failure_is_contained_and_reconnects(self) -> None:
        failed = FakeMQTTClient(enter_error=ConnectionError("broker offline"))
        connected = FakeMQTTClient()
        factory = ScriptedClientFactory([failed, connected])
        transport = self.create_transport(factory)

        status = await transport.start()
        self.assertEqual(status.lifecycle, TransportLifecycle.RUNNING)
        self.assertTrue(await transport.wait_connected(timeout=1))
        status = transport.status()

        self.assertEqual(status.connection_state, MQTTConnectionState.CONNECTED)
        self.assertGreaterEqual(status.reconnect_attempts, 1)
        self.assertEqual(status.connections_established, 1)
        self.assertEqual((await transport.health()).state, TransportHealthState.HEALTHY)
        await transport.stop()

    async def test_malformed_payload_is_rejected_without_disconnect(self) -> None:
        client = FakeMQTTClient()
        factory = ScriptedClientFactory([client])
        transport = self.create_transport(factory)
        await transport.start()
        self.assertTrue(await transport.wait_connected(timeout=1))

        await client.incoming.put(FakeMQTTMessage(b"not-json"))
        await client.incoming.put(
            FakeMQTTMessage(
                encode_wire_message(
                    make_wire_message(WireMessageType.ACTION, {"action": {}})
                )
            )
        )
        await eventually(lambda: transport.status().messages_rejected == 2)

        self.assertTrue(transport.status().connected)
        self.assertEqual(transport.status().last_error.code, TransportErrorCode.INVALID_PAYLOAD)
        await transport.stop()

    async def test_action_queued_offline_is_delivered_after_reconnect(self) -> None:
        failed = FakeMQTTClient(enter_error=ConnectionError("offline"))
        connected = FakeMQTTClient()
        factory = ScriptedClientFactory([failed, connected])
        transport = self.create_transport(factory)
        await transport.start()

        dispatch = await transport.execute(Action(id="queued-1", type="relay.on"))
        self.assertFalse(dispatch.result["connected"])
        self.assertTrue(await transport.wait_connected(timeout=1))
        await eventually(lambda: len(connected.publications) >= 2)

        action = decode_wire_message(connected.publications[1][1])
        self.assertEqual(action.payload["action"]["id"], "queued-1")
        await transport.stop()

    async def test_action_in_flight_during_broker_loss_is_retried(self) -> None:
        first = FailActionPublishClient()
        second = FakeMQTTClient()
        factory = ScriptedClientFactory([first, second])
        transport = self.create_transport(factory, outbound_capacity=1)
        await transport.start()
        self.assertTrue(await transport.wait_connected(timeout=1))
        await transport.execute(Action(id="retry-1", type="relay.on"))

        await eventually(lambda: transport.status().connections_established == 2)
        await eventually(lambda: len(second.publications) >= 2)
        action = decode_wire_message(second.publications[1][1])

        self.assertEqual(action.payload["action"]["id"], "retry-1")
        self.assertEqual(transport.status().outbound_depth, 0)
        await transport.stop()

    async def test_disconnected_mqtt_does_not_prevent_other_transports(self) -> None:
        from echo import Entity, LocalQueueTransport, MedullaSupervisor, Runtime

        mqtt_factory = ScriptedClientFactory(
            [FakeMQTTClient(enter_error=ConnectionError("offline"))]
        )
        mqtt = self.create_transport(mqtt_factory)
        local = LocalQueueTransport("local")
        runtime = Runtime([Entity("bit")])
        supervisor = MedullaSupervisor(
            runtime,
            [mqtt, local],
            default_transport_id="local",
        )

        status = await supervisor.start()
        await eventually(lambda: mqtt.status().last_error is not None)
        result = await supervisor.dispatch(Action(type="local.work"))
        health = await supervisor.health()

        self.assertEqual(status.lifecycle.value, "running")
        self.assertEqual(result.state, ActionDispatchState.ACCEPTED)
        self.assertEqual(health.state, TransportHealthState.DEGRADED)
        self.assertTrue(runtime.running)
        await supervisor.stop()


if __name__ == "__main__":
    unittest.main()
