from __future__ import annotations

import asyncio
from contextlib import chdir
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echo import Entity, MockProvider, ProviderRouter, Runtime, Signal, load_entity_seed
from echo.host import (
    build_host,
    register_user_message_handler,
    selected_entity_seed_path,
)


class HostChatHandlerTests(unittest.TestCase):
    def test_installed_host_resolves_launcher_entity_root(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            entity_root = Path(temporary_directory) / "entities"
            bit_directory = entity_root / "bit"
            bit_directory.mkdir(parents=True)
            unrelated_directory = Path(temporary_directory) / "elsewhere"
            unrelated_directory.mkdir()

            with (
                patch.dict(os.environ, {"ECHO_ENTITY_ROOT": str(entity_root)}),
                chdir(unrelated_directory),
            ):
                selected = selected_entity_seed_path("bit")

            self.assertEqual(selected, bit_directory.resolve())

    def test_configured_host_starts_with_bit_seed(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "echo.toml"
            config_path.write_text(
                '[providers]\nmode = "auto"\n\n[api]\nenabled = true\n',
                encoding="utf-8",
            )

            _app, _config, runtime, _service = build_host(config_path)
            try:
                entity = runtime.get_entity("bit")
                self.assertIsNotNone(entity)
                self.assertEqual(entity.identity.name, "Bit")
                self.assertEqual(entity.identity.entity_type, "embodied_companion")
                self.assertEqual(entity.traits.values["curiosity"], 0.82)
            finally:
                runtime.stop()

    def test_bit_seed_governs_identity_and_provider_instructions(self) -> None:
        async def exercise() -> None:
            seed = load_entity_seed(
                Path(__file__).resolve().parents[1] / "entities" / "bit"
            )
            provider = MockProvider(response="I am Bit.")
            router = ProviderRouter(remote=provider)
            entity = seed.create_entity()
            register_user_message_handler(
                entity,
                router,
                character_guidance=seed.character_guidance,
            )
            runtime = Runtime([entity])

            await runtime.emit(
                Signal(type="UserMessage", payload={"text": "Who are you?"})
            )

            request = provider.requests[0]
            self.assertEqual(request.context["identity"]["name"], "Bit")
            self.assertEqual(
                request.context["identity"]["entity_type"],
                "embodied_companion",
            )
            self.assertIn("speak in the first person as Bit", request.instructions)
            self.assertIn("Never\nidentify Bit as the inference model", request.instructions)

        asyncio.run(exercise())

    def test_user_message_routes_through_provider_and_records_response_action(self) -> None:
        async def exercise() -> None:
            provider = MockProvider(
                provider_id="openrouter",
                model="remote-model",
                response="Hello from Echo.",
            )
            router = ProviderRouter(remote=provider)
            entity = Entity("bit")
            register_user_message_handler(entity, router)
            runtime = Runtime([entity])
            signal = Signal(
                type="UserMessage",
                source="console",
                payload={"text": "Hello"},
            )

            await runtime.emit(signal)

            routed = runtime.get_signal(signal.id)
            self.assertIsNotNone(routed)
            self.assertEqual(routed.routing_result.status, "completed")
            self.assertEqual(routed.routing_result.handler_count, 1)
            self.assertEqual(len(provider.requests), 1)
            self.assertEqual(provider.requests[0].prompt, "Hello")
            self.assertEqual(provider.requests[0].metadata["signal_id"], signal.id)
            context = provider.requests[0].context
            self.assertEqual(context["entity_id"], "bit")
            self.assertEqual(context["situation"], "Hello")
            self.assertIn("identity", context)
            self.assertIn("memories", context)

            actions = runtime.filter_actions(signal_id=signal.id)
            self.assertEqual(len(actions), 1)
            response = actions[0]
            self.assertEqual(response.type, "EchoResponse")
            self.assertEqual(response.parameters["text"], "Hello from Echo.")
            self.assertEqual(
                response.parameters["provider"]["provider_id"], "openrouter"
            )
            self.assertEqual(response.entity_id, "bit")
            self.assertIn(response.task_id, routed.routing_result.task_ids)

        asyncio.run(exercise())

    def test_user_message_requires_non_empty_text(self) -> None:
        async def exercise() -> None:
            entity = Entity("bit")
            register_user_message_handler(
                entity,
                ProviderRouter(remote=MockProvider()),
            )
            runtime = Runtime([entity])
            signal = Signal(type="UserMessage", payload={"text": "  "})

            with self.assertRaisesRegex(ValueError, "payload.text"):
                await runtime.emit(signal)

            routed = runtime.get_signal(signal.id)
            self.assertIsNotNone(routed)
            self.assertEqual(routed.routing_result.status, "failed")
            self.assertEqual(runtime.filter_actions(signal_id=signal.id), ())

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
