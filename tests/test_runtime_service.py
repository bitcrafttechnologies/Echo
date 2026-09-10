from __future__ import annotations

import asyncio
import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    ActionQuery,
    EmitSignalRequest,
    Entity,
    InvalidRequestError,
    LogQuery,
    ResourceNotFoundError,
    Runtime,
    RuntimeService,
    SetStateValuesRequest,
    Signal,
    SignalEmissionError,
    SignalQuery,
    StateUpdateNotAllowedError,
    TaskNotCancellableError,
    TaskQuery,
    TaskStatus,
)


class RuntimeServiceLiveRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bit = Entity("bit", state={"mode": "idle", "battery": 1.0})
        self.runtime = Runtime([self.bit])
        self.service = RuntimeService(
            self.runtime,
            allowed_state_keys={"bit": {"mode", "battery"}},
        )

        @self.bit.on("observe")
        async def observe(signal: Signal):
            self.bit.state["mode"] = "observed"
            return await self.bit.action("remember", value=signal.payload["value"])

    async def test_every_service_operation_against_a_live_runtime(self) -> None:
        status = self.service.get_runtime_status()
        self.assertEqual(status.status, "idle")
        self.assertEqual(status.runtime_id, self.runtime.id)

        entities = self.service.get_entities()
        self.assertEqual([entity.id for entity in entities], ["bit"])
        inspected_entity = self.service.inspect_entity("bit")
        self.assertEqual(inspected_entity.handlers["count"], 1)

        signal = Signal(type="observe", source="test", payload={"value": 7})
        emitted = await self.service.emit_signal(
            EmitSignalRequest(signal=signal, priority="high")
        )
        self.assertEqual(emitted.id, signal.id)
        self.assertEqual(emitted.routing_result.status, "completed")

        signals = self.service.get_recent_signals(
            SignalQuery(limit=1, signal_type="observe", source="test")
        )
        self.assertEqual([entry.id for entry in signals], [signal.id])
        self.assertEqual(self.service.inspect_signal(signal.id).payload["value"], 7)

        tasks = self.service.get_tasks(TaskQuery(status="completed"))
        self.assertEqual(len(tasks), 1)
        task = self.service.inspect_task(tasks[0].id)
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(task.signal_id, signal.id)

        actions = self.service.get_actions(
            ActionQuery(action_type="remember", task_id=task.id, signal_id=signal.id)
        )
        self.assertEqual(len(actions), 1)
        action = self.service.inspect_action(actions[0].id)
        self.assertEqual(action.parameters, {"value": 7})

        state = self.service.get_entity_state("bit")
        self.assertEqual(state.values["mode"], "observed")
        state.values["mode"] = "tampered"
        self.assertEqual(self.bit.state["mode"], "observed")

        updated = self.service.set_allowed_state_values(
            SetStateValuesRequest(entity_id="bit", values={"mode": "ready"})
        )
        self.assertEqual(updated.values["mode"], "ready")
        self.assertEqual(self.bit.state["mode"], "ready")

        logs = self.service.get_logs(
            LogQuery(event_type="state.changed", entity_id="bit")
        )
        self.assertGreaterEqual(len(logs.events), 2)
        self.assertEqual(logs.events[0]["metadata"]["operation"], "runtime_service")

    async def test_cancel_task_cancels_the_live_handler(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        @self.bit.on("wait")
        async def wait(signal: Signal) -> None:
            started.set()
            await release.wait()

        signal = Signal(type="wait")
        emission = asyncio.create_task(
            self.service.emit_signal(EmitSignalRequest(signal=signal))
        )
        await started.wait()
        running = self.service.get_tasks(TaskQuery(status="running"))[0]

        cancelled = await self.service.cancel_task(running.id)

        self.assertEqual(cancelled.status, TaskStatus.CANCELLED)
        with self.assertRaises(asyncio.CancelledError):
            await emission
        self.assertEqual(
            self.service.inspect_signal(signal.id).routing_result.status,
            "cancelled",
        )

    async def test_failures_are_clean_domain_errors(self) -> None:
        with self.assertRaises(ResourceNotFoundError) as missing:
            self.service.inspect_entity("missing")
        self.assertEqual(missing.exception.to_dict()["code"], "not_found")

        with self.assertRaises(StateUpdateNotAllowedError) as denied:
            self.service.set_allowed_state_values(
                SetStateValuesRequest(entity_id="bit", values={"secret": True})
            )
        self.assertEqual(denied.exception.details["keys"], ["secret"])
        self.assertNotIn("secret", self.bit.state)

        with self.assertRaises(InvalidRequestError):
            self.service.get_recent_signals(SignalQuery(limit=-1))

        @self.bit.on("broken")
        async def broken(signal: Signal) -> None:
            raise ValueError("handler broke")

        with self.assertRaises(SignalEmissionError) as failed:
            await self.service.emit_signal(
                EmitSignalRequest(signal=Signal(type="broken"))
            )
        self.assertEqual(failed.exception.details["error_type"], "ValueError")

        await self.service.emit_signal(
            EmitSignalRequest(
                signal=Signal(type="observe", payload={"value": 1})
            )
        )
        completed = self.service.get_tasks()[0]
        with self.assertRaises(TaskNotCancellableError):
            await self.service.cancel_task(completed.id)

        for inspect, identifier in (
            (self.service.inspect_signal, "missing-signal"),
            (self.service.inspect_task, "missing-task"),
            (self.service.inspect_action, "missing-action"),
        ):
            with self.assertRaises(ResourceNotFoundError):
                inspect(identifier)


if __name__ == "__main__":
    unittest.main()
