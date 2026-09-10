from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo.providers import (
    InferenceRequest,
    LlamaCppServerBackend,
    MockProvider,
    OfflineBackendHealth,
    OfflineBackendResult,
    OfflineConfigurationError,
    OfflineInferenceConfig,
    OfflineInferenceProvider,
    OfflineInitializationError,
    OfflineModelState,
    OfflineUnloadError,
    ProviderHealthStatus,
    ProviderRouter,
)


class FakeOfflineBackend:
    def __init__(
        self,
        *,
        load_error: Exception | None = None,
        infer_error: Exception | None = None,
        unload_error: Exception | None = None,
        load_delay: float = 0,
    ) -> None:
        self._loaded = False
        self.load_error = load_error
        self.infer_error = infer_error
        self.unload_error = unload_error
        self.load_delay = load_delay
        self.load_calls = 0
        self.infer_calls = 0
        self.health_calls = 0
        self.unload_calls = 0

    @property
    def loaded(self) -> bool:
        return self._loaded

    async def load(self, config: OfflineInferenceConfig) -> None:
        self.load_calls += 1
        if self.load_delay:
            await asyncio.sleep(self.load_delay)
        if self.load_error is not None:
            raise self.load_error
        self._loaded = True

    async def infer(self, request: InferenceRequest) -> OfflineBackendResult:
        self.infer_calls += 1
        if self.infer_error is not None:
            raise self.infer_error
        return OfflineBackendResult(
            output=f"offline: {request.prompt}",
            model="served-local-model",
            metadata={"tokens": 4},
        )

    async def health(self) -> OfflineBackendHealth:
        self.health_calls += 1
        return OfflineBackendHealth(
            status=(
                ProviderHealthStatus.HEALTHY
                if self.loaded
                else ProviderHealthStatus.UNAVAILABLE
            ),
            message="fake backend health",
            details={"loaded": self.loaded},
        )

    async def unload(self) -> None:
        self.unload_calls += 1
        if self.unload_error is not None:
            raise self.unload_error
        self._loaded = False


class OfflineInferenceProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.model_path = Path(self.temporary_directory.name) / "model.gguf"
        self.model_path.touch()

    def config(self, **overrides: object) -> OfflineInferenceConfig:
        values: dict[str, object] = {
            "model_path": self.model_path,
            "model": "configured-local-model",
        }
        values.update(overrides)
        return OfflineInferenceConfig(**values)

    async def test_construction_and_health_do_not_load_model(self) -> None:
        backend = FakeOfflineBackend()
        provider = OfflineInferenceProvider(self.config(), backend=backend)

        initial = provider.status()
        health = await provider.health()

        self.assertEqual(initial.state, OfflineModelState.UNLOADED)
        self.assertFalse(initial.loaded)
        self.assertEqual(health.status, ProviderHealthStatus.DEGRADED)
        self.assertEqual(health.details["state"], "unloaded")
        self.assertEqual(backend.load_calls, 0)
        self.assertEqual(backend.health_calls, 0)

    async def test_remote_success_never_loads_offline_fallback(self) -> None:
        backend = FakeOfflineBackend()
        offline = OfflineInferenceProvider(self.config(), backend=backend)
        router = ProviderRouter(
            remote=MockProvider(provider_id="openrouter", response="remote"),
            offline=offline,
        )

        result = await router.infer(InferenceRequest(prompt="stay remote"))

        self.assertEqual(result.output, "remote")
        self.assertEqual(backend.load_calls, 0)
        self.assertEqual(offline.status().state, OfflineModelState.UNLOADED)

    async def test_offline_fallback_lazily_loads_once_and_normalizes(self) -> None:
        backend = FakeOfflineBackend()
        offline = OfflineInferenceProvider(self.config(), backend=backend)
        router = ProviderRouter(
            remote=MockProvider(provider_id="openrouter", status="unavailable"),
            lan=MockProvider(provider_id="lan", status="unavailable"),
            offline=offline,
        )

        with self.assertLogs("echo.providers.offline", level="INFO"):
            first = await router.infer(InferenceRequest(prompt="first"))
            second = await router.infer(InferenceRequest(prompt="second"))

        self.assertEqual(first.output, "offline: first")
        self.assertEqual(second.output, "offline: second")
        self.assertEqual(first.provider.provider_id, "offline")
        self.assertEqual(first.provider.model, "served-local-model")
        self.assertEqual(first.metadata["tokens"], 4)
        self.assertEqual(backend.load_calls, 1)
        self.assertEqual(backend.infer_calls, 2)
        self.assertTrue(offline.loaded)
        self.assertEqual(offline.status().state, OfflineModelState.LOADED)

    async def test_concurrent_first_requests_share_one_lazy_load(self) -> None:
        backend = FakeOfflineBackend(load_delay=0.01)
        provider = OfflineInferenceProvider(self.config(), backend=backend)

        await asyncio.gather(
            provider.infer(InferenceRequest(prompt="one")),
            provider.infer(InferenceRequest(prompt="two")),
        )

        self.assertEqual(backend.load_calls, 1)
        self.assertEqual(backend.infer_calls, 2)

    async def test_explicit_unload_releases_backend_and_updates_state(self) -> None:
        backend = FakeOfflineBackend()
        provider = OfflineInferenceProvider(self.config(), backend=backend)
        await provider.infer(InferenceRequest(prompt="load"))

        status = await provider.unload()

        self.assertEqual(backend.unload_calls, 1)
        self.assertFalse(status.loaded)
        self.assertEqual(status.state, OfflineModelState.UNLOADED)

    async def test_unload_failure_is_structured_and_visible(self) -> None:
        backend = FakeOfflineBackend(unload_error=RuntimeError("cannot stop"))
        provider = OfflineInferenceProvider(self.config(), backend=backend)
        await provider.infer(InferenceRequest(prompt="load"))

        with self.assertRaises(OfflineUnloadError):
            await provider.unload()

        self.assertEqual(provider.status().state, OfflineModelState.ERROR)
        self.assertIn("cannot stop", provider.status().last_error)

    async def test_missing_path_is_optional_and_fails_without_loading(self) -> None:
        backend = FakeOfflineBackend()
        provider = OfflineInferenceProvider(
            OfflineInferenceConfig(), backend=backend
        )

        health = await provider.health()
        with self.assertRaises(OfflineConfigurationError):
            await provider.infer(InferenceRequest(prompt="unconfigured"))

        self.assertEqual(health.status, ProviderHealthStatus.UNAVAILABLE)
        self.assertEqual(provider.status().state, OfflineModelState.UNCONFIGURED)
        self.assertEqual(backend.load_calls, 0)

    async def test_backend_initialization_failure_is_structured(self) -> None:
        backend = FakeOfflineBackend(load_error=RuntimeError("load failed"))
        provider = OfflineInferenceProvider(self.config(), backend=backend)

        with self.assertRaises(OfflineInitializationError) as raised:
            await provider.infer(InferenceRequest(prompt="load"))

        self.assertIn("load failed", raised.exception.message)
        self.assertEqual(provider.status().state, OfflineModelState.ERROR)
        self.assertFalse(provider.loaded)

    async def test_real_adapter_reports_missing_llama_server_cleanly(self) -> None:
        provider = OfflineInferenceProvider(
            self.config(executable="echo-llama-server-does-not-exist"),
            backend=LlamaCppServerBackend(),
        )

        with self.assertRaises(OfflineInitializationError) as raised:
            await provider.infer(InferenceRequest(prompt="load"))

        self.assertIn("executable is unavailable", raised.exception.message)
        self.assertFalse(provider.loaded)

    def test_environment_requires_an_explicit_model_path(self) -> None:
        empty = OfflineInferenceConfig.from_env({})
        configured = OfflineInferenceConfig.from_env(
            {
                "OFFLINE_MODEL_PATH": str(self.model_path),
                "OFFLINE_MODEL": "env-model",
                "OFFLINE_REQUEST_TIMEOUT_SECONDS": "45",
            }
        )

        self.assertIsNone(empty.model_path)
        self.assertEqual(configured.model_path, self.model_path.resolve())
        self.assertEqual(configured.model, "env-model")
        self.assertEqual(configured.request_timeout_seconds, 45)


if __name__ == "__main__":
    unittest.main()
