"""OpenAI-compatible inference provider for an explicitly configured LAN host."""

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
from urllib.parse import urlsplit, urlunsplit
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

DEFAULT_LAN_MODEL = "local-model"
_RESERVED_PARAMETERS = frozenset(
    {"messages", "model", "models", "route", "stream"}
)


@dataclass(slots=True, kw_only=True, frozen=True)
class LanInferenceConfig:
    """Configuration for one known OpenAI-compatible LAN inference server."""

    base_url: str | None = None
    model: str = DEFAULT_LAN_MODEL
    timeout_seconds: float = 30.0
    api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        base_url = _normalize_base_url(self.base_url)
        model = self.model.strip() if isinstance(self.model, str) else ""
        api_key = self.api_key.strip() if self.api_key else None
        if not model:
            raise ValueError("LAN inference model must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("LAN inference timeout_seconds must be positive")
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "api_key", api_key)

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> LanInferenceConfig:
        values = os.environ if environ is None else environ
        base_url = _first_present(
            values,
            "LAN_INFERENCE_URL",
            "LAN_INFERENCE_BASE_URL",
            "LLAMA_CPP_BASE_URL",
        )
        model = _first_present(
            values,
            "LAN_INFERENCE_MODEL",
            "LLAMA_CPP_MODEL",
        )
        timeout_value = _first_present(
            values,
            "LAN_INFERENCE_TIMEOUT_SECONDS",
            "LLAMA_CPP_TIMEOUT_SECONDS",
        )
        try:
            timeout_seconds = float(timeout_value or "30")
        except (TypeError, ValueError) as error:
            raise ValueError(
                "LAN inference timeout environment value must be a number"
            ) from error
        return cls(
            base_url=base_url,
            model=model or DEFAULT_LAN_MODEL,
            timeout_seconds=timeout_seconds,
            api_key=_first_present(
                values,
                "LAN_INFERENCE_API_KEY",
                "LLAMA_CPP_API_KEY",
            ),
        )


@dataclass(slots=True, kw_only=True, frozen=True)
class LanInferenceHttpResponse:
    status_code: int
    payload: Any


class LanInferenceTransport(Protocol):
    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> LanInferenceHttpResponse: ...


class LanInferenceError(ProviderError):
    code = "lan_inference_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


class LanInferenceConfigurationError(
    LanInferenceError, ProviderUnavailableError
):
    code = "lan_inference_not_configured"


class LanInferenceRequestError(LanInferenceError):
    code = "lan_inference_invalid_request"


class LanInferenceNetworkError(LanInferenceError, ProviderUnavailableError):
    code = "lan_inference_network_error"


class LanInferenceResponseError(LanInferenceError):
    code = "lan_inference_invalid_response"


class LanInferenceAPIError(LanInferenceError, ProviderUnavailableError):
    code = "lan_inference_api_error"

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


class LanInferenceProvider:
    """One configured LAN endpoint; fallback remains a router responsibility."""

    def __init__(
        self,
        config: LanInferenceConfig | None = None,
        *,
        transport: LanInferenceTransport | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if config is not None and environ is not None:
            raise ValueError("config and environ cannot be supplied together")
        self._config = config or LanInferenceConfig.from_env(environ)
        self._transport = transport or _UrllibLanInferenceTransport()
        attributes: dict[str, Any] = {
            "server_type": "llama.cpp/openai-compatible",
            "network": "lan",
        }
        if self._config.base_url is not None:
            attributes["base_url"] = self._config.base_url
        self._metadata = ProviderMetadata(
            provider_id="lan",
            name="LAN Inference Server",
            model=self._config.model,
            capabilities=(ProviderCapability.INFER,),
            attributes=attributes,
        )

    @property
    def metadata(self) -> ProviderMetadata:
        return self._metadata

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        if not isinstance(request, InferenceRequest):
            raise TypeError("request must be an InferenceRequest")
        base_url = self._require_base_url()
        conflicting = _RESERVED_PARAMETERS.intersection(request.parameters)
        if conflicting:
            fields = ", ".join(sorted(conflicting))
            raise LanInferenceRequestError(
                f"inference parameters cannot override reserved fields: {fields}"
            )

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
            raise LanInferenceRequestError(
                "inference parameters must be JSON-serializable"
            ) from error

        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        try:
            response = await self._transport.request(
                method="POST",
                url=f"{base_url}/chat/completions",
                headers=self._headers(),
                json_body=body,
                timeout_seconds=self._config.timeout_seconds,
            )
            self._raise_for_status(response)
            output, response_metadata = self._normalize_response(response.payload)
        except asyncio.CancelledError:
            raise
        except LanInferenceError as error:
            timing = _timing(started_at, started)
            self._log_failure(request.request_id, timing, error)
            raise
        except Exception as error:
            timing = _timing(started_at, started)
            wrapped = LanInferenceNetworkError(
                _redact(f"LAN inference request failed: {error}", self._config.api_key)
            )
            self._log_failure(request.request_id, timing, wrapped)
            raise wrapped from error

        timing = _timing(started_at, started)
        actual_model = response_metadata.pop("model", self._config.model)
        provider_metadata = ProviderMetadata(
            provider_id=self.metadata.provider_id,
            name=self.metadata.name,
            model=actual_model,
            capabilities=self.metadata.capabilities,
            attributes=self.metadata.attributes,
        )
        logger.info(
            "LAN inference completed",
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
            metadata={
                "configured_model": self._config.model,
                **response_metadata,
            },
        )

    async def health(self) -> ProviderHealthResult:
        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        if self._config.base_url is None:
            return ProviderHealthResult(
                status=ProviderHealthStatus.UNAVAILABLE,
                provider_metadata=self.metadata,
                timing=_timing(started_at, started),
                message="LAN inference base URL is not configured",
                details={"configured": False},
            )

        try:
            response = await self._transport.request(
                method="GET",
                url=f"{self._config.base_url}/health",
                headers=self._headers(),
                json_body=None,
                timeout_seconds=self._config.timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return ProviderHealthResult(
                status=ProviderHealthStatus.UNAVAILABLE,
                provider_metadata=self.metadata,
                timing=_timing(started_at, started),
                message=_redact(
                    f"LAN inference health check failed: {error}",
                    self._config.api_key,
                ),
                details={"configured": True, "ready": False},
            )

        if (
            200 <= response.status_code < 300
            and isinstance(response.payload, Mapping)
            and response.payload.get("status") == "ok"
        ):
            return ProviderHealthResult(
                status=ProviderHealthStatus.HEALTHY,
                provider_metadata=self.metadata,
                timing=_timing(started_at, started),
                message="LAN inference server is ready",
                details={"configured": True, "ready": True},
            )

        if 200 <= response.status_code < 300:
            return ProviderHealthResult(
                status=ProviderHealthStatus.DEGRADED,
                provider_metadata=self.metadata,
                timing=_timing(started_at, started),
                message="LAN inference server returned an invalid health response",
                details={"configured": True, "ready": False},
            )

        error = self._api_error(response)
        return ProviderHealthResult(
            status=(
                ProviderHealthStatus.DEGRADED
                if response.status_code == 503
                else ProviderHealthStatus.UNAVAILABLE
            ),
            provider_metadata=self.metadata,
            timing=_timing(started_at, started),
            message=error.message,
            details={
                "configured": True,
                "ready": False,
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

    def _require_base_url(self) -> str:
        if self._config.base_url is None:
            raise LanInferenceConfigurationError(
                "LAN inference base URL is not configured"
            )
        return self._config.base_url

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    def _raise_for_status(self, response: LanInferenceHttpResponse) -> None:
        if not 200 <= response.status_code < 300:
            raise self._api_error(response)

    def _api_error(
        self,
        response: LanInferenceHttpResponse,
    ) -> LanInferenceAPIError:
        message = f"LAN inference server returned HTTP {response.status_code}"
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
        return LanInferenceAPIError(
            _redact(message, self._config.api_key),
            status_code=response.status_code,
            api_error_code=api_error_code,
        )

    def _normalize_response(
        self,
        payload: Any,
    ) -> tuple[str, dict[str, Any]]:
        if not isinstance(payload, Mapping):
            raise LanInferenceResponseError(
                "LAN inference server returned a non-object response"
            )
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LanInferenceResponseError(
                "LAN inference response has no choices"
            )
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise LanInferenceResponseError(
                "LAN inference server returned an invalid choice"
            )
        message = choice.get("message")
        if not isinstance(message, Mapping) or not isinstance(
            message.get("content"), str
        ):
            raise LanInferenceResponseError(
                "LAN inference response has no text message content"
            )

        metadata: dict[str, Any] = {}
        for source, destination in (
            ("id", "response_id"),
            ("model", "model"),
            ("usage", "usage"),
            ("timings", "server_timings"),
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
        error: LanInferenceError,
    ) -> None:
        logger.warning(
            "LAN inference failed",
            extra={
                "provider_id": self.metadata.provider_id,
                "model": self.metadata.model,
                "request_id": request_id,
                "duration_ms": timing.duration_ms,
                "error_code": error.code,
            },
        )


class _UrllibLanInferenceTransport:
    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> LanInferenceHttpResponse:
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
    ) -> LanInferenceHttpResponse:
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
                return LanInferenceHttpResponse(
                    status_code=response.status,
                    payload=_decode_json(response.read()),
                )
        except HTTPError as error:
            return LanInferenceHttpResponse(
                status_code=error.code,
                payload=_decode_error_json(error.read()),
            )
        except (TimeoutError, URLError, OSError) as error:
            detail = error.reason if isinstance(error, URLError) else error
            raise LanInferenceNetworkError(
                f"LAN inference network request failed: {detail}"
            ) from error


def _normalize_base_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("LAN inference base_url must be an HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("LAN inference base_url must not contain credentials")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
    if not path.endswith("/v1"):
        path = f"{path}/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _first_present(
    values: Mapping[str, str],
    *names: str,
) -> str | None:
    for name in names:
        value = values.get(name)
        if value is not None and value.strip():
            return value
    return None


def _decode_json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LanInferenceResponseError(
            "LAN inference server returned a non-JSON response"
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
