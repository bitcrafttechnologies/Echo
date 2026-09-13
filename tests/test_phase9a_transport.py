from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import Action, Signal
from echo.medulla import (
    ActionDispatchResult,
    ActionDispatchState,
    BaseTransport,
    Transport,
    TransportErrorCode,
    TransportHealth,
    TransportHealthState,
    TransportLifecycle,
    TransportOperation,
    TransportOperationError,
    TransportStateError,
    TransportValidationError,
    external_signal_from_dict,
)


class FakeTransport(BaseTransport):
    def __init__(self) -> None:
        super().__init__("fake")
        self.incoming: asyncio.Queue[Signal] = asyncio.Queue()
        self.started = 0
        self.stopped = 0
        self.health_error: Exception | None = None
        self.receive_error: Exception | None = None
        self.executed: list[Action] = []

    async def _start(self) -> None:
        self.started += 1

    async def _stop(self) -> None:
        self.stopped += 1

    async def _receive(self) -> Signal:
        if self.receive_error is not None:
            raise self.receive_error
        return await self.incoming.get()

    async def _execute(self, action: Action) -> ActionDispatchResult:
        self.executed.append(action)
        return ActionDispatchResult(
            transport_id=self.transport_id,
            action_id=action.id,
            state=ActionDispatchState.COMPLETED,
            result={"delivered": True},
        )

    async def _health(self) -> TransportHealth:
        if self.health_error is not None:
            raise self.health_error
        state = (
            TransportHealthState.HEALTHY
            if self.status().lifecycle is TransportLifecycle.RUNNING
            else TransportHealthState.UNAVAILABLE
        )
        return TransportHealth(
            transport_id=self.transport_id,
            lifecycle=self.status().lifecycle,
            state=state,
        )


class Phase9ATransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_structural_contract_and_idempotent_lifecycle(self) -> None:
        transport = FakeTransport()

        self.assertIsInstance(transport, Transport)
        self.assertEqual(transport.status().lifecycle, TransportLifecycle.STOPPED)

        first = await transport.start()
        second = await transport.start()
        self.assertEqual(first.lifecycle, TransportLifecycle.RUNNING)
        self.assertEqual(second.lifecycle, TransportLifecycle.RUNNING)
        self.assertEqual(transport.started, 1)

        stopped = await transport.stop()
        await transport.stop()
        self.assertEqual(stopped.lifecycle, TransportLifecycle.STOPPED)
        self.assertEqual(transport.stopped, 1)

    async def test_receive_and_execute_use_detached_validated_primitives(self) -> None:
        transport = FakeTransport()
        await transport.start()
        source = Signal(
            id="signal-1",
            type="sensor.temperature",
            source="sensor-a",
            timestamp=datetime(2026, 9, 10, tzinfo=timezone.utc),
            payload={"celsius": 22.5},
        )
        await transport.incoming.put(source)

        received = await transport.receive()
        source.payload["celsius"] = 100.0
        self.assertIs(type(received), Signal)
        self.assertEqual(received.payload, {"celsius": 22.5})

        action = Action(type="head.rotate", parameters={"degrees": 15})
        result = await transport.execute(action)
        action.parameters["degrees"] = 90
        self.assertEqual(result.state, ActionDispatchState.COMPLETED)
        self.assertEqual(transport.executed[0].parameters, {"degrees": 15})

    async def test_data_operations_require_running_state(self) -> None:
        transport = FakeTransport()

        with self.assertRaises(TransportStateError) as raised:
            await transport.receive()

        self.assertEqual(raised.exception.operation, TransportOperation.RECEIVE)
        self.assertEqual(raised.exception.code, TransportErrorCode.INVALID_STATE)

    async def test_unsafe_inbound_payload_becomes_structured_validation_error(self) -> None:
        transport = FakeTransport()
        await transport.start()
        await transport.incoming.put(
            Signal(type="unsafe", source="device", payload={"object": object()})
        )

        with self.assertRaises(TransportValidationError) as raised:
            await transport.receive()

        self.assertEqual(raised.exception.code, TransportErrorCode.INVALID_PAYLOAD)
        self.assertEqual(transport.status().last_error, raised.exception.info)

    async def test_unexpected_io_failure_is_translated(self) -> None:
        transport = FakeTransport()
        await transport.start()
        transport.receive_error = OSError("link down")

        with self.assertRaises(TransportOperationError) as raised:
            await transport.receive()

        self.assertEqual(raised.exception.operation, TransportOperation.RECEIVE)
        self.assertEqual(raised.exception.transport_id, "fake")
        self.assertEqual(raised.exception.code, TransportErrorCode.INTERNAL)

    async def test_health_probe_failure_returns_unavailable_snapshot(self) -> None:
        transport = FakeTransport()
        await transport.start()
        transport.health_error = OSError("probe failed")

        health = await transport.health()

        self.assertEqual(health.state, TransportHealthState.UNAVAILABLE)
        self.assertEqual(health.lifecycle, TransportLifecycle.RUNNING)
        self.assertIn("probe failed", health.message or "")
        self.assertIsNotNone(transport.status().last_error)

    async def test_cancellation_remains_cooperative(self) -> None:
        transport = FakeTransport()
        await transport.start()
        pending = asyncio.create_task(transport.receive())
        await asyncio.sleep(0)

        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending

        self.assertIsNone(transport.status().last_error)


class Phase9AValidationTests(unittest.TestCase):
    def test_external_signal_requires_closed_json_schema(self) -> None:
        signal = external_signal_from_dict(
            {
                "id": "remote-1",
                "type": "camera.person_detected",
                "source": "camera-1",
                "timestamp": "2026-09-10T12:00:00+00:00",
                "payload": {"confidence": 0.91},
                "metadata": {},
            }
        )
        self.assertEqual(signal.id, "remote-1")

        with self.assertRaisesRegex(ValueError, "unknown fields"):
            external_signal_from_dict(
                {
                    "id": "remote-2",
                    "type": "unsafe",
                    "source": "node",
                    "timestamp": "2026-09-10T12:00:00+00:00",
                    "payload": {},
                    "metadata": {},
                    "python_type": "module.Class",
                }
            )

    def test_failed_dispatch_result_requires_structured_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "require an error"):
            ActionDispatchResult(
                transport_id="fake",
                action_id="action-1",
                state=ActionDispatchState.FAILED,
            )


if __name__ == "__main__":
    unittest.main()
