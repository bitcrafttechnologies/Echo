from __future__ import annotations

import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import Entity, Runtime, Scheduler, Signal, TaskStatus


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_priority_and_fifo_ordering(self) -> None:
        scheduler = Scheduler()
        normal_one = Signal(type="normal.one")
        normal_two = Signal(type="normal.two")
        critical = Signal(type="critical")

        await scheduler.schedule(normal_one)
        await scheduler.schedule(normal_two)
        await scheduler.schedule(critical, "critical")

        self.assertIs(await scheduler.next_signal(), critical)
        self.assertIs(await scheduler.next_signal(), normal_one)
        self.assertIs(await scheduler.next_signal(), normal_two)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_emit_dispatches_handler_as_task_and_records_action(self) -> None:
        bit = Entity("bit")
        runtime = Runtime([bit])

        @bit.on("battery.low")
        async def battery_low(signal: Signal):
            bit.state["battery"] = signal.payload["percent"]
            return await bit.action("speak", text="I should charge soon.")

        signal = Signal(type="battery.low", payload={"percent": 0.08})
        await runtime.emit(signal)

        self.assertEqual(bit.state["battery"], 0.08)
        self.assertEqual(runtime.signals, [signal])
        self.assertEqual(runtime.tasks[0].status, TaskStatus.COMPLETED)
        self.assertEqual(runtime.actions[0].type, "speak")
        self.assertEqual(runtime.actions[0].task_id, runtime.tasks[0].id)
        self.assertEqual(bit.active_tasks, {})

    async def test_handler_failure_marks_task_failed_and_propagates(self) -> None:
        bit = Entity("bit")
        runtime = Runtime([bit])

        @bit.on("broken")
        async def broken(signal: Signal) -> None:
            raise ValueError("sensor failed")

        with self.assertRaisesRegex(ValueError, "sensor failed"):
            await runtime.emit(Signal(type="broken"))

        self.assertEqual(runtime.tasks[0].status, TaskStatus.FAILED)
        self.assertEqual(runtime.tasks[0].error, "sensor failed")
        self.assertEqual(bit.active_tasks, {})

    async def test_entity_can_emit_nested_signal(self) -> None:
        bit = Entity("bit")
        runtime = Runtime([bit])

        @bit.on("outer")
        async def outer(signal: Signal) -> None:
            await bit.emit(Signal(type="inner"), priority="high")

        @bit.on("inner")
        async def inner(signal: Signal) -> None:
            bit.state["nested"] = True

        await runtime.emit(Signal(type="outer"))

        self.assertTrue(bit.state["nested"])
        self.assertEqual([signal.type for signal in runtime.signals], ["outer", "inner"])


if __name__ == "__main__":
    unittest.main()

