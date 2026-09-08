from __future__ import annotations

import asyncio
import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    EmitSignalRequest,
    Entity,
    InvalidRequestError,
    InvalidSubscriptionError,
    Runtime,
    RuntimeEventCategory,
    RuntimeService,
    RuntimeSubscriptionRequest,
    Signal,
    SignalEmissionError,
)


class RuntimeEventSubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bit = Entity("bit", state={"mode": "idle"})
        self.runtime = Runtime([self.bit])
        self.service = RuntimeService(self.runtime)

        @self.bit.on("observe")
        async def observe(signal: Signal):
            self.bit.state["mode"] = "observed"
            return await self.bit.action(
                "remember", value=signal.payload["value"]
            )

        @self.bit.on("broken")
        async def broken(signal: Signal) -> None:
            raise ValueError("consumer test failure")

    async def test_consumer_receives_ordered_runtime_activity(self) -> None:
        subscription = self.service.subscribe_events(
            RuntimeSubscriptionRequest(max_queue_size=50)
        )

        signal = Signal(type="observe", payload={"value": 7})
        await self.service.emit_signal(EmitSignalRequest(signal=signal))
        with self.assertRaises(SignalEmissionError):
            await self.service.emit_signal(
                EmitSignalRequest(signal=Signal(type="broken"))
            )
        self.runtime.stop()

        events = [
            await subscription.get() for _ in range(subscription.pending_count)
        ]
        sequences = [event.sequence for event in events]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(len(sequences), len(set(sequences)))

        categories = {event.category for event in events}
        self.assertEqual(
            categories,
            {
                RuntimeEventCategory.SIGNAL_RECEIVED,
                RuntimeEventCategory.SIGNAL_ROUTED,
                RuntimeEventCategory.TASK_LIFECYCLE,
                RuntimeEventCategory.ACTION_LIFECYCLE,
                RuntimeEventCategory.STATE_CHANGED,
                RuntimeEventCategory.RUNTIME_CHANGED,
                RuntimeEventCategory.ERROR,
            },
        )
        event_types = [event.event.event_type.value for event in events]
        self.assertEqual(
            event_types[:8],
            [
                "signal.received",
                "signal.routed",
                "task.created",
                "task.status_changed",
                "action.created",
                "action.executed",
                "task.status_changed",
                "state.changed",
            ],
        )
        self.assertEqual(event_types[-2:], ["error", "runtime.stopped"])
        self.assertEqual(events[0].to_dict()["event"]["signal_id"], signal.id)
        subscription.close()

    async def test_category_filter_only_delivers_matching_events(self) -> None:
        subscription = self.service.subscribe_events(
            RuntimeSubscriptionRequest(
                categories=(RuntimeEventCategory.SIGNAL_RECEIVED,)
            )
        )

        await self.service.emit_signal(
            EmitSignalRequest(
                signal=Signal(type="observe", payload={"value": 1})
            )
        )

        self.assertEqual(subscription.pending_count, 1)
        event = await subscription.get()
        self.assertEqual(event.category, RuntimeEventCategory.SIGNAL_RECEIVED)
        subscription.close()

    async def test_slow_subscriber_is_bounded_and_does_not_block_runtime(self) -> None:
        subscription = self.service.subscribe_events(
            RuntimeSubscriptionRequest(max_queue_size=2)
        )
        keep_oldest = self.service.subscribe_events(
            RuntimeSubscriptionRequest(
                max_queue_size=2,
                backpressure="drop_newest",
            )
        )

        for index in range(5):
            await self.service.emit_signal(
                EmitSignalRequest(signal=Signal(type=f"unhandled.{index}"))
            )

        self.assertEqual(subscription.pending_count, 2)
        self.assertEqual(subscription.dropped_count, 3)
        retained = [await subscription.get(), await subscription.get()]
        self.assertLess(retained[0].sequence, retained[1].sequence)
        self.assertEqual(
            [event.event.metadata["signal_type"] for event in retained],
            ["unhandled.3", "unhandled.4"],
        )
        first_retained = [await keep_oldest.get(), await keep_oldest.get()]
        self.assertEqual(keep_oldest.dropped_count, 3)
        self.assertEqual(
            [event.event.metadata["signal_type"] for event in first_retained],
            ["unhandled.0", "unhandled.1"],
        )
        subscription.close()
        keep_oldest.close()

    async def test_failed_consumer_cannot_crash_or_block_runtime(self) -> None:
        subscription = self.service.subscribe_events(
            RuntimeSubscriptionRequest(max_queue_size=1)
        )

        async def failed_consumer() -> None:
            await subscription.get()
            raise RuntimeError("subscriber failed")

        consumer = asyncio.create_task(failed_consumer())
        await self.service.emit_signal(
            EmitSignalRequest(signal=Signal(type="first.unhandled"))
        )
        with self.assertRaisesRegex(RuntimeError, "subscriber failed"):
            await consumer

        for index in range(3):
            await self.service.emit_signal(
                EmitSignalRequest(signal=Signal(type=f"after.failure.{index}"))
            )

        self.assertTrue(self.runtime.running)
        self.assertLessEqual(subscription.pending_count, 1)
        self.assertGreater(subscription.dropped_count, 0)
        subscription.close()

    async def test_invalid_subscription_requests_fail_cleanly(self) -> None:
        with self.assertRaises(InvalidSubscriptionError) as invalid:
            RuntimeSubscriptionRequest(max_queue_size=0)
        self.assertEqual(invalid.exception.to_dict()["code"], "invalid_subscription")

        with self.assertRaises(InvalidRequestError):
            self.service.subscribe_events("not a request")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
