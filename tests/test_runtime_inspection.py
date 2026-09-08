from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import Entity, Runtime, Signal


class RuntimeInspectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_snapshot_contains_required_runtime_sections(self) -> None:
        bit = Entity(
            "bit",
            state={
                "battery": 0.8,
                "observed_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
            },
        )
        runtime = Runtime([bit])

        @bit.on("ping")
        async def ping(signal: Signal) -> None:
            pass

        snapshot = runtime.inspect()

        self.assertEqual(snapshot["runtime_status"], "idle")
        self.assertGreaterEqual(snapshot["uptime_seconds"], 0.0)
        self.assertEqual(snapshot["entity_ids"], ["bit"])
        self.assertEqual(snapshot["entity_state"]["bit"]["battery"], 0.8)
        self.assertEqual(snapshot["active_tasks"], [])
        self.assertEqual(snapshot["queued_signals"], [])
        self.assertEqual(snapshot["recent_signals"], [])
        self.assertEqual(snapshot["recent_actions"], [])
        self.assertEqual(snapshot["recent_errors"], [])
        self.assertEqual(snapshot["scheduler"], {"queue_size": 0, "empty": True})
        self.assertEqual(snapshot["handler_registry"]["bit"]["count"], 1)
        self.assertEqual(
            snapshot["handler_registry"]["bit"]["registrations"][0]["signal"],
            "ping",
        )

    async def test_snapshot_changes_during_active_work_without_mutation(self) -> None:
        bit = Entity("bit", state={"phase": "idle"})
        runtime = Runtime([bit])
        started = asyncio.Event()
        release = asyncio.Event()

        @bit.on("work")
        async def work(signal: Signal) -> None:
            bit.state["phase"] = "working"
            started.set()
            await release.wait()
            await bit.action("record", value=signal.payload["value"])
            bit.state["phase"] = "done"

        processing = asyncio.create_task(
            runtime.emit(Signal(type="work", payload={"value": 1}))
        )
        await started.wait()
        queued = Signal(type="work", payload={"value": 2})
        await runtime.scheduler.schedule(queued, priority="background")

        active = runtime.snapshot()

        self.assertEqual(active["runtime_status"], "active")
        self.assertEqual(active["entity_state"]["bit"]["phase"], "working")
        self.assertEqual(len(active["active_tasks"]), 1)
        self.assertEqual(active["active_tasks"][0]["status"], "running")
        self.assertEqual(active["queued_signals"][0]["id"], queued.id)
        self.assertEqual(active["scheduler"]["queue_size"], 1)
        self.assertEqual(len(runtime.scheduler), 1)

        release.set()
        await processing
        after = runtime.inspect()

        self.assertEqual(after["runtime_status"], "idle")
        self.assertEqual(after["active_tasks"], [])
        self.assertEqual(after["entity_state"]["bit"]["phase"], "done")
        self.assertEqual(len(after["recent_signals"]), 1)
        self.assertEqual(len(after["recent_actions"]), 1)
        self.assertEqual(len(runtime.scheduler), 1)

    async def test_snapshot_is_json_safe_and_reports_recent_errors(self) -> None:
        class InternalValue:
            pass

        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        bit = Entity(
            "bit",
            state={
                "opaque": InternalValue(),
                "cycle": cyclic,
                "values": {1, 2},
            },
        )
        runtime = Runtime([bit])

        @bit.on("broken")
        async def broken(signal: Signal) -> None:
            raise ValueError("sensor failed")

        with self.assertRaisesRegex(ValueError, "sensor failed"):
            await runtime.emit(Signal(type="broken"))

        state_before = bit.state.copy()
        snapshot = runtime.inspect(recent_limit=1)
        serialized = json.dumps(snapshot, allow_nan=False)

        self.assertIn("sensor failed", serialized)
        self.assertEqual(snapshot["recent_errors"][0]["event_type"], "error")
        self.assertEqual(snapshot["recent_errors"][0]["metadata"]["error_type"], "ValueError")
        self.assertIs(bit.state["opaque"], state_before["opaque"])
        self.assertIs(bit.state["cycle"], state_before["cycle"])

    async def test_stopped_snapshot_preserves_elapsed_uptime(self) -> None:
        runtime = Runtime()
        runtime.stop()

        first = runtime.inspect()
        second = runtime.inspect()

        self.assertEqual(first["runtime_status"], "stopped")
        self.assertEqual(first["uptime_seconds"], second["uptime_seconds"])


if __name__ == "__main__":
    unittest.main()
