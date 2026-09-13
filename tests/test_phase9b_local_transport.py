from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import Action, Entity, Runtime, Signal
from echo.medulla import (
    ActionDispatchState,
    LocalQueueFullError,
    LocalQueueTransport,
    QueueOverflowPolicy,
    Transport,
    TransportErrorCode,
    TransportHealthState,
    TransportLifecycle,
    TransportStateError,
)


class LocalQueueTransportFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_signal_flows_through_transport_into_echo(self) -> None:
        transport = LocalQueueTransport(inbound_capacity=2)
        entity = Entity("bit")
        runtime = Runtime([entity])

        @entity.on("sensor.temperature")
        async def observe_temperature(signal: Signal) -> None:
            entity.state["temperature"] = signal.payload["celsius"]

        await transport.start()
        await transport.publish_signal(
            {
                "id": "outside-1",
                "type": "sensor.temperature",
                "source": "local-sensor",
                "timestamp": datetime(2026, 9, 10, tzinfo=timezone.utc).isoformat(),
                "payload": {"celsius": 24.5},
                "metadata": {},
            }
        )

        signal = await transport.receive()
        await runtime.emit(signal)

        self.assertEqual(entity.state["temperature"], 24.5)
        self.assertEqual(runtime.signals[0].source, "local-sensor")

    async def test_echo_action_flows_to_external_consumer(self) -> None:
        transport = LocalQueueTransport(outbound_capacity=2)
        entity = Entity("bit")
        runtime = Runtime([entity])

        @entity.on("user.greet")
        async def greet(_: Signal) -> Action:
            return await entity.action("speak", text="Hello")

        await transport.start()
        await runtime.emit(Signal(type="user.greet"))
        action = runtime.actions[0]

        dispatch = await transport.execute(action)
        consumed = await transport.receive_action()

        self.assertEqual(dispatch.state, ActionDispatchState.ACCEPTED)
        self.assertEqual(consumed.id, action.id)
        self.assertEqual(consumed.parameters, {"text": "Hello"})
        self.assertEqual(consumed.entity_id, "bit")

    async def test_local_transport_satisfies_phase9a_contract(self) -> None:
        transport = LocalQueueTransport()
        self.assertIsInstance(transport, Transport)


class LocalQueueTransportOverflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_reject_newest_is_nonblocking_structured_and_counted(self) -> None:
        transport = LocalQueueTransport(inbound_capacity=1)
        await transport.start()
        await transport.publish_signal(Signal(id="first", type="one", source="test"))

        with self.assertRaises(LocalQueueFullError) as raised:
            await transport.publish_signal(
                Signal(id="second", type="two", source="test")
            )

        self.assertEqual(raised.exception.code, TransportErrorCode.QUEUE_FULL)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(transport.status().inbound_rejected, 1)
        self.assertEqual((await transport.receive()).id, "first")

    async def test_drop_oldest_retains_newest_and_reports_dropped_id(self) -> None:
        transport = LocalQueueTransport(
            inbound_capacity=1,
            outbound_capacity=1,
            inbound_overflow=QueueOverflowPolicy.DROP_OLDEST,
            outbound_overflow=QueueOverflowPolicy.DROP_OLDEST,
        )
        await transport.start()
        await transport.publish_signal(Signal(id="signal-1", type="one"))
        offer = await transport.publish_signal(Signal(id="signal-2", type="two"))

        first_action = Action(id="action-1", type="one")
        second_action = Action(id="action-2", type="two")
        await transport.execute(first_action)
        dispatch = await transport.execute(second_action)

        self.assertEqual(offer.dropped_id, "signal-1")
        self.assertEqual((await transport.receive()).id, "signal-2")
        self.assertEqual(dispatch.result["dropped_id"], "action-1")
        self.assertEqual((await transport.receive_action()).id, "action-2")
        status = transport.status()
        self.assertEqual(status.inbound_dropped, 1)
        self.assertEqual(status.outbound_dropped, 1)

    async def test_outbound_rejection_isolated_from_core_action_history(self) -> None:
        transport = LocalQueueTransport(outbound_capacity=1)
        entity = Entity("bit")
        runtime = Runtime([entity])
        await transport.start()
        first = await entity.action("speak", text="first")
        second = await entity.action("speak", text="second")
        await transport.execute(first)

        with self.assertRaises(LocalQueueFullError):
            await transport.execute(second)

        self.assertEqual([action.id for action in runtime.actions], [first.id, second.id])
        self.assertEqual((await transport.receive_action()).id, first.id)


class LocalQueueTransportLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_wakes_all_pending_receivers_and_consumers(self) -> None:
        transport = LocalQueueTransport()
        await transport.start()
        signal_waiters = [
            asyncio.create_task(transport.receive()) for _ in range(2)
        ]
        action_waiters = [
            asyncio.create_task(transport.receive_action()) for _ in range(2)
        ]
        await asyncio.sleep(0)

        status = await transport.stop()

        self.assertEqual(status.lifecycle, TransportLifecycle.STOPPED)
        for waiter in signal_waiters + action_waiters:
            with self.assertRaises(TransportStateError):
                await waiter

    async def test_stop_discards_stale_items_and_restart_uses_fresh_queues(self) -> None:
        transport = LocalQueueTransport()
        await transport.start()
        await transport.publish_signal(Signal(type="stale.signal"))
        await transport.execute(Action(type="stale.action"))

        await transport.stop()
        stopped = transport.status()
        self.assertEqual(stopped.inbound_depth, 0)
        self.assertEqual(stopped.outbound_depth, 0)
        self.assertEqual(stopped.inbound_discarded_on_stop, 1)
        self.assertEqual(stopped.outbound_discarded_on_stop, 1)

        await transport.start()
        self.assertEqual(transport.status().inbound_depth, 0)
        self.assertEqual(transport.status().outbound_depth, 0)

    async def test_status_and_health_report_capacity_depth_and_saturation(self) -> None:
        transport = LocalQueueTransport(inbound_capacity=1, outbound_capacity=2)
        stopped_health = await transport.health()
        self.assertEqual(stopped_health.state, TransportHealthState.UNAVAILABLE)

        await transport.start()
        healthy = await transport.health()
        self.assertEqual(healthy.state, TransportHealthState.HEALTHY)
        await transport.publish_signal(Signal(type="fills.queue"))

        status = transport.status()
        health = await transport.health()
        self.assertEqual(status.inbound_depth, 1)
        self.assertEqual(status.inbound_capacity, 1)
        self.assertEqual(health.state, TransportHealthState.DEGRADED)
        self.assertEqual(health.details["inbound_depth"], 1)

    async def test_invalid_capacity_and_operations_while_stopped_fail_cleanly(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive integer"):
            LocalQueueTransport(inbound_capacity=0)

        transport = LocalQueueTransport()
        with self.assertRaises(TransportStateError):
            await transport.publish_signal(Signal(type="not.running"))
        with self.assertRaises(TransportStateError):
            await transport.receive_action()


if __name__ == "__main__":
    unittest.main()
