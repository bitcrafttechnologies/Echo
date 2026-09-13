from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
import subprocess


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    Entity,
    RelationshipState,
    RelationshipStore,
    Runtime,
    RuntimeConfigurationManager,
    RuntimeService,
    Signal,
    parse_config,
)
from echo.cli import _one_shot
from echo.tui import EchoTui, HttpEchoClient, LocalEchoClient, TmuxSessionManager, default_registry, render_snapshot


class TuiCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bit = Entity(
            "bit",
            state={"mode": "attentive"},
            relationships=RelationshipStore(
                {"operator": RelationshipState("operator", familiarity=0.5, trust=0.75)}
            ),
        )
        self.runtime = Runtime([self.bit])
        config = parse_config({})
        router = config.create_provider_router()
        self.service = RuntimeService(
            self.runtime,
            allowed_state_keys={"bit": {"mode"}},
            provider_router=router,
            configuration_manager=RuntimeConfigurationManager(
                config, self.runtime, provider_router=router
            ),
        )
        self.client = LocalEchoClient(self.service)

        @self.bit.on("UserMessage")
        async def chat(signal: Signal):
            return await self.bit.action("speak", text=f"heard: {signal.payload['text']}")

        @self.bit.on("observe")
        async def observe(signal: Signal):
            return await self.bit.action("remember", value=signal.payload.get("value"))

        await self.client.execute("signal inject observe '{\"value\":7}'")

    async def test_every_web_console_surface_has_a_semantic_tui_snapshot(self) -> None:
        expected = {
            "overview": "SYSTEM OVERVIEW",
            "chat": "CHAT · USERMESSAGE SIGNAL PATH",
            "signals": "SIGNAL INSPECTOR",
            "tasks": "TASK INSPECTOR",
            "entity": "ENTITY INSPECTOR",
            "providers": "PROVIDER ROUTING",
            "medulla": "MEDULLA NODES",
            "configuration": "EFFECTIVE CONFIGURATION",
            "logs": "STRUCTURED LOGS",
        }
        for surface, label in expected.items():
            with self.subTest(surface=surface):
                data = await self.client.snapshot(surface, entity_id="bit")
                rendered = render_snapshot(surface, data, width=160, color=False)
                self.assertIn("ECHO ◉", rendered)
                self.assertIn(label, rendered)
                self.assertIn("[:] command", rendered)

    async def test_entity_surface_includes_current_web_character_sections(self) -> None:
        rendered = render_snapshot(
            "entity",
            await self.client.snapshot("entity", entity_id="bit"),
            width=180,
        )
        for label in (
            "State",
            "Handlers",
            "Identity",
            "Traits",
            "Control state",
            "Drives",
            "Self model / embodiment",
            "Attention",
            "Relationships",
            "operator",
        ):
            self.assertIn(label, rendered)

    async def test_chat_uses_the_normal_signal_task_action_path(self) -> None:
        emitted = await self.client.chat("Morning.", entity_id="bit")
        self.assertEqual(emitted["type"], "UserMessage")
        self.assertEqual(emitted["source"], "console")
        self.assertEqual(emitted["routing_result"]["status"], "completed")

        rendered = render_snapshot("chat", await self.client.snapshot("chat"), width=160)
        self.assertIn("Morning.", rendered)
        self.assertIn("heard: Morning.", rendered)
        self.assertIn("Echo · speak", rendered)

    async def test_command_area_uses_the_existing_dispatcher(self) -> None:
        result = await self.client.execute("entity inspect bit")
        self.assertEqual(result["command"], "entity.inspect")
        self.assertEqual(result["data"]["id"], "bit")

        updated = await self.client.execute("state set bit '{\"mode\":\"ready\"}'")
        self.assertEqual(updated["data"]["values"]["mode"], "ready")

        configuration = await self.client.execute("config inspect")
        self.assertTrue(configuration["data"]["fields"])
        changed = await self.client.execute("config set history.signals 25")
        self.assertTrue(changed["data"]["applied"])
        self.assertEqual(self.runtime.signal_history.max_size, 25)

    async def test_signal_and_log_filters_match_the_web_console_controls(self) -> None:
        await self.client.execute("signal inject other")
        signals = await self.client.snapshot(
            "signals", filters={"type": "observe", "source": "developer"}
        )
        self.assertEqual([item["type"] for item in signals["signals"]], ["observe"])
        self.assertIn(
            "Filters: type=observe source=developer",
            render_snapshot("signals", signals, width=160),
        )

        logs = await self.client.snapshot("logs", filters={"severity": "info"})
        self.assertTrue(logs["logs"])
        self.assertTrue(all(item["severity"] == "info" for item in logs["logs"]))
        self.assertEqual(
            EchoTui._parse_filters("severity=error event_type=signal.routed ignored"),
            {"severity": "error", "event_type": "signal.routed"},
        )

    def test_surface_registry_is_ordered_and_transport_safe(self) -> None:
        registry = default_registry()
        self.assertEqual(
            [surface.key for surface in registry.list()],
            ["overview", "chat", "signals", "tasks", "entity", "providers", "medulla", "configuration", "logs"],
        )
        self.assertEqual(registry.catalog()[2]["capabilities"], ["list", "inspect", "emit"])

        with self.assertRaises(ValueError):
            registry.register(registry.get("overview"))

    def test_tmux_workspace_contains_views_config_shell_and_shortcuts(self) -> None:
        manager = TmuxSessionManager(project_root=Path("/echo"))
        commands = manager.commands("/usr/bin/tmux")
        text = "\n".join(" ".join(command) for command in commands)
        for window in ("dashboard", "chat", "signals", "tasks", "entity", "providers", "medulla", "configuration", "logs", "files", "shell"):
            self.assertIn(window, text)
        for key in range(1, 10):
            self.assertIn(f"M-{key}", text)
        self.assertIn("--no-tmux", text)
        self.assertIn("/echo/entities", text)

    def test_tmux_launcher_creates_then_attaches_to_named_session(self) -> None:
        calls: list[list[str]] = []

        def runner(command, **kwargs):
            del kwargs
            calls.append(command)
            return subprocess.CompletedProcess(command, 1 if command[1] == "has-session" else 0)

        manager = TmuxSessionManager(runner=runner)
        with (
            patch("echo.tui.tmux.shutil.which", return_value="/usr/bin/tmux"),
            patch.dict(os.environ, {"TMUX": ""}),
        ):
            manager.launch()

        self.assertEqual(calls[0], ["/usr/bin/tmux", "has-session", "-t", "echo-core"])
        self.assertIn(["/usr/bin/tmux", "attach-session", "-t", "echo-core"], calls)
        self.assertTrue(any(command[1:5] == ["new-session", "-d", "-s", "echo-core"] for command in calls))

    async def test_http_client_translates_commands_to_the_public_adapter(self) -> None:
        client = HttpEchoClient("http://echo.test")
        client._request = AsyncMock(return_value={"id": "signal-1"})

        result = await client.execute("signal inject observe '{\"value\":7}'")

        self.assertEqual(result["command"], "signal.inject")
        client._request.assert_awaited_once_with(
            "/signals",
            method="POST",
            body={
                "type": "observe",
                "source": "developer",
                "payload": {"value": 7},
                "metadata": {},
                "priority": 20,
            },
        )

        client._request.reset_mock(return_value=True)
        client._request.return_value = {"status": "no_change", "applied": True}
        result = await client.execute("config reload")

        self.assertEqual(result["command"], "config.reload")
        client._request.assert_awaited_once_with(
            "/configuration/reload",
            method="POST",
            body=None,
        )

        client._request.reset_mock(return_value=True)
        client._request.return_value = {"status": "applied", "applied": True}
        result = await client.execute("config set history.signals 25")

        self.assertEqual(result["command"], "config.set")
        client._request.assert_awaited_once_with(
            "/configuration",
            method="PATCH",
            body={"values": {"history.signals": 25}},
        )

    async def test_cli_preserves_json_as_one_shared_command_argument(self) -> None:
        fake = AsyncMock()
        fake.execute.return_value = {"command": "signal.inject", "data": {}}
        with (
            patch("echo.cli.HttpEchoClient", return_value=fake),
            patch("builtins.print"),
        ):
            result = await _one_shot(
                ["signal", "inject", "observe", '{"message": "hello world"}'],
                "http://echo.test",
            )
        self.assertEqual(result, 0)
        fake.execute.assert_awaited_once_with(
            "signal inject observe '{\"message\": \"hello world\"}'"
        )


if __name__ == "__main__":
    unittest.main()
