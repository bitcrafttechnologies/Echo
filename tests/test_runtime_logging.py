from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    Entity,
    InMemoryLogSink,
    Runtime,
    RuntimeEventType,
    RuntimeLogSeverity,
    RuntimeLogEvent,
    Signal,
)


class RuntimeLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_transitions_produce_structured_events(self) -> None:
        bit = Entity("bit", state={"battery": 1.0})
        sink = InMemoryLogSink()
        runtime = Runtime([bit], log_sink=sink)

        @bit.on("battery.low")
        async def battery_low(signal: Signal):
            bit.state["battery"] = signal.payload["percent"]
            return await bit.action("speak", text="I should charge soon.")

        signal = Signal(type="battery.low", payload={"percent": 0.08})
        await runtime.emit(signal)
        runtime.stop()

        event_types = [event.event_type for event in sink.events]
        for expected in (
            RuntimeEventType.RUNTIME_STARTED,
            RuntimeEventType.SIGNAL_RECEIVED,
            RuntimeEventType.SIGNAL_ROUTED,
            RuntimeEventType.TASK_CREATED,
            RuntimeEventType.TASK_STATUS_CHANGED,
            RuntimeEventType.ACTION_CREATED,
            RuntimeEventType.ACTION_EXECUTED,
            RuntimeEventType.STATE_CHANGED,
            RuntimeEventType.RUNTIME_STOPPED,
        ):
            self.assertIn(expected, event_types)

        task = runtime.tasks[0]
        action = runtime.actions[0]
        related = sink.query(task_id=task.id)
        self.assertTrue(related)
        self.assertTrue(all(event.entity_id == bit.id for event in related))
        self.assertTrue(all(event.signal_id == signal.id for event in related))
        self.assertTrue(all(isinstance(event.metadata, dict) for event in sink.events))
        self.assertTrue(all(event.timestamp.tzinfo is not None for event in sink.events))

        action_events = sink.query(action_id=action.id)
        self.assertEqual(
            [event.event_type for event in action_events],
            [RuntimeEventType.ACTION_CREATED, RuntimeEventType.ACTION_EXECUTED],
        )
        self.assertTrue(all(event.task_id == task.id for event in action_events))

        statuses = sink.query(
            event_type=RuntimeEventType.TASK_STATUS_CHANGED,
            task_id=task.id,
        )
        self.assertEqual(
            [event.metadata["status"] for event in statuses],
            ["running", "completed"],
        )
        state_event = sink.query(event_type="state.changed")[0]
        self.assertEqual(
            state_event.metadata["changes"]["battery"],
            {"operation": "updated", "before": 1.0, "after": 0.08},
        )

    async def test_handler_exception_is_captured_as_structured_error(self) -> None:
        bit = Entity("bit")
        sink = InMemoryLogSink()
        runtime = Runtime([bit], log_sink=sink)

        @bit.on("broken")
        async def broken(signal: Signal) -> None:
            raise ValueError("sensor failed")

        signal = Signal(type="broken")
        with self.assertRaisesRegex(ValueError, "sensor failed"):
            await runtime.emit(signal)

        error = sink.query(event_type=RuntimeEventType.ERROR)[0]
        self.assertEqual(error.entity_id, "bit")
        self.assertEqual(error.signal_id, signal.id)
        self.assertEqual(error.task_id, runtime.tasks[0].id)
        self.assertEqual(error.metadata["error_type"], "ValueError")
        self.assertEqual(error.metadata["message"], "sensor failed")
        self.assertEqual(error.severity, RuntimeLogSeverity.ERROR)
        self.assertEqual(sink.query(severity="error"), (error,))
        self.assertEqual(error.to_dict()["severity"], "error")

    async def test_query_returns_events_in_chronological_order(self) -> None:
        sink = InMemoryLogSink()
        later = RuntimeLogEvent(
            event_type=RuntimeEventType.RUNTIME_STOPPED,
            timestamp=datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc),
        )
        earlier = RuntimeLogEvent(
            event_type=RuntimeEventType.RUNTIME_STARTED,
            timestamp=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        )
        sink.write(later)
        sink.write(earlier)

        queried = sink.query()
        self.assertEqual(queried, (earlier, later))

    async def test_replaceable_sink_failure_does_not_break_runtime(self) -> None:
        class BrokenSink:
            def write(self, event: RuntimeLogEvent) -> None:
                raise OSError("log destination unavailable")

        bit = Entity("bit")
        runtime = Runtime([bit], log_sink=BrokenSink())

        @bit.on("ping")
        async def ping(signal: Signal) -> None:
            bit.state["seen"] = True

        await runtime.emit(Signal(type="ping"))

        self.assertTrue(bit.state["seen"])
        self.assertEqual(runtime.tasks[0].status.value, "completed")


if __name__ == "__main__":
    unittest.main()
