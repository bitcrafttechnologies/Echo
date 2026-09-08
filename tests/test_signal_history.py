from __future__ import annotations

import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import Entity, Runtime, Signal


class SignalHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_processed_signal_is_recorded_with_routing_result(self) -> None:
        bit = Entity("bit")
        runtime = Runtime([bit])

        @bit.on("battery.low")
        async def battery_low(signal: Signal) -> None:
            bit.state["battery"] = signal.payload["percent"]

        signal = Signal(
            type="battery.low",
            source="battery",
            payload={"percent": 0.08},
            metadata={"sensor": {"port": 1}},
        )
        await runtime.emit(signal)

        entry = runtime.get_signal(signal.id)
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.id, signal.id)
        self.assertEqual(entry.type, "battery.low")
        self.assertEqual(entry.source, "battery")
        self.assertEqual(entry.timestamp, signal.timestamp)
        self.assertEqual(entry.payload, {"percent": 0.08})
        self.assertEqual(entry.metadata, {"sensor": {"port": 1}})
        self.assertEqual(entry.routing_result.status, "completed")
        self.assertEqual(entry.routing_result.entity_ids, ("bit",))
        self.assertEqual(entry.routing_result.handler_count, 1)
        self.assertEqual(entry.routing_result.task_ids, (runtime.tasks[0].id,))
        self.assertEqual(
            entry.routing_result.task_statuses,
            {runtime.tasks[0].id: "completed"},
        )

        signal.payload["percent"] = 0.5
        entry.metadata["sensor"]["port"] = 2
        stored_again = runtime.get_signal(signal.id)
        assert stored_again is not None
        self.assertEqual(stored_again.payload, {"percent": 0.08})
        self.assertEqual(stored_again.metadata, {"sensor": {"port": 1}})

    async def test_history_filters_by_type_and_source(self) -> None:
        runtime = Runtime()
        battery_low = Signal(type="battery.low", source="battery")
        battery_ok = Signal(type="battery.ok", source="battery")
        camera_low = Signal(type="battery.low", source="camera")

        for signal in (battery_low, battery_ok, camera_low):
            await runtime.emit(signal)

        self.assertEqual(
            [entry.id for entry in runtime.filter_signals(signal_type="battery.low")],
            [camera_low.id, battery_low.id],
        )
        self.assertEqual(
            [entry.id for entry in runtime.filter_signals(source="battery")],
            [battery_ok.id, battery_low.id],
        )
        self.assertEqual(
            [
                entry.id
                for entry in runtime.filter_signals(
                    signal_type="battery.low",
                    source="battery",
                )
            ],
            [battery_low.id],
        )

    async def test_bounded_history_evicts_oldest_signal(self) -> None:
        runtime = Runtime(signal_history_size=2)
        first = Signal(type="first")
        second = Signal(type="second")
        third = Signal(type="third")

        for signal in (first, second, third):
            await runtime.emit(signal)

        self.assertEqual(len(runtime.signal_history), 2)
        self.assertEqual(
            [entry.id for entry in runtime.latest_signals()],
            [third.id, second.id],
        )
        self.assertEqual(runtime.latest_signals(1)[0].id, third.id)
        self.assertEqual(runtime.signals, [second, third])
        self.assertIsNone(runtime.get_signal(first.id))

    async def test_failed_routing_result_remains_inspectable(self) -> None:
        bit = Entity("bit")
        runtime = Runtime([bit])

        @bit.on("broken")
        async def broken(signal: Signal) -> None:
            raise ValueError("sensor failed")

        signal = Signal(type="broken")
        with self.assertRaisesRegex(ValueError, "sensor failed"):
            await runtime.emit(signal)

        entry = runtime.get_signal(signal.id)
        assert entry is not None
        self.assertEqual(entry.routing_result.status, "failed")
        self.assertEqual(entry.routing_result.handler_count, 1)
        self.assertEqual(
            entry.routing_result.error,
            {"type": "ValueError", "message": "sensor failed"},
        )
        self.assertEqual(
            entry.routing_result.task_statuses,
            {runtime.tasks[0].id: "failed"},
        )

    async def test_unknown_or_invalid_id_returns_none(self) -> None:
        runtime = Runtime()

        self.assertIsNone(runtime.get_signal("missing"))
        self.assertIsNone(runtime.get_signal(""))

    async def test_history_size_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive integer"):
            Runtime(signal_history_size=0)


if __name__ == "__main__":
    unittest.main()
