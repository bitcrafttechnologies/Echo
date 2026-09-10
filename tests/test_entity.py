from __future__ import annotations

import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import Entity, HandlerRegistry, Signal


class BatteryLow(Signal):
    def __init__(self, percent: float) -> None:
        super().__init__(type="battery.low", payload={"percent": percent})


class EntityTests(unittest.TestCase):
    def test_entity_owns_copied_state_and_handler_registry(self) -> None:
        initial = {"battery": 1.0}
        entity = Entity("bit", state=initial)
        initial["battery"] = 0.5

        self.assertEqual(entity.id, "bit")
        self.assertEqual(entity.state["battery"], 1.0)
        self.assertIsInstance(entity.handlers, HandlerRegistry)

    def test_decorator_registers_string_handler_without_wrapping_it(self) -> None:
        entity = Entity("bit")

        @entity.on("battery.low")
        async def battery_low(signal: Signal) -> None:
            entity.state["battery"] = signal.payload["percent"]

        resolved = entity.handlers.resolve(BatteryLow(0.08))

        self.assertEqual(resolved, (battery_low,))
        self.assertEqual(len(entity.handlers), 1)

    def test_registry_resolves_signal_subclasses(self) -> None:
        registry = HandlerRegistry()

        async def any_signal(signal: Signal) -> None:
            pass

        async def battery_low(signal: Signal) -> None:
            pass

        registry.register(Signal, any_signal)
        registry.register(BatteryLow, battery_low)

        self.assertEqual(
            registry.resolve(BatteryLow(0.12)),
            (any_signal, battery_low),
        )

    def test_registry_rejects_invalid_signal_key(self) -> None:
        with self.assertRaises(TypeError):
            HandlerRegistry().register(object, lambda signal: None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()

