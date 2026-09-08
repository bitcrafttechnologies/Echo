from __future__ import annotations

import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import Action, Entity, Runtime, Signal, TaskStatus


class BatteryLow(Signal):
    def __init__(self, percent: float, *, source: str = "battery") -> None:
        super().__init__(
            type="battery.low",
            source=source,
            payload={"percent": float(percent)},
        )

    @property
    def percent(self) -> float:
        return self.payload["percent"]


class KernelIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_typed_signal_flows_from_runtime_to_task_and_action(self) -> None:
        bit = Entity("bit", state={"battery": 1.0})
        runtime = Runtime([bit])

        @bit.on(BatteryLow)
        async def handle_low_battery(signal: BatteryLow) -> Action:
            bit.state["battery"] = signal.percent
            return await bit.action(
                "speak",
                text="I should probably charge soon.",
            )

        signal = BatteryLow(0.08)
        await runtime.emit(signal)

        self.assertIs(runtime.get_entity("bit"), bit)
        self.assertEqual(bit.state["battery"], 0.08)
        self.assertEqual(runtime.signals, [signal])
        self.assertEqual(len(runtime.tasks), 1)
        self.assertEqual(runtime.tasks[0].name, "handle_low_battery")
        self.assertEqual(runtime.tasks[0].owner, "bit")
        self.assertEqual(runtime.tasks[0].status, TaskStatus.COMPLETED)
        self.assertEqual(len(runtime.actions), 1)
        self.assertEqual(runtime.actions[0].type, "speak")
        self.assertEqual(
            runtime.actions[0].parameters,
            {"text": "I should probably charge soon."},
        )
        self.assertEqual(runtime.actions[0].entity_id, "bit")
        self.assertEqual(runtime.actions[0].task_id, runtime.tasks[0].id)

    async def test_one_signal_routes_independently_to_multiple_entities(self) -> None:
        bit = Entity("bit")
        dock = Entity("dock")
        runtime = Runtime([bit, dock])

        @bit.on("battery.low")
        async def remember_battery(signal: Signal) -> None:
            bit.state["battery"] = signal.payload["percent"]

        @dock.on(BatteryLow)
        async def prepare_charger(signal: BatteryLow) -> Action:
            return Action(type="enable.charger")

        await runtime.emit(BatteryLow(0.12))

        self.assertEqual(bit.state["battery"], 0.12)
        self.assertEqual([task.owner for task in runtime.tasks], ["bit", "dock"])
        self.assertEqual(runtime.actions[0].entity_id, "dock")


if __name__ == "__main__":
    unittest.main()

