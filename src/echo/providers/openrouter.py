"""OpenRouter implementation of Echo's provider-neutral inference contract."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from inspect import isawaitable
import json
import logging
import os
from time import perf_counter
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from echo.providers.base import (
    InferenceRequest,
    InferenceResult,
    ProviderCapability,
    ProviderError,
    ProviderHealthResult,
    ProviderHealthStatus,
    ProviderMetadata,
    ProviderTiming,
    ProviderUnavailableError,
)


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "openrouter/auto"
_RESERVED_PARAMETERS = frozenset(
    {"messages", "model", "models", "route", "stream"}
)


@dataclass(slots=True, kw_only=True, frozen=True)
class OpenRouterConfig:
    """Non-global provider configuration; secrets are excluded from repr."""

    api_key: str | None = field(default=None, repr=False)
    model: str = DEFAULT_OPENROUTER_MODEL
    base_url: str = DEFAULT_OPENROUTER_BASE_URL
    timeout_seconds: float = 60.0
    app_url: str | None = None
    app_title: str | None = "Echo"

    def __post_init__(self) -> None:
        api_key = self.api_key.strip() if self.api_key else None
        model = self.model.strip() if isinstance(self.model, str) else ""
        base_url = self.base_url.strip().rstrip("/") if self.base_url else ""
        if not model:
            raise ValueError("OpenRouter model must not be empty")
        if not base_url:
            raise ValueError("OpenRouter base_url must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("OpenRouter timeout_seconds must be positive")
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "base_url", base_url)

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> OpenRouterConfig:
        values = os.environ if environ is None else environ
        timeout_value = values.get("OPENROUTER_TIMEOUT_SECONDS", "60")
        try:
            timeout_seconds = float(timeout_value)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "OPENROUTER_TIMEOUT_SECONDS must be a number"
            ) from error
        return cls(
            api_key=values.get("OPENROUTER_API_KEY"),
            model=values.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL),
            base_url=values.get(
                "OPENROUTER_BASE_URL",
                values.get("OPENROUTER_URL", DEFAULT_OPENROUTER_BASE_URL),
            ),
            timeout_seconds=timeout_seconds,
            app_url=values.get("OPENROUTER_APP_URL"),
            app_title=values.get("OPENROUTER_APP_TITLE", "Echo"),
        )


@dataclass(slots=True, kw_only=True, frozen=True)
class OpenRouterHttpResponse:
    status_code: int
    payload: Any


class OpenRouterTransport(Protocol):
    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> OpenRouterHttpResponse: ...


class OpenRouterError(ProviderError):
    code = "openrouter_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


class OpenRouterConfigurationError(OpenRouterError, ProviderUnavailableError):
    code = "openrouter_not_configured"


class OpenRouterRequestError(OpenRouterError):
    code = "openrouter_invalid_request"


class OpenRouterNetworkError(OpenRouterError, ProviderUnavailableError):
    code = "openrouter_network_error"


class OpenRouterResponseError(OpenRouterError):
    code = "openrouter_invalid_response"


class OpenRouterAPIError(OpenRouterError, ProviderUnavailableError):
    code = "openrouter_api_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        api_error_code: str | int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.api_error_code = api_error_code

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "status_code": self.status_code,
            "api_error_code": self.api_error_code,
        }


class OpenRouterProvider:
    """One OpenRouter API target; cross-provider fallback belongs to the router."""

    def __init__(
        self,
        config: OpenRouterConfig | None = None,
        *,
        transport: OpenRouterTransport | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if config is not None and environ is not None:
            raise ValueError("config and environ cannot be supplied together")
        self._config = config or OpenRouterConfig.from_env(environ)
        self._transport = transport or _UrllibOpenRouterTransport()
        self._metadata = ProviderMetadata(
            provider_id="openrouter",
            name="OpenRouter",
            model=self._config.model,
            capabilities=(ProviderCapability.INFER,),
            attributes={
                "base_url": self._config.base_url,
                "remote": True,
            },
        )

    @property
    def metadata(self) -> ProviderMetadata:
        return self._metadata

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        if not isinstance(request, InferenceRequest):
            raise TypeError("request must be an InferenceRequest")
        api_key = self._require_api_key()
        conflicting = _RESERVED_PARAMETERS.intersection(request.parameters)
        if conflicting:
            fields = ", ".join(sorted(conflicting))
            raise OpenRouterRequestError(
                f"inference parameters cannot override reserved fields: {fields}"
            )

        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        body = dict(request.parameters)
        messages: list[dict[str, str]] = []
        if request.context:
            messages.append(
                {
                    "role": "system",
                    "content": json.dumps(
                        {"echo_character_context": dict(request.context)},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            )
        messages.append({"role": "user", "content": request.prompt})
        body.update(
            {
                "model": self._config.model,
                "messages": messages,
                "stream": False,
            }
        )
        try:
            json.dumps(body)
        except (TypeError, ValueError) as error:
            raise OpenRouterRequestError(
                "inference parameters must be JSON-serializable"
            ) from error

        try:
            response = await self._transport.request(
                method="POST",
                url=f"{self._config.base_url}/chat/completions",
                headers=self._headers(api_key),
                json_body=body,
                timeout_seconds=self._config.timeout_seconds,
            )
            self._raise_for_status(response)
            output, response_metadata = self._normalize_response(response.payload)
        except asyncio.CancelledError:
            raise
        except OpenRouterError as error:
            timing = _timing(started_at, started)
            self._log_failure(request.request_id, timing, error)
            raise
        except Exception as error:
            timing = _timing(started_at, started)
            wrapped = OpenRouterNetworkError(
                _redact(f"OpenRouter request failed: {error}", api_key)
            )
            self._log_failure(request.request_id, timing, wrapped)
            raise wrapped from error

        timing = _timing(started_at, started)
        actual_model = response_metadata.pop("model", self._config.model)
        result_metadata = {
            "configured_model": self._config.model,
            **response_metadata,
        }
        provider_metadata = ProviderMetadata(
            provider_id=self.metadata.provider_id,
            name=self.metadata.name,
            model=actual_model,
            capabilities=self.metadata.capabilities,
            attributes=self.metadata.attributes,
        )
        logger.info(
            "OpenRouter inference completed",
            extra={
                "provider_id": self.metadata.provider_id,
                "model": actual_model,
                "request_id": request.request_id,
                "duration_ms": timing.duration_ms,
            },
        )
        return InferenceResult(
            request_id=request.request_id,
            output=output,
            provider_metadata=provider_metadata,
            timing=timing,
            metadata=result_metadata,
        )

    async def health(self) -> ProviderHealthResult:
        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        if self._config.api_key is None:
            return ProviderHealthResult(
                status=ProviderHealthStatus.UNAVAILABLE,
                provider_metadata=self.metadata,
                timing=_timing(started_at, started),
                message="OPENROUTER_API_KEY is not configured",
                details={"configured": False},
            )

        try:
            response = await self._transport.request(
                method="GET",
                url=f"{self._config.base_url}/key",
                headers=self._headers(self._config.api_key),
                json_body=None,
                timeout_seconds=self._config.timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message = _redact(
                f"OpenRouter health check failed: {error}", self._config.api_key
            )
            return ProviderHealthResult(
                status=ProviderHealthStatus.UNAVAILABLE,
                provider_metadata=self.metadata,
                timing=_timing(started_at, started),
                message=message,
                details={"configured": True, "authenticated": False},
            )

        if (
            200 <= response.status_code < 300
            and isinstance(response.payload, Mapping)
            and isinstance(response.payload.get("data"), Mapping)
        ):
            return ProviderHealthResult(
                status=ProviderHealthStatus.HEALTHY,
                provider_metadata=self.metadata,
                timing=_timing(started_at, started),
                message="OpenRouter credentials accepted",
                details={"configured": True, "authenticated": True},
            )

        if 200 <= response.status_code < 300:
            return ProviderHealthResult(
                status=ProviderHealthStatus.DEGRADED,
                provider_metadata=self.metadata,
                timing=_timing(started_at, started),
                message="OpenRouter returned an invalid health response",
                details={"configured": True, "authenticated": False},
            )

        error = self._api_error(response)
        status = (
            ProviderHealthStatus.UNAVAILABLE
            if response.status_code in {401, 403}
            else ProviderHealthStatus.DEGRADED
        )
        return ProviderHealthResult(
            status=status,
            provider_metadata=self.metadata,
            timing=_timing(started_at, started),
            message=error.message,
            details={
                "configured": True,
                "authenticated": False,
                "status_code": response.status_code,
            },
        )

    async def aclose(self) -> None:
        """Close a custom transport when it exposes a close hook."""

        method = next(
            (
                candidate
                for name in ("aclose", "close")
                if callable(candidate := getattr(self._transport, name, None))
            ),
            None,
        )
        if method is not None:
            outcome = method()
            if isawaitable(outcome):
                await outcome

    def _require_api_key(self) -> str:
        if self._config.api_key is None:
            raise OpenRouterConfigurationError(
                "OPENROUTER_API_KEY is not configured"
            )
        return self._config.api_key

    def _headers(self, api_key: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if self._config.app_url:
            headers["HTTP-Referer"] = self._config.app_url
        if self._config.app_title:
            headers["X-OpenRouter-Title"] = self._config.app_title
        return headers

    def _raise_for_status(self, response: OpenRouterHttpResponse) -> None:
        if not 200 <= response.status_code < 300:
            raise self._api_error(response)

    def _api_error(self, response: OpenRouterHttpResponse) -> OpenRouterAPIError:
        message = f"OpenRouter returned HTTP {response.status_code}"
        api_error_code: str | int | None = None
        if isinstance(response.payload, Mapping):
            error = response.payload.get("error")
            if isinstance(error, Mapping):
                raw_message = error.get("message")
                if isinstance(raw_message, str) and raw_message.strip():
                    message = raw_message.strip()
                raw_code = error.get("code")
                if isinstance(raw_code, (str, int)):
                    api_error_code = raw_code
        return OpenRouterAPIError(
            _redact(message, self._config.api_key),
            status_code=response.status_code,
            api_error_code=api_error_code,
        )

    def _normalize_response(
        self,
        payload: Any,
    ) -> tuple[str, dict[str, Any]]:
        if not isinstance(payload, Mapping):
            raise OpenRouterResponseError("OpenRouter returned a non-object response")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterResponseError("OpenRouter response has no choices")
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise OpenRouterResponseError("OpenRouter returned an invalid choice")
        message = choice.get("message")
        if not isinstance(message, Mapping) or not isinstance(
            message.get("content"), str
        ):
            raise OpenRouterResponseError(
                "OpenRouter response has no text message content"
            )

        metadata: dict[str, Any] = {}
        for source, destination in (
            ("id", "response_id"),
            ("model", "model"),
            ("usage", "usage"),
        ):
            value = payload.get(source)
            if isinstance(value, (str, int, float, bool, dict, list)):
                metadata[destination] = value
        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str):
            metadata["finish_reason"] = finish_reason
        return message["content"], metadata

    def _log_failure(
        self,
        request_id: str,
        timing: ProviderTiming,
        error: OpenRouterError,
    ) -> None:
        logger.warning(
            "OpenRouter inference failed",
            extra={
                "provider_id": self.metadata.provider_id,
                "model": self.metadata.model,
                "request_id": request_id,
                "duration_ms": timing.duration_ms,
                "error_code": error.code,
            },
        )


class _UrllibOpenRouterTransport:
    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> OpenRouterHttpResponse:
        return await asyncio.to_thread(
            self._request_sync,
            method=method,
            url=url,
            headers=headers,
            json_body=json_body,
            timeout_seconds=timeout_seconds,
        )

    def _request_sync(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> OpenRouterHttpResponse:
        data = (
            json.dumps(dict(json_body)).encode("utf-8")
            if json_body is not None
            else None
        )
        request = Request(
            url=url,
            data=data,
            headers=dict(headers),
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return OpenRouterHttpResponse(
                    status_code=response.status,
                    payload=_decode_json(response.read()),
                )
        except HTTPError as error:
            return OpenRouterHttpResponse(
                status_code=error.code,
                payload=_decode_error_json(error.read()),
            )
        except (TimeoutError, URLError, OSError) as error:
            raise OpenRouterNetworkError(
                f"OpenRouter network request failed: {error.reason if isinstance(error, URLError) else error}"
            ) from error


def _decode_json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OpenRouterResponseError(
            "OpenRouter returned a non-JSON response"
        ) from error


def _decode_error_json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _timing(started_at: datetime, started_counter: float) -> ProviderTiming:
    return ProviderTiming(
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
        duration_ms=(perf_counter() - started_counter) * 1000,
    )


def _redact(message: str, secret: str | None) -> str:
    if secret:
        return message.replace(secret, "[REDACTED]")
    return message
