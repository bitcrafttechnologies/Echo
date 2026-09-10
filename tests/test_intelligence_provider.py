from __future__ import annotations

import os
import sys
import unittest
from dataclasses import FrozenInstanceError


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import Entity, Runtime
from echo.providers import (
    ClassificationProvider,
    EmbeddingProvider,
    InferenceRequest,
    IntelligenceProvider,
    MockProvider,
    ProviderCapability,
    ProviderHealthStatus,
    ProviderUnavailableError,
)


class IntelligenceProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_provider_implements_async_contract(self) -> None:
        provider = MockProvider(response="hello from mock")
        request = InferenceRequest(
            prompt="Say hello",
            parameters={"temperature": 0},
            metadata={"entity_id": "bit"},
        )

        result = await provider.infer(request)

        self.assertIsInstance(provider, IntelligenceProvider)
        self.assertEqual(result.request_id, request.request_id)
        self.assertEqual(result.output, "hello from mock")
        self.assertEqual(result.provider.provider_id, "mock")
        self.assertGreaterEqual(result.timing.duration_ms, 0)
        self.assertEqual(provider.requests, (request,))
        self.assertEqual(result.to_dict()["provider"]["model"], "mock-model")

    async def test_health_result_contains_status_provider_and_timing(self) -> None:
        provider = MockProvider(status=ProviderHealthStatus.DEGRADED)

        health = await provider.health()

        self.assertEqual(health.status, ProviderHealthStatus.DEGRADED)
        self.assertEqual(health.provider, provider.metadata)
        self.assertGreaterEqual(health.timing.duration_ms, 0)
        self.assertEqual(health.to_dict()["status"], "degraded")

    async def test_unavailable_mock_fails_cleanly(self) -> None:
        provider = MockProvider(status="unavailable")

        with self.assertRaises(ProviderUnavailableError):
            await provider.infer(InferenceRequest(prompt="hello"))

    async def test_queued_mock_responses_are_deterministic(self) -> None:
        provider = MockProvider(response="fallback", responses=("one", "two"))

        outputs = [
            (await provider.infer(InferenceRequest(prompt=str(index)))).output
            for index in range(3)
        ]

        self.assertEqual(outputs, ["one", "two", "fallback"])

    def test_request_and_provider_metadata_are_immutable(self) -> None:
        parameters = {"nested": {"temperature": 0}}
        request = InferenceRequest(prompt="hello", parameters=parameters)
        provider = MockProvider()
        parameters["nested"]["temperature"] = 1

        self.assertEqual(request.parameters["nested"]["temperature"], 0)
        with self.assertRaises(TypeError):
            request.parameters["temperature"] = 1  # type: ignore[index]
        with self.assertRaises(FrozenInstanceError):
            provider.metadata.name = "changed"  # type: ignore[misc]

    def test_future_capabilities_are_optional_protocols(self) -> None:
        provider = MockProvider()

        self.assertTrue(provider.metadata.supports(ProviderCapability.INFER))
        self.assertFalse(provider.metadata.supports(ProviderCapability.EMBED))
        self.assertNotIsInstance(provider, EmbeddingProvider)
        self.assertNotIsInstance(provider, ClassificationProvider)

    async def test_echo_core_runs_without_a_provider(self) -> None:
        runtime = Runtime([Entity("bit")])

        snapshot = runtime.inspect()
        runtime.stop()

        self.assertEqual(snapshot["runtime_status"], "idle")
        self.assertEqual(snapshot["entity_ids"], ["bit"])


if __name__ == "__main__":
    unittest.main()
