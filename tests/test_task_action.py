from __future__ import annotations

import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import Action, InvalidTaskTransition, Task, TaskStatus


class TaskTests(unittest.TestCase):
    def test_task_completes_with_timestamps_and_result(self) -> None:
        task = Task(name="answer-user", owner="bit")

        task.start()
        task.complete({"answer": "hello"})

        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertIsNotNone(task.started_at)
        self.assertIsNotNone(task.completed_at)
        self.assertEqual(task.result, {"answer": "hello"})

    def test_task_can_pause_block_resume_and_cancel(self) -> None:
        paused = Task(name="bring-water", owner="bit")
        paused.start()
        paused.pause()
        paused.resume()
        paused.cancel()

        blocked = Task(name="monitor-door", owner="bit")
        blocked.start()
        blocked.block()
        blocked.resume()

        self.assertEqual(paused.status, TaskStatus.CANCELLED)
        self.assertEqual(blocked.status, TaskStatus.RUNNING)

    def test_invalid_terminal_transition_is_rejected(self) -> None:
        task = Task(name="done", owner="bit")
        task.start()
        task.complete()

        with self.assertRaises(InvalidTaskTransition):
            task.cancel()

    def test_parent_child_relationship_uses_stable_ids(self) -> None:
        parent = Task(name="bring-water", owner="bit")
        child = Task(name="avoid-obstacle", owner="bit")

        parent.add_child(child)

        self.assertEqual(child.parent, parent.id)
        self.assertEqual(parent.children, [child.id])


class ActionTests(unittest.TestCase):
    def test_action_is_structured_and_serializable(self) -> None:
        action = Action(
            type="speak",
            parameters={"text": "Hello"},
            entity_id="bit",
            task_id="task-1",
        )

        data = action.to_dict()

        self.assertEqual(data["type"], "speak")
        self.assertEqual(data["parameters"], {"text": "Hello"})
        self.assertEqual(data["entity_id"], "bit")
        self.assertIsInstance(data["created_at"], str)


if __name__ == "__main__":
    unittest.main()

