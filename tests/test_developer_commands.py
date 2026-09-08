from __future__ import annotations

import asyncio
import builtins
import os
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    ActionListCommand,
    CommandExecutionError,
    CommandParseError,
    CommandValidationError,
    DeveloperCommandDispatcher,
    DeveloperCommandError,
    Entity,
    EntityInspectCommand,
    LogsCommand,
    Runtime,
    RuntimeService,
    RuntimeStatusCommand,
    Signal,
    SignalInjectCommand,
    SignalInspectCommand,
    SignalListCommand,
    StateGetCommand,
    StateSetCommand,
    TaskCancelCommand,
    TaskInspectCommand,
    TaskListCommand,
    UnknownCommandError,
    parse_developer_command,
)


class DeveloperCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bit = Entity("bit", state={"mode": "idle", "battery": 1.0})
        self.runtime = Runtime([self.bit])
        self.service = RuntimeService(
            self.runtime,
            allowed_state_keys={"bit": {"mode", "battery"}},
        )
        self.commands = DeveloperCommandDispatcher(self.service)

        @self.bit.on("observe")
        async def observe(signal: Signal):
            self.bit.state["mode"] = "observed"
            return await self.bit.action(
                "remember", value=signal.payload.get("value")
            )

    async def test_all_structured_commands_resolve_through_service(self) -> None:
        status = await self.commands.execute(RuntimeStatusCommand())
        self.assertEqual(status.to_dict()["command"], "runtime.status")
        self.assertEqual(status.data["status"], "idle")

        entity = await self.commands.execute(EntityInspectCommand(entity_id="bit"))
        self.assertEqual(entity.data["id"], "bit")

        injected = await self.commands.execute(
            SignalInjectCommand(
                signal_type="observe",
                source="test",
                payload={"value": 7},
                priority="high",
            )
        )
        signal_id = injected.data["id"]
        self.assertEqual(injected.data["routing_result"]["status"], "completed")

        signals = await self.commands.execute(SignalListCommand())
        self.assertEqual(signals.data[0]["id"], signal_id)
        inspected_signal = await self.commands.execute(
            SignalInspectCommand(signal_id=signal_id)
        )
        self.assertEqual(inspected_signal.data["payload"], {"value": 7})

        tasks = await self.commands.execute(TaskListCommand())
        task_id = tasks.data[0]["id"]
        inspected_task = await self.commands.execute(
            TaskInspectCommand(task_id=task_id)
        )
        self.assertEqual(inspected_task.data["status"], "completed")

        actions = await self.commands.execute(ActionListCommand())
        self.assertEqual(actions.data[0]["type"], "remember")
        self.assertEqual(actions.data[0]["task_id"], task_id)

        state = await self.commands.execute(StateGetCommand(entity_id="bit"))
        self.assertEqual(state.data["values"]["mode"], "observed")
        updated = await self.commands.execute(
            StateSetCommand(entity_id="bit", values={"mode": "ready"})
        )
        self.assertEqual(updated.data["values"]["mode"], "ready")
        self.assertEqual(self.bit.state["mode"], "ready")

        logs = await self.commands.execute(LogsCommand())
        self.assertTrue(logs.data["events"])

    async def test_task_cancel_command_cancels_live_runtime_task(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        @self.bit.on("wait")
        async def wait(signal: Signal) -> None:
            started.set()
            await release.wait()

        injection = asyncio.create_task(
            self.commands.execute(SignalInjectCommand(signal_type="wait"))
        )
        await started.wait()
        tasks = await self.commands.execute(TaskListCommand())

        cancelled = await self.commands.execute(
            TaskCancelCommand(task_id=tasks.data[0]["id"])
        )

        self.assertEqual(cancelled.data["status"], "cancelled")
        with self.assertRaises(asyncio.CancelledError):
            await injection

    async def test_minimal_text_grammar_produces_structured_commands(self) -> None:
        cases = {
            "runtime status": RuntimeStatusCommand,
            "entity inspect bit": EntityInspectCommand,
            "signal list": SignalListCommand,
            "signal inspect signal-1": SignalInspectCommand,
            "signal inject observe '{\"value\": 3}'": SignalInjectCommand,
            "task list": TaskListCommand,
            "task inspect task-1": TaskInspectCommand,
            "task cancel task-1": TaskCancelCommand,
            "action list": ActionListCommand,
            "state get bit": StateGetCommand,
            "state set bit '{\"mode\": \"ready\"}'": StateSetCommand,
            "logs": LogsCommand,
        }
        for text, expected_type in cases.items():
            with self.subTest(text=text):
                self.assertIsInstance(parse_developer_command(text), expected_type)

        result = await self.commands.execute_text(
            "signal inject observe '{\"value\": 9}'"
        )
        self.assertEqual(result.data["payload"], {"value": 9})

    async def test_malformed_commands_fail_with_structured_errors(self) -> None:
        malformed = (
            "",
            "runtime",
            "runtime status extra",
            "entity inspect",
            "signal inject",
            "signal inject observe not-json",
            "signal inject observe '[]'",
            "state set bit '{}'",
            "state set bit not-json",
            "unknown operation",
        )
        for text in malformed:
            with self.subTest(text=text):
                with self.assertRaises(
                    (CommandParseError, CommandValidationError, UnknownCommandError)
                ) as raised:
                    parse_developer_command(text)
                self.assertIn("code", raised.exception.to_dict())

        with self.assertRaises(CommandExecutionError) as missing:
            await self.commands.execute(EntityInspectCommand(entity_id="missing"))
        self.assertEqual(
            missing.exception.details["service_error"]["code"],
            "not_found",
        )

    async def test_text_commands_never_execute_python(self) -> None:
        attempts = (
            "python -c 'raise SystemExit'",
            "eval __import__('os').system('false')",
            "runtime status; __import__('os').system('false')",
            "signal inject observe '{\"value\": __import__(\"os\")}'",
        )
        with (
            patch.object(builtins, "eval") as eval_mock,
            patch.object(builtins, "exec") as exec_mock,
        ):
            for text in attempts:
                with self.subTest(text=text):
                    with self.assertRaises(DeveloperCommandError):
                        await self.commands.execute_text(text)

        eval_mock.assert_not_called()
        exec_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
