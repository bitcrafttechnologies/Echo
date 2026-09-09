from __future__ import annotations

import itertools
import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo.providers import (
    InferenceRequest,
    InferenceUnavailableError,
    MockProvider,
    ProviderHealthStatus,
    IntelligenceProvider,
    ProviderMode,
    ProviderRouter,
)


SLOTS = ("remote", "lan", "offline")


def _provider(slot: str, available: bool) -> MockProvider:
    return MockProvider(
        provider_id=slot,
        name=slot.title(),
        response=f"{slot} result",
        status="healthy" if available else "unavailable",
    )


class ProviderRouterFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_auto_fallback_permutation(self) -> None:
        for availability in itertools.product((False, True), repeat=3):
            with self.subTest(availability=availability):
                providers = {
                    slot: _provider(slot, available)
                    for slot, available in zip(SLOTS, availability, strict=True)
                }
                router = ProviderRouter(
                    remote=providers["remote"],
                    lan=providers["lan"],
                    offline=providers["offline"],
                )
                request = InferenceRequest(prompt="route this")
                expected = next(
                    (
                        slot
                        for slot, available in zip(
                            SLOTS, availability, strict=True
                        )
                        if available
                    ),
                    None,
                )

                if expected is None:
                    with self.assertRaises(InferenceUnavailableError) as raised:
                        await router.infer(request)
                    error = raised.exception
                    self.assertEqual(error.code, "inference_unavailable")
                    self.assertEqual(error.request_id, request.request_id)
                    self.assertEqual(len(error.attempts), 3)
                    self.assertEqual(error.to_dict()["mode"], "auto")
                else:
                    result = await router.infer(request)
                    self.assertEqual(result.output, f"{expected} result")
                    self.assertEqual(
                        router.current_selected_provider.provider_id, expected
                    )
                    self.assertIs(router.current_provider, providers[expected])

                expected_attempt_count = (
                    3 if expected is None else SLOTS.index(expected) + 1
                )
                status = router.status()
                self.assertEqual(len(status.attempts), expected_attempt_count)
                self.assertEqual(
                    len(status.failed_attempts),
                    expected_attempt_count if expected is None else expected_attempt_count - 1,
                )
                self.assertIsNotNone(status.latency_ms)

    async def test_explicit_modes_never_fallback_to_another_slot(self) -> None:
        for mode in (ProviderMode.REMOTE, ProviderMode.LAN, ProviderMode.OFFLINE):
            with self.subTest(mode=mode):
                providers = {slot: _provider(slot, True) for slot in SLOTS}
                selected = providers[mode.value]
                selected.set_status("unavailable")
                router = ProviderRouter(
                    remote=providers["remote"],
                    lan=providers["lan"],
                    offline=providers["offline"],
                    mode=mode,
                )

                with self.assertRaises(InferenceUnavailableError) as raised:
                    await router.infer(InferenceRequest(prompt="stay in mode"))

                self.assertEqual(len(raised.exception.attempts), 1)
                self.assertEqual(raised.exception.attempts[0].slot.value, mode.value)
                self.assertEqual(router.current_selected_provider, None)

    async def test_missing_allowed_provider_is_a_structured_failure(self) -> None:
        router = ProviderRouter(mode="remote")

        with self.assertRaises(InferenceUnavailableError) as raised:
            await router.infer(InferenceRequest(prompt="no provider"))

        error = raised.exception
        self.assertEqual(error.attempts, ())
        self.assertIn("no provider is configured", error.reason)
        self.assertEqual(router.status().error_reason, error.reason)
        self.assertEqual(router.error_reason, error.reason)

    async def test_route_state_resets_for_each_request(self) -> None:
        remote = _provider("remote", False)
        lan = _provider("lan", True)
        router = ProviderRouter(remote=remote, lan=lan)

        await router.infer(InferenceRequest(prompt="first"))
        self.assertEqual(len(router.failed_attempts), 1)
        remote.set_status("healthy")
        await router.infer(InferenceRequest(prompt="second"))

        status = router.status()
        self.assertEqual(status.current_selected_provider.provider_id, "remote")
        self.assertEqual(status.failed_attempts, ())
        self.assertEqual(len(status.attempts), 1)

    async def test_health_inspects_only_providers_allowed_by_mode(self) -> None:
        router = ProviderRouter(
            remote=_provider("remote", False),
            lan=_provider("lan", True),
            offline=_provider("offline", True),
            mode="lan",
        )

        health = await router.health()

        self.assertEqual(health.status, ProviderHealthStatus.HEALTHY)
        self.assertEqual(health.provider.provider_id, "provider-router")
        self.assertEqual(len(health.details["providers"]), 1)
        self.assertEqual(health.details["providers"][0]["slot"], "lan")
        self.assertGreaterEqual(health.timing.duration_ms, 0)

    async def test_all_failed_attempts_include_latency_and_reason(self) -> None:
        router = ProviderRouter(
            remote=_provider("remote", False),
            lan=_provider("lan", False),
            offline=_provider("offline", False),
        )

        with self.assertRaises(InferenceUnavailableError):
            await router.infer(InferenceRequest(prompt="fail"))

        status = router.status()
        self.assertEqual(len(status.failed_attempts), 3)
        self.assertTrue(all(item.timing.duration_ms >= 0 for item in status.attempts))
        self.assertTrue(all(item.error_reason for item in status.failed_attempts))
        self.assertIn("remote:", status.error_reason)
        self.assertEqual(status.to_dict()["current_selected_provider"], None)

    def test_positional_providers_use_default_preference_slots(self) -> None:
        router = ProviderRouter(
            [_provider("remote", True), _provider("lan", True)]
        )

        configured = router.status().to_dict()["configured_providers"]

        self.assertEqual(list(configured), ["remote", "lan"])
        self.assertIsInstance(router, IntelligenceProvider)

    async def test_inference_history_is_bounded_and_attributes_each_request(self) -> None:
        router = ProviderRouter(remote=_provider("remote", True), history_limit=2)
        requests = [InferenceRequest(prompt=f"request {index}") for index in range(3)]

        for request in requests:
            await router.infer(request)

        history = router.recent_inferences()
        self.assertEqual(
            [record.request_id for record in history],
            [requests[2].request_id, requests[1].request_id],
        )
        self.assertTrue(
            all(record.served_by.provider_id == "remote" for record in history)
        )


if __name__ == "__main__":
    unittest.main()
