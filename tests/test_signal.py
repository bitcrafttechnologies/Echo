from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import Signal


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


class SignalTests(unittest.TestCase):
    def test_defaults_are_timestamped_and_independent(self) -> None:
        first = Signal(type="one")
        second = Signal(type="two")

        self.assertIsNot(first.payload, second.payload)
        self.assertIsNotNone(first.timestamp.tzinfo)

    def test_json_round_trip_preserves_transport_fields(self) -> None:
        original = Signal(
            type="battery.low",
            source="sensor-1",
            timestamp=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
            payload={"percent": 0.08},
            metadata={"trace_id": "abc"},
        )

        restored = Signal.from_json(original.to_json())

        self.assertEqual(restored, original)
        self.assertEqual(json.loads(original.to_json())["type"], "battery.low")

    def test_typed_subclass_has_clean_constructor(self) -> None:
        signal = BatteryLow(0.12)

        self.assertEqual(signal.type, "battery.low")
        self.assertEqual(signal.percent, 0.12)

    def test_naive_timestamp_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            Signal(type="invalid", timestamp=datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()

