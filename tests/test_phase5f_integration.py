from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    CharacterMemory,
    Entity,
    EpisodicMemory,
    InferenceRequest,
    InferenceServiceUnavailableError,
    MemoryKind,
    MockProvider,
    PreferenceMemory,
    ProviderModeCommand,
    ProviderRouter,
    ProviderStatusCommand,
    RelationshipMemory,
    RelationshipState,
    RelationshipStore,
    Runtime,
    RuntimeService,
    SemanticMemory,
    WorkingMemory,
)
from echo.developer_commands import DeveloperCommandDispatcher


def _provider(slot: str) -> MockProvider:
    return MockProvider(
        provider_id=slot,
        name=slot.title(),
        model=f"{slot}-model",
        response=f"served by {slot}",
    )


class Phase5FFallbackIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.remote = _provider("openrouter")
        self.lan = _provider("lan")
        self.offline = _provider("offline")
        self.router = ProviderRouter(
            remote=self.remote,
            lan=self.lan,
            offline=self.offline,
        )
        memory = CharacterMemory(
            (
                WorkingMemory(content={"task": "test routing"}, source="test"),
                EpisodicMemory(content={"event": "met operator"}, source="test"),
                SemanticMemory(content={"fact": "Echo owns memory"}, source="test"),
                PreferenceMemory(content={"prefers": "concise status"}, source="test"),
                RelationshipMemory(
                    content={"subject_id": "operator", "event": "helped"},
                    source="test",
                ),
            )
        )
        self.bit = Entity(
            "bit",
            memory=memory,
            relationships=RelationshipStore(
                {"operator": RelationshipState("operator", trust=0.8)}
            ),
        )
        self.service = RuntimeService(
            Runtime([self.bit]), provider_router=self.router
        )

    async def test_complete_fallback_restore_and_provider_attribution(self) -> None:
        character_before = self.bit.inspect_character()
        requests = [InferenceRequest(prompt=f"request {index}") for index in range(4)]

        first = await self.service.infer(requests[0])
        self.assertEqual(first.provider.provider_id, "openrouter")

        self.remote.set_status("unavailable")
        second = await self.service.infer(requests[1])
        self.assertEqual(second.provider.provider_id, "lan")

        self.lan.set_status("unavailable")
        third = await self.service.infer(requests[2])
        self.assertEqual(third.provider.provider_id, "offline")

        self.remote.set_status("healthy")
        fourth = await self.service.infer(requests[3])
        self.assertEqual(fourth.provider.provider_id, "openrouter")

        inspection = await self.service.inspect_providers()
        history = list(reversed(inspection.recent_inferences))
        self.assertEqual(
            [item["request_id"] for item in history],
            [request.request_id for request in requests],
        )
        self.assertEqual(
            [item["served_by"]["provider_id"] for item in history],
            ["openrouter", "lan", "offline", "openrouter"],
        )
        self.assertEqual(len(inspection.recent_failures), 3)
        self.assertEqual(inspection.active_provider["provider_id"], "openrouter")
        self.assertEqual(inspection.model, "openrouter-model")
        self.assertIsNotNone(inspection.last_latency_ms)
        self.assertEqual(self.bit.inspect_character(), character_before)

    async def test_modes_are_switchable_and_router_remains_only_selector(self) -> None:
        commands = DeveloperCommandDispatcher(self.service)
        for mode in ("remote", "lan", "offline", "auto"):
            result = await commands.execute(ProviderModeCommand(mode=mode))
            self.assertEqual(result.data["mode"], mode)
        status = await commands.execute(ProviderStatusCommand())
        self.assertEqual(set(status.data["configured_providers"]), {"remote", "lan", "offline"})

    def test_all_five_memory_types_are_distinct_and_inspectable(self) -> None:
        memory = self.bit.inspect_character()["memory"]
        self.assertEqual(set(memory), {kind.value for kind in MemoryKind})
        self.assertTrue(all(len(memory[kind.value]) == 1 for kind in MemoryKind))
        self.assertEqual(
            {type(record) for record in self.bit.memories},
            {WorkingMemory, EpisodicMemory, SemanticMemory, PreferenceMemory, RelationshipMemory},
        )


class Phase5FOptionalProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_still_operates_without_providers(self) -> None:
        service = RuntimeService(Runtime([Entity("bit")]))
        self.assertEqual(service.get_runtime_status().status, "idle")
        inspection = await service.inspect_providers()
        self.assertEqual(inspection.configured_providers, {})
        with self.assertRaises(InferenceServiceUnavailableError) as raised:
            await service.infer(InferenceRequest(prompt="not configured"))
        self.assertEqual(raised.exception.code, "inference_unavailable")


if __name__ == "__main__":
    unittest.main()
