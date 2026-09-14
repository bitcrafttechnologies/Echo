from __future__ import annotations

import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    Action,
    ActionHistory,
    ActionStatus,
    Entity,
    Runtime,
    Signal,
    Task,
    TaskHistory,
    TaskStatus,
)


class TaskHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_transitions_are_read_from_live_task(self) -> None:
        history = TaskHistory(max_size=2)
        parent = Task(name="parent", owner="bit", priority=7)
        child = Task(name="child", owner="bit")
        parent.add_child(child)
        history.record(parent)

        entry = history.get(parent.id)
        assert entry is not None
        self.assertEqual(entry.status, TaskStatus.PENDING)
        parent.start()
        entry = history.get(parent.id)
        assert entry is not None
        self.assertEqual(entry.status, TaskStatus.RUNNING)
        parent.pause()
        entry = history.get(parent.id)
        assert entry is not None
        self.assertEqual(entry.status, TaskStatus.PAUSED)
        parent.resume()
        parent.complete({"answer": 42})

        entry = history.get(parent.id)
        assert entry is not None
        self.assertEqual(entry.status, TaskStatus.COMPLETED)
        self.assertEqual(entry.priority, 7)
        self.assertEqual(entry.children, (child.id,))
        self.assertEqual(entry.result, {"answer": 42})
        self.assertIsNotNone(entry.started_at)
        self.assertIsNotNone(entry.completed_at)

    async def test_completed_and_failed_tasks_remain_inspectable(self) -> None:
        bit = Entity("bit")
        runtime = Runtime([bit])

        @bit.on("complete")
        async def complete(signal: Signal) -> dict[str, bool]:
            return {"ok": True}

        @bit.on("fail")
        async def fail(signal: Signal) -> None:
            raise ValueError("task failed")

        completed_signal = Signal(type="complete")
        await runtime.emit(completed_signal)
        completed_task = runtime.tasks[-1]

        failed_signal = Signal(type="fail")
        with self.assertRaisesRegex(ValueError, "task failed"):
            await runtime.emit(failed_signal)
        failed_task = runtime.tasks[-1]

        completed = runtime.get_task(completed_task.id)
        failed = runtime.get_task(failed_task.id)
        assert completed is not None
        assert failed is not None
        self.assertEqual(completed.status, TaskStatus.COMPLETED)
        self.assertEqual(completed.result, {"ok": True})
        self.assertEqual(completed.signal_id, completed_signal.id)
        self.assertEqual(failed.status, TaskStatus.FAILED)
        self.assertEqual(failed.error, "task failed")
        self.assertEqual(failed.signal_id, failed_signal.id)
        self.assertEqual(
            [entry.id for entry in runtime.filter_tasks(status="failed")],
            [failed_task.id],
        )


class ActionHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_action_lifecycle_can_be_inspected_from_creation_to_execution(self) -> None:
        history = ActionHistory()
        action = Action(type="speak", task_id="task-1")
        history.record(action, signal_id="signal-1")

        created = history.get(action.id)
        assert created is not None
        self.assertEqual(created.status, ActionStatus.CREATED)
        self.assertIsNone(created.execution_time)

        history.mark_executed(action.id, result={"spoken": True})
        executed = history.get(action.id)
        assert executed is not None
        self.assertEqual(executed.status, ActionStatus.EXECUTED)
        self.assertIsNotNone(executed.execution_time)
        self.assertEqual(executed.task_id, "task-1")
        self.assertEqual(executed.signal_id, "signal-1")
        self.assertEqual(executed.result, {"spoken": True})
        self.assertIsNone(executed.error)

    async def test_runtime_action_records_task_and_signal_associations(self) -> None:
        bit = Entity("bit")
        runtime = Runtime([bit])

        @bit.on("greet")
        async def greet(signal: Signal) -> Action:
            return await bit.action("speak", text="hello")

        signal = Signal(type="greet")
        await runtime.emit(signal)
        action = runtime.actions[0]
        task = runtime.tasks[0]

        entry = runtime.get_action(action.id)
        assert entry is not None
        self.assertEqual(entry.status, ActionStatus.EXECUTED)
        self.assertEqual(entry.type, "speak")
        self.assertEqual(entry.parameters, {"text": "hello"})
        self.assertEqual(entry.task_id, task.id)
        self.assertEqual(entry.signal_id, signal.id)
        assert entry.execution_time is not None
        self.assertGreaterEqual(entry.execution_time, entry.created_at)

    async def test_external_action_remains_pending_until_outcome_arrives(self) -> None:
        bit = Entity("bit")
        runtime = Runtime([bit])

        action = await bit.begin_action("filesystem.list", resource_id="filesystem.root.0")
        pending = runtime.get_action(action.id)
        assert pending is not None
        self.assertEqual(pending.status, ActionStatus.CREATED)
        self.assertIsNone(pending.execution_time)

        completed = runtime.complete_action(action.id, result={"entries": []})
        assert completed is not None
        self.assertEqual(completed.status, ActionStatus.EXECUTED)
        self.assertEqual(completed.result, {"entries": []})

    async def test_action_failure_fields_are_exposed(self) -> None:
        history = ActionHistory()
        action = Action(type="speak")
        history.record(action)
        history.mark_failed(action.id, RuntimeError("speaker unavailable"))

        entry = history.get(action.id)
        assert entry is not None
        self.assertEqual(entry.status, ActionStatus.FAILED)
        self.assertEqual(entry.error, "speaker unavailable")
        self.assertIsNone(entry.result)


class HistoryLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_and_action_histories_evict_oldest_entries(self) -> None:
        bit = Entity("bit")
        runtime = Runtime([bit], task_history_size=2, action_history_size=2)

        @bit.on("work")
        async def work(signal: Signal) -> Action:
            return await bit.action("record", value=signal.payload["value"])

        task_ids: list[str] = []
        action_ids: list[str] = []
        for value in range(3):
            await runtime.emit(Signal(type="work", payload={"value": value}))
            task_ids.append(runtime.tasks[-1].id)
            action_ids.append(runtime.actions[-1].id)

        self.assertEqual(len(runtime.task_history), 2)
        self.assertEqual(len(runtime.action_history), 2)
        self.assertEqual(len(runtime.tasks), 2)
        self.assertEqual(len(runtime.actions), 2)
        self.assertIsNone(runtime.get_task(task_ids[0]))
        self.assertIsNone(runtime.get_action(action_ids[0]))
        self.assertEqual(
            [entry.id for entry in runtime.latest_tasks()],
            list(reversed(task_ids[1:])),
        )
        self.assertEqual(
            [entry.id for entry in runtime.latest_actions()],
            list(reversed(action_ids[1:])),
        )

    async def test_history_sizes_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "task history"):
            Runtime(task_history_size=0)
        with self.assertRaisesRegex(ValueError, "action history"):
            Runtime(action_history_size=0)


if __name__ == "__main__":
    unittest.main()
