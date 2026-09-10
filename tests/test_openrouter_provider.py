from __future__ import annotations

from collections.abc import Mapping
import os
import sys
import unittest
from typing import Any


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo.providers import (
    InferenceRequest,
    MockProvider,
    OpenRouterAPIError,
    OpenRouterConfig,
    OpenRouterConfigurationError,
    OpenRouterHttpResponse,
    OpenRouterNetworkError,
    OpenRouterProvider,
    OpenRouterRequestError,
    OpenRouterResponseError,
    ProviderHealthStatus,
    ProviderRouter,
)


class FakeOpenRouterTransport:
    def __init__(
        self,
        responses: list[OpenRouterHttpResponse] | None = None,
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
    ) -> OpenRouterHttpResponse:
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
            raise AssertionError("unexpected OpenRouter transport request")
        return self.responses.pop(0)


def _config(*, api_key: str | None = "test-secret") -> OpenRouterConfig:
    return OpenRouterConfig(
        api_key=api_key,
        model="openai/test-model",
        app_url="https://echo.example",
        app_title="Echo Tests",
        timeout_seconds=12,
    )


def _completion(
    *,
    content: str = "normalized output",
    model: str = "openai/served-model",
) -> OpenRouterHttpResponse:
    return OpenRouterHttpResponse(
        status_code=200,
        payload={
            "id": "generation-1",
            "model": model,
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 6,
            },
        },
    )


class OpenRouterProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_configuration_reads_environment_without_exposing_secret(self) -> None:
        config = OpenRouterConfig.from_env(
            {
                "OPENROUTER_API_KEY": "environment-secret",
                "OPENROUTER_MODEL": "anthropic/test-model",
                "OPENROUTER_BASE_URL": "https://router.example/v1/",
                "OPENROUTER_TIMEOUT_SECONDS": "9.5",
                "OPENROUTER_APP_URL": "https://echo.example",
                "OPENROUTER_APP_TITLE": "Echo Dev",
            }
        )
        provider = OpenRouterProvider(config)

        self.assertEqual(config.model, "anthropic/test-model")
        self.assertEqual(config.base_url, "https://router.example/v1")
        self.assertEqual(config.timeout_seconds, 9.5)
        self.assertNotIn("environment-secret", repr(config))
        self.assertNotIn("environment-secret", str(provider.metadata.to_dict()))

        url_alias = OpenRouterConfig.from_env(
            {"OPENROUTER_URL": "https://router-alias.example/v1"}
        )
        self.assertEqual(
            url_alias.base_url, "https://router-alias.example/v1"
        )

    async def test_infer_uses_openai_compatible_request_and_normalizes_result(
        self,
    ) -> None:
        transport = FakeOpenRouterTransport([_completion()])
        provider = OpenRouterProvider(_config(), transport=transport)
        request = InferenceRequest(
            prompt="Keep the laptop cool",
            instructions="Speak as Bit, never as the provider.",
            context={"entity_id": "bit", "state": {"fatigue": 0.2}},
            parameters={"temperature": 0.2, "max_tokens": 50},
        )

        with self.assertLogs(
            "echo.providers.openrouter", level="INFO"
        ) as captured:
            result = await provider.infer(request)

        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(
            call["url"], "https://openrouter.ai/api/v1/chat/completions"
        )
        self.assertEqual(call["headers"]["Authorization"], "Bearer test-secret")
        self.assertEqual(call["headers"]["HTTP-Referer"], "https://echo.example")
        self.assertEqual(call["headers"]["X-OpenRouter-Title"], "Echo Tests")
        self.assertEqual(call["json_body"]["model"], "openai/test-model")
        self.assertEqual(
            call["json_body"]["messages"],
            [
                {
                    "role": "system",
                    "content": "Speak as Bit, never as the provider.",
                },
                {
                    "role": "system",
                    "content": '{"echo_character_context":{"entity_id":"bit","state":{"fatigue":0.2}}}',
                },
                {"role": "user", "content": "Keep the laptop cool"},
            ],
        )
        self.assertFalse(call["json_body"]["stream"])
        self.assertEqual(result.output, "normalized output")
        self.assertEqual(result.provider.provider_id, "openrouter")
        self.assertEqual(result.provider.model, "openai/served-model")
        self.assertEqual(result.metadata["configured_model"], "openai/test-model")
        self.assertEqual(result.metadata["finish_reason"], "stop")
        self.assertEqual(result.metadata["usage"]["total_tokens"], 6)
        self.assertGreaterEqual(result.timing.duration_ms, 0)
        self.assertNotIn("test-secret", "\n".join(captured.output))
        record = captured.records[0]
        self.assertEqual(record.provider_id, "openrouter")
        self.assertEqual(record.model, "openai/served-model")
        self.assertGreaterEqual(record.duration_ms, 0)

    async def test_missing_credentials_fail_without_network_access(self) -> None:
        transport = FakeOpenRouterTransport()
        provider = OpenRouterProvider(
            _config(api_key=None), transport=transport
        )

        with self.assertRaises(OpenRouterConfigurationError) as raised:
            await provider.infer(InferenceRequest(prompt="hello"))
        health = await provider.health()

        self.assertEqual(raised.exception.code, "openrouter_not_configured")
        self.assertEqual(health.status, ProviderHealthStatus.UNAVAILABLE)
        self.assertEqual(transport.calls, [])

    async def test_health_validates_current_key(self) -> None:
        transport = FakeOpenRouterTransport(
            [OpenRouterHttpResponse(status_code=200, payload={"data": {}})]
        )
        provider = OpenRouterProvider(_config(), transport=transport)

        health = await provider.health()

        self.assertEqual(health.status, ProviderHealthStatus.HEALTHY)
        self.assertTrue(health.details["authenticated"])
        self.assertEqual(transport.calls[0]["method"], "GET")
        self.assertEqual(
            transport.calls[0]["url"], "https://openrouter.ai/api/v1/key"
        )

    async def test_health_normalizes_auth_and_service_errors(self) -> None:
        for status_code, expected in (
            (401, ProviderHealthStatus.UNAVAILABLE),
            (503, ProviderHealthStatus.DEGRADED),
        ):
            with self.subTest(status_code=status_code):
                transport = FakeOpenRouterTransport(
                    [
                        OpenRouterHttpResponse(
                            status_code=status_code,
                            payload={"error": {"message": "service problem"}},
                        )
                    ]
                )
                provider = OpenRouterProvider(_config(), transport=transport)

                health = await provider.health()

                self.assertEqual(health.status, expected)
                self.assertEqual(health.details["status_code"], status_code)

    async def test_health_rejects_malformed_success_response(self) -> None:
        transport = FakeOpenRouterTransport(
            [OpenRouterHttpResponse(status_code=200, payload={})]
        )
        provider = OpenRouterProvider(_config(), transport=transport)

        health = await provider.health()

        self.assertEqual(health.status, ProviderHealthStatus.DEGRADED)
        self.assertFalse(health.details["authenticated"])

    async def test_api_error_is_structured_and_secret_is_redacted(self) -> None:
        secret = "secret-that-must-not-leak"
        transport = FakeOpenRouterTransport(
            [
                OpenRouterHttpResponse(
                    status_code=429,
                    payload={
                        "error": {
                            "code": 429,
                            "message": f"rate limited for {secret}",
                        }
                    },
                )
            ]
        )
        provider = OpenRouterProvider(
            _config(api_key=secret), transport=transport
        )

        with self.assertLogs(
            "echo.providers.openrouter", level="WARNING"
        ) as captured:
            with self.assertRaises(OpenRouterAPIError) as raised:
                await provider.infer(InferenceRequest(prompt="hello"))

        error = raised.exception
        self.assertEqual(error.status_code, 429)
        self.assertEqual(error.api_error_code, 429)
        self.assertNotIn(secret, error.message)
        self.assertNotIn(secret, str(error.to_dict()))
        self.assertNotIn(secret, "\n".join(captured.output))
        self.assertEqual(len(transport.calls), 1)

    async def test_transport_and_response_failures_are_provider_errors(self) -> None:
        network_provider = OpenRouterProvider(
            _config(),
            transport=FakeOpenRouterTransport(
                error=OpenRouterNetworkError("network unavailable")
            ),
        )
        malformed_provider = OpenRouterProvider(
            _config(),
            transport=FakeOpenRouterTransport(
                [OpenRouterHttpResponse(status_code=200, payload={"choices": []})]
            ),
        )

        with self.assertRaises(OpenRouterNetworkError):
            await network_provider.infer(InferenceRequest(prompt="network"))
        with self.assertRaises(OpenRouterResponseError):
            await malformed_provider.infer(InferenceRequest(prompt="malformed"))

    async def test_reserved_fields_cannot_bypass_provider_configuration(self) -> None:
        transport = FakeOpenRouterTransport()
        provider = OpenRouterProvider(_config(), transport=transport)

        with self.assertRaises(OpenRouterRequestError):
            await provider.infer(
                InferenceRequest(
                    prompt="hello",
                    parameters={"models": ["unauthorized/model"]},
                )
            )

        self.assertEqual(transport.calls, [])

    async def test_non_json_parameters_fail_before_network_access(self) -> None:
        transport = FakeOpenRouterTransport()
        provider = OpenRouterProvider(_config(), transport=transport)

        with self.assertRaises(OpenRouterRequestError):
            await provider.infer(
                InferenceRequest(prompt="hello", parameters={"bad": object()})
            )

        self.assertEqual(transport.calls, [])

    async def test_provider_does_not_fallback_but_router_can(self) -> None:
        transport = FakeOpenRouterTransport(
            [
                OpenRouterHttpResponse(
                    status_code=503,
                    payload={"error": {"message": "remote unavailable"}},
                )
            ]
        )
        openrouter = OpenRouterProvider(_config(), transport=transport)
        lan = MockProvider(provider_id="lan", response="LAN result")
        router = ProviderRouter(remote=openrouter, lan=lan)

        result = await router.infer(InferenceRequest(prompt="fallback once"))

        self.assertEqual(result.output, "LAN result")
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(router.current_selected_provider.provider_id, "lan")
        self.assertEqual(len(router.failed_attempts), 1)


if __name__ == "__main__":
    unittest.main()
