from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
import queue
import sys
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echo import Action, Signal
from echo.medulla import (
    SERIAL_FRAME_DELIMITER,
    ActionDispatchState,
    LocalQueueTransport,
    MedullaSupervisor,
    SerialConnectionState,
    SerialFrameParser,
    SerialPortConfig,
    SerialTransport,
    TransportErrorCode,
    TransportHealthState,
    TransportLifecycle,
    WireMessageType,
    decode_wire_message,
    encode_serial_frame,
    encode_serial_message,
    make_wire_message,
)


class FakeSerial:
    def __init__(self) -> None:
        self.incoming: queue.Queue[bytes | BaseException] = queue.Queue()
        self.written: list[bytes] = []
        self.closed = False
        self.write_error: Exception | None = None
        self._lock = threading.Lock()

    def read(self, size: int) -> bytes:
        if self.closed:
            raise OSError("port closed")
        try:
            item = self.incoming.get(timeout=0.01)
        except queue.Empty:
            return b""
        if isinstance(item, BaseException):
            raise item
        if len(item) <= size:
            return item
        self.incoming.put(item[size:])
        return item[:size]

    def write(self, data: bytes) -> int:
        if self.write_error is not None:
            error = self.write_error
            self.write_error = None
            raise error
        if self.closed:
            raise OSError("port closed")
        with self._lock:
            self.written.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        if self.closed:
            raise OSError("port closed")

    def close(self) -> None:
        self.closed = True


class FailActionWriteSerial(FakeSerial):
    def write(self, data: bytes) -> int:
        if b'"type":"action"' in data:
            raise OSError("cable dropped during Action write")
        return super().write(data)


class ScriptedSerialFactory:
    def __init__(self, outcomes: list[FakeSerial | Exception]) -> None:
        self.outcomes = deque(outcomes)
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> FakeSerial:
        self.calls.append(kwargs)
        if not self.outcomes:
            raise OSError("no scripted serial device")
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def eventually(predicate, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


class SerialFramingTests(unittest.TestCase):
    def test_frame_round_trip_is_incremental_and_escapes_reserved_bytes(self) -> None:
        payload = b'json with ~ delimiter and } escape'
        frame = encode_serial_frame(payload)
        parser = SerialFrameParser()

        decoded: list[bytes] = []
        for byte in frame:
            decoded.extend(parser.feed(bytes((byte,))))

        self.assertEqual(decoded, [payload])
        self.assertEqual(parser.frames_accepted, 1)
        self.assertEqual(parser.frames_rejected, 0)

    def test_noise_bad_crc_and_unsupported_version_resynchronize(self) -> None:
        bad_crc = bytearray(encode_serial_frame(b"bad"))
        bad_crc[5] ^= 0x01
        unsupported_body = bytes((2, 0, 0, 0, 0, 0, 0))
        unsupported = (
            bytes((SERIAL_FRAME_DELIMITER,))
            + unsupported_body
            + bytes((SERIAL_FRAME_DELIMITER,))
        )
        valid = encode_serial_frame(b"good")
        parser = SerialFrameParser()

        payloads = parser.feed(b"boot noise\x00" + bad_crc + unsupported + valid)

        self.assertEqual(payloads, (b"good",))
        self.assertEqual(parser.frames_accepted, 1)
        self.assertGreaterEqual(parser.frames_rejected, 2)
        self.assertGreater(parser.bytes_discarded, 0)

    def test_oversized_unterminated_noise_is_bounded(self) -> None:
        parser = SerialFrameParser(max_payload_bytes=8)
        parser.feed(bytes((SERIAL_FRAME_DELIMITER,)) + b"x" * 40)

        self.assertEqual(parser.frames_rejected, 1)
        self.assertGreater(parser.bytes_discarded, 0)
        self.assertEqual(parser.feed(encode_serial_frame(b"ok", max_payload_bytes=8)), (b"ok",))


class SerialTransportTests(unittest.IsolatedAsyncioTestCase):
    def create_transport(
        self,
        factory: ScriptedSerialFactory,
        **kwargs: object,
    ) -> SerialTransport:
        return SerialTransport(
            SerialPortConfig(
                port="/dev/ttyUSB0",
                baudrate=115200,
                read_timeout=0.02,
                write_timeout=0.1,
            ),
            serial_factory=factory,
            reconnect_initial_delay=0.01,
            reconnect_max_delay=0.02,
            **kwargs,
        )

    async def test_configured_signal_in_and_action_out(self) -> None:
        device = FakeSerial()
        factory = ScriptedSerialFactory([device])
        transport = self.create_transport(factory)
        await transport.start()
        self.assertTrue(await transport.wait_connected(timeout=1))

        signal = Signal(
            id="mcu-signal",
            type="button.pressed",
            source="mcu-1",
            payload={"pin": 4},
        )
        message = make_wire_message(
            WireMessageType.SIGNAL, {"signal": signal.to_dict()}
        )
        device.incoming.put(encode_serial_message(message))
        received = await asyncio.wait_for(transport.receive(), timeout=1)

        result = await transport.execute(
            Action(id="mcu-action", type="led.set", parameters={"on": True})
        )
        await eventually(lambda: len(device.written) == 1)
        frames = SerialFrameParser().feed(device.written[0])
        outbound = decode_wire_message(frames[0])

        self.assertEqual(factory.calls[0]["port"], "/dev/ttyUSB0")
        self.assertEqual(factory.calls[0]["baudrate"], 115200)
        self.assertEqual(received.id, "mcu-signal")
        self.assertEqual(result.state, ActionDispatchState.ACCEPTED)
        self.assertEqual(outbound.type, WireMessageType.ACTION)
        self.assertEqual(outbound.payload["action"]["id"], "mcu-action")
        await transport.stop()

    async def test_malformed_and_noisy_input_never_reaches_core(self) -> None:
        device = FakeSerial()
        factory = ScriptedSerialFactory([device])
        transport = self.create_transport(factory)
        await transport.start()
        self.assertTrue(await transport.wait_connected(timeout=1))

        bad = bytearray(encode_serial_frame(b"not-json"))
        bad[-2] ^= 0x01
        wrong_type = make_wire_message(WireMessageType.ACTION, {"action": {}})
        device.incoming.put(b"microcontroller booting...\r\n" + bad)
        device.incoming.put(encode_serial_message(wrong_type))
        await eventually(lambda: transport.status().frames_rejected >= 2)

        self.assertTrue(transport.status().connected)
        self.assertEqual(transport.status().inbound_depth, 0)
        self.assertGreater(transport.status().noise_bytes_discarded, 0)
        self.assertEqual(transport.status().last_error.code, TransportErrorCode.INVALID_PAYLOAD)
        await transport.stop()

    async def test_open_failure_is_contained_and_reconnects(self) -> None:
        device = FakeSerial()
        factory = ScriptedSerialFactory([OSError("device absent"), device])
        transport = self.create_transport(factory)

        status = await transport.start()
        self.assertEqual(status.lifecycle, TransportLifecycle.RUNNING)
        self.assertTrue(await transport.wait_connected(timeout=1))
        status = transport.status()

        self.assertEqual(status.connection_state, SerialConnectionState.CONNECTED)
        self.assertGreaterEqual(status.reconnect_attempts, 1)
        self.assertEqual(status.connections_established, 1)
        self.assertEqual((await transport.health()).state, TransportHealthState.HEALTHY)
        await transport.stop()

    async def test_queued_action_and_interrupted_write_retry_after_reconnect(self) -> None:
        first = FailActionWriteSerial()
        second = FakeSerial()
        factory = ScriptedSerialFactory([first, second])
        transport = self.create_transport(factory, outbound_capacity=1)
        await transport.start()
        self.assertTrue(await transport.wait_connected(timeout=1))
        await transport.execute(Action(id="retry-action", type="motor.stop"))

        await eventually(lambda: transport.status().connections_established == 2)
        await eventually(lambda: len(second.written) == 1)
        payload = SerialFrameParser().feed(second.written[0])[0]
        outbound = decode_wire_message(payload)

        self.assertEqual(outbound.payload["action"]["id"], "retry-action")
        self.assertEqual(transport.status().outbound_depth, 0)
        await transport.stop()

    async def test_serial_failure_does_not_prevent_other_transports(self) -> None:
        from echo import Entity, Runtime

        serial = self.create_transport(
            ScriptedSerialFactory([OSError("device absent")])
        )
        local = LocalQueueTransport("local")
        runtime = Runtime([Entity("bit")])
        supervisor = MedullaSupervisor(
            runtime,
            [serial, local],
            default_transport_id="local",
        )

        status = await supervisor.start()
        await eventually(lambda: serial.status().last_error is not None)
        result = await supervisor.dispatch(Action(type="local.work"))
        health = await supervisor.health()

        self.assertEqual(status.lifecycle.value, "running")
        self.assertEqual(result.state, ActionDispatchState.ACCEPTED)
        self.assertEqual(health.state, TransportHealthState.DEGRADED)
        self.assertTrue(runtime.running)
        await supervisor.stop()


if __name__ == "__main__":
    unittest.main()
