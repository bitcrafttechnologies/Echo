from __future__ import annotations

from collections.abc import Mapping
import os
import sys
import unittest
from typing import Any


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo.providers import (
    InferenceRequest,
    LanInferenceAPIError,
    LanInferenceConfig,
    LanInferenceConfigurationError,
    LanInferenceHttpResponse,
    LanInferenceNetworkError,
    LanInferenceProvider,
    LanInferenceRequestError,
    LanInferenceResponseError,
    MockProvider,
    ProviderHealthStatus,
    ProviderRouter,
)


class FakeLanTransport:
    def __init__(
        self,
        responses: list[LanInferenceHttpResponse] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> LanInferenceHttpResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "json_body": dict(json_body) if json_body is not None else None,
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise AssertionError("unexpected LAN transport request")
        return self.responses.pop(0)


def _config(
    *,
    api_key: str | None = None,
) -> LanInferenceConfig:
    return LanInferenceConfig(
        base_url="http://echo-lan.test:8080",
        model="qwen-test.gguf",
        timeout_seconds=7.5,
        api_key=api_key,
    )


def _completion() -> LanInferenceHttpResponse:
    return LanInferenceHttpResponse(
        status_code=200,
        payload={
            "id": "chatcmpl-lan-1",
            "model": "qwen-served.gguf",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "LAN output"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "total_tokens": 5,
            },
            "timings": {"predicted_ms": 12.5},
        },
    )


class LanInferenceProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_configuration_reads_supported_environment_aliases(self) -> None:
        config = LanInferenceConfig.from_env(
            {
                "LAN_INFERENCE_URL": "http://model-box.local:8080/",
                "LAN_INFERENCE_MODEL": "qwen-lan",
                "LAN_INFERENCE_TIMEOUT_SECONDS": "4.25",
                "LAN_INFERENCE_API_KEY": "lan-secret",
            }
        )

        self.assertEqual(config.base_url, "http://model-box.local:8080/v1")
        self.assertEqual(config.model, "qwen-lan")
        self.assertEqual(config.timeout_seconds, 4.25)
        self.assertNotIn("lan-secret", repr(config))

        alias = LanInferenceConfig.from_env(
            {
                "LLAMA_CPP_BASE_URL": "http://192.0.2.20:8080/v1",
                "LLAMA_CPP_MODEL": "alias-model",
            }
        )
        self.assertEqual(alias.base_url, "http://192.0.2.20:8080/v1")
        self.assertEqual(alias.model, "alias-model")

    def test_full_completion_url_is_normalized_to_api_root(self) -> None:
        config = LanInferenceConfig(
            base_url="http://model-box:8080/v1/chat/completions",
            model="model",
        )

        self.assertEqual(config.base_url, "http://model-box:8080/v1")

    async def test_infer_uses_openai_compatible_request_and_normalizes_result(
        self,
    ) -> None:
        transport = FakeLanTransport([_completion()])
        provider = LanInferenceProvider(
            _config(api_key="lan-secret"), transport=transport
        )
        request = InferenceRequest(
            prompt="Answer over the LAN",
            parameters={"temperature": 0.1, "max_tokens": 40},
        )

        with self.assertLogs("echo.providers.lan", level="INFO") as captured:
            result = await provider.infer(request)

        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(
            call["url"], "http://echo-lan.test:8080/v1/chat/completions"
        )
        self.assertEqual(call["timeout_seconds"], 7.5)
        self.assertEqual(call["headers"]["Authorization"], "Bearer lan-secret")
        self.assertEqual(call["json_body"]["model"], "qwen-test.gguf")
        self.assertEqual(
            call["json_body"]["messages"],
            [{"role": "user", "content": "Answer over the LAN"}],
        )
        self.assertFalse(call["json_body"]["stream"])
        self.assertEqual(result.output, "LAN output")
        self.assertEqual(result.provider.provider_id, "lan")
        self.assertEqual(result.provider.model, "qwen-served.gguf")
        self.assertEqual(result.metadata["usage"]["total_tokens"], 5)
        self.assertEqual(result.metadata["server_timings"]["predicted_ms"], 12.5)
        self.assertGreaterEqual(result.timing.duration_ms, 0)
        self.assertNotIn("lan-secret", "\n".join(captured.output))

    async def test_health_uses_llama_cpp_v1_health_endpoint(self) -> None:
        transport = FakeLanTransport(
            [LanInferenceHttpResponse(status_code=200, payload={"status": "ok"})]
        )
        provider = LanInferenceProvider(_config(), transport=transport)

        health = await provider.health()

        self.assertEqual(health.status, ProviderHealthStatus.HEALTHY)
        self.assertTrue(health.details["ready"])
        self.assertEqual(transport.calls[0]["method"], "GET")
        self.assertEqual(
            transport.calls[0]["url"], "http://echo-lan.test:8080/v1/health"
        )
        self.assertEqual(transport.calls[0]["timeout_seconds"], 7.5)

    async def test_loading_and_malformed_health_are_degraded(self) -> None:
        for response in (
            LanInferenceHttpResponse(
                status_code=503,
                payload={
                    "error": {
                        "code": 503,
                        "message": "Loading model",
                    }
                },
            ),
            LanInferenceHttpResponse(status_code=200, payload={"status": "busy"}),
        ):
            with self.subTest(response=response):
                provider = LanInferenceProvider(
                    _config(), transport=FakeLanTransport([response])
                )

                health = await provider.health()

                self.assertEqual(health.status, ProviderHealthStatus.DEGRADED)
                self.assertFalse(health.details["ready"])

    async def test_missing_url_is_optional_and_never_discovers(self) -> None:
        transport = FakeLanTransport()
        provider = LanInferenceProvider(
            LanInferenceConfig(), transport=transport
        )

        health = await provider.health()
        with self.assertRaises(LanInferenceConfigurationError):
            await provider.infer(InferenceRequest(prompt="not configured"))

        self.assertEqual(health.status, ProviderHealthStatus.UNAVAILABLE)
        self.assertEqual(health.details["configured"], False)
        self.assertEqual(transport.calls, [])
        self.assertNotIn("base_url", provider.metadata.attributes)

    async def test_api_network_and_response_failures_are_structured(self) -> None:
        api_provider = LanInferenceProvider(
            _config(),
            transport=FakeLanTransport(
                [
                    LanInferenceHttpResponse(
                        status_code=500,
                        payload={"error": {"code": 500, "message": "failed"}},
                    )
                ]
            ),
        )
        network_provider = LanInferenceProvider(
            _config(),
            transport=FakeLanTransport(
                error=LanInferenceNetworkError("timed out")
            ),
        )
        malformed_provider = LanInferenceProvider(
            _config(),
            transport=FakeLanTransport(
                [LanInferenceHttpResponse(status_code=200, payload={})]
            ),
        )

        with self.assertRaises(LanInferenceAPIError) as api_error:
            await api_provider.infer(InferenceRequest(prompt="api"))
        with self.assertRaises(LanInferenceNetworkError):
            await network_provider.infer(InferenceRequest(prompt="network"))
        with self.assertRaises(LanInferenceResponseError):
            await malformed_provider.infer(InferenceRequest(prompt="response"))

        self.assertEqual(api_error.exception.status_code, 500)
        self.assertEqual(api_error.exception.api_error_code, 500)

    async def test_reserved_fields_fail_before_network_access(self) -> None:
        transport = FakeLanTransport()
        provider = LanInferenceProvider(_config(), transport=transport)

        with self.assertRaises(LanInferenceRequestError):
            await provider.infer(
                InferenceRequest(
                    prompt="hello",
                    parameters={"messages": []},
                )
            )

        self.assertEqual(transport.calls, [])

    async def test_provider_does_not_fallback_but_router_can(self) -> None:
        transport = FakeLanTransport(
            [
                LanInferenceHttpResponse(
                    status_code=503,
                    payload={"error": {"message": "model loading"}},
                )
            ]
        )
        lan = LanInferenceProvider(_config(), transport=transport)
        offline = MockProvider(provider_id="offline", response="offline result")
        router = ProviderRouter(lan=lan, offline=offline)

        result = await router.infer(InferenceRequest(prompt="fallback once"))

        self.assertEqual(result.output, "offline result")
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(router.current_selected_provider.provider_id, "offline")
        self.assertEqual(len(router.failed_attempts), 1)


if __name__ == "__main__":
    unittest.main()
