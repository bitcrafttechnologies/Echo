from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echo import Action, Signal
from echo.medulla import (
    ActionDispatchState,
    TransportHealthState,
    TransportLifecycle,
    WIRE_PROTOCOL,
    WIRE_VERSION,
    WebSocketConnectionState,
    WebSocketTransport,
    WireMessageType,
    WireProtocolError,
    decode_wire_message,
    encode_wire_message,
    make_wire_message,
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


class FailActionSendSocket(FakeSocket):
    async def send(self, message: str) -> None:
        decoded = decode_wire_message(message)
        if decoded.type is WireMessageType.ACTION:
            raise ConnectionError("dropped during Action send")
        await super().send(message)


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
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, uri: str, **kwargs: object) -> FakeConnection:
        self.calls.append((uri, kwargs))
        if not self.outcomes:
            return FakeConnection(error=ConnectionError("no scripted connection"))
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            return FakeConnection(error=outcome)
        return FakeConnection(socket=outcome)


async def eventually(predicate, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


class WireProtocolTests(unittest.TestCase):
    def test_versioned_signal_envelope_round_trip(self) -> None:
        signal = Signal(
            id="signal-1",
            type="sensor.temperature",
            source="device-1",
            timestamp=datetime(2026, 9, 10, tzinfo=timezone.utc),
            payload={"celsius": 23.5},
        )
        message = make_wire_message(
            WireMessageType.SIGNAL, {"signal": signal.to_dict()}, message_id="message-1"
        )

        encoded = encode_wire_message(message)
        decoded = decode_wire_message(encoded)

        self.assertEqual(decoded.protocol, WIRE_PROTOCOL)
        self.assertEqual(decoded.version, WIRE_VERSION)
        self.assertEqual(decoded.id, "message-1")
        self.assertEqual(decoded.payload["signal"]["id"], "signal-1")

    def test_unknown_malformed_and_unsafe_messages_are_rejected(self) -> None:
        base = {
            "protocol": WIRE_PROTOCOL,
            "version": WIRE_VERSION,
            "type": "future-python-object",
            "id": "message-1",
            "payload": {},
        }
        with self.assertRaisesRegex(WireProtocolError, "unknown wire message type"):
            decode_wire_message(json.dumps(base))

        base["type"] = "signal"
        base["python_class"] = "package.Type"
        with self.assertRaisesRegex(WireProtocolError, "unknown envelope fields"):
            decode_wire_message(json.dumps(base))

        with self.assertRaisesRegex(WireProtocolError, "duplicate object key"):
            decode_wire_message(
                '{"protocol":"echo.medulla","version":1,"type":"status",'
                '"id":"one","id":"two","payload":{}}'
            )

        with self.assertRaisesRegex(WireProtocolError, "size limit"):
            decode_wire_message("{}", max_bytes=1)


class WebSocketTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_signal_in_and_action_out(self) -> None:
        socket = FakeSocket()
        connector = ScriptedConnector([socket])
        transport = WebSocketTransport(
            "ws://device.test/medulla",
            connector=connector,
            reconnect_initial_delay=0.01,
            reconnect_max_delay=0.02,
        )
        await transport.start()
        self.assertTrue(await transport.wait_connected(timeout=1))

        signal = Signal(
            id="remote-signal",
            type="camera.person_detected",
            source="camera-1",
            payload={"confidence": 0.9},
        )
        inbound = make_wire_message(
            WireMessageType.SIGNAL, {"signal": signal.to_dict()}
        )
        await socket.incoming.put(encode_wire_message(inbound))
        received = await asyncio.wait_for(transport.receive(), timeout=1)

        result = await transport.execute(Action(id="action-1", type="light.on"))
        await eventually(lambda: len(socket.sent) >= 2)
        outbound = decode_wire_message(socket.sent[-1])

        self.assertEqual(received.id, "remote-signal")
        self.assertEqual(result.state, ActionDispatchState.ACCEPTED)
        self.assertEqual(outbound.type, WireMessageType.ACTION)
        self.assertEqual(outbound.payload["action"]["id"], "action-1")
        await transport.stop()

    async def test_connection_failure_is_contained_and_reconnects(self) -> None:
        socket = FakeSocket()
        connector = ScriptedConnector([ConnectionError("offline"), socket])
        transport = WebSocketTransport(
            "ws://device.test/medulla?deployment=lab",
            connector=connector,
            reconnect_initial_delay=0.01,
            reconnect_max_delay=0.02,
        )

        status = await transport.start()
        self.assertEqual(status.lifecycle, TransportLifecycle.RUNNING)
        self.assertTrue(await transport.wait_connected(timeout=1))

        status = transport.status()
        self.assertEqual(status.connection_state, WebSocketConnectionState.CONNECTED)
        self.assertEqual(status.endpoint, "ws://device.test/medulla")
        self.assertGreaterEqual(status.reconnect_attempts, 1)
        self.assertEqual(status.connections_established, 1)
        self.assertIsNotNone(status.last_error)
        self.assertEqual((await transport.health()).state, TransportHealthState.HEALTHY)
        await transport.stop()

    async def test_authentication_hook_and_protocol_rejection(self) -> None:
        socket = FakeSocket()
        connector = ScriptedConnector([socket])
        transport = WebSocketTransport(
            "wss://device.test/medulla",
            connector=connector,
            authentication_headers=lambda: {"Authorization": "Bearer secret"},
            reconnect_initial_delay=0.01,
            reconnect_max_delay=0.02,
        )
        await transport.start()
        self.assertTrue(await transport.wait_connected(timeout=1))
        self.assertEqual(
            connector.calls[0][1]["additional_headers"],
            {"Authorization": "Bearer secret"},
        )
        self.assertNotIn("secret", repr(transport.status()))

        unknown = json.dumps(
            {
                "protocol": WIRE_PROTOCOL,
                "version": WIRE_VERSION,
                "type": "not-real",
                "id": "bad-1",
                "payload": {},
            }
        )
        await socket.incoming.put(unknown)
        await eventually(lambda: transport.status().messages_rejected == 1)
        error = decode_wire_message(socket.sent[-1])

        self.assertEqual(error.type, WireMessageType.ERROR)
        self.assertEqual(error.payload["code"], "unknown_message_type")
        self.assertTrue(transport.status().connected)

        malformed_hello = make_wire_message(WireMessageType.HELLO, {"role": "device"})
        await socket.incoming.put(encode_wire_message(malformed_hello))
        await eventually(lambda: transport.status().messages_rejected == 2)
        hello_error = decode_wire_message(socket.sent[-1])
        self.assertEqual(hello_error.payload["code"], "invalid_payload")
        self.assertTrue(transport.status().connected)
        await transport.stop()

    async def test_action_queued_while_disconnected_is_sent_after_reconnect(self) -> None:
        socket = FakeSocket()
        connector = ScriptedConnector([ConnectionError("offline"), socket])
        transport = WebSocketTransport(
            "ws://device.test/medulla",
            connector=connector,
            reconnect_initial_delay=0.02,
            reconnect_max_delay=0.02,
        )
        await transport.start()
        dispatch = await transport.execute(Action(id="queued-action", type="display.text"))
        self.assertFalse(dispatch.result["connected"])

        self.assertTrue(await transport.wait_connected(timeout=1))
        await eventually(lambda: len(socket.sent) >= 2)
        sent = [decode_wire_message(item) for item in socket.sent]

        self.assertEqual(sent[0].type, WireMessageType.HELLO)
        self.assertEqual(sent[1].payload["action"]["id"], "queued-action")
        await transport.stop()

    async def test_action_in_flight_during_disconnect_is_retried(self) -> None:
        first = FailActionSendSocket()
        second = FakeSocket()
        connector = ScriptedConnector([first, second])
        transport = WebSocketTransport(
            "ws://device.test/medulla",
            connector=connector,
            outbound_capacity=1,
            reconnect_initial_delay=0.01,
            reconnect_max_delay=0.01,
        )
        await transport.start()
        self.assertTrue(await transport.wait_connected(timeout=1))
        await transport.execute(Action(id="retry-action", type="display.text"))

        await eventually(lambda: transport.status().connections_established == 2)
        await eventually(lambda: len(second.sent) >= 2)
        retried = decode_wire_message(second.sent[1])

        self.assertEqual(retried.type, WireMessageType.ACTION)
        self.assertEqual(retried.payload["action"]["id"], "retry-action")
        self.assertEqual(transport.status().outbound_depth, 0)
        await transport.stop()

    async def test_disconnected_running_transport_reports_degraded_health(self) -> None:
        connector = ScriptedConnector([ConnectionError("offline")])
        transport = WebSocketTransport(
            "ws://device.test/medulla",
            connector=connector,
            reconnect_initial_delay=0.1,
            reconnect_max_delay=0.1,
        )
        await transport.start()
        await eventually(lambda: transport.status().last_error is not None)

        health = await transport.health()
        self.assertEqual(health.state, TransportHealthState.DEGRADED)
        self.assertTrue(health.details["reconnect_attempts"] >= 0)
        await transport.stop()


if __name__ == "__main__":
    unittest.main()
