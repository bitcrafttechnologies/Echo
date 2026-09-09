from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echo import Entity, MockProvider, ProviderRouter, Runtime, Signal
from echo.host import register_user_message_handler


class HostChatHandlerTests(unittest.TestCase):
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
