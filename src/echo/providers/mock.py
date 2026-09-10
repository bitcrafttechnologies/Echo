"""Deterministic provider implementation for tests and local development."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timezone
from time import perf_counter

from echo.providers.base import (
    InferenceRequest,
    InferenceResult,
    ProviderCapability,
    ProviderHealthResult,
    ProviderHealthStatus,
    ProviderMetadata,
    ProviderTiming,
    ProviderUnavailableError,
)


class MockProvider:
    """A dependency-free provider with configurable responses and health."""

    def __init__(
        self,
        *,
        provider_id: str = "mock",
        name: str = "Mock Provider",
        model: str | None = "mock-model",
        response: str = "mock inference result",
        responses: Iterable[str] | None = None,
        status: ProviderHealthStatus | str = ProviderHealthStatus.HEALTHY,
        latency_seconds: float = 0.0,
    ) -> None:
        if latency_seconds < 0:
            raise ValueError("latency_seconds must be non-negative")
        self._metadata = ProviderMetadata(
            provider_id=provider_id,
            name=name,
            model=model,
            capabilities=(ProviderCapability.INFER,),
            attributes={"mock": True},
        )
        self._response = response
        self._responses = list(responses or ())
        self._status = ProviderHealthStatus(status)
        self._latency_seconds = latency_seconds
        self._requests: list[InferenceRequest] = []

    @property
    def metadata(self) -> ProviderMetadata:
        return self._metadata

    @property
    def requests(self) -> tuple[InferenceRequest, ...]:
        return tuple(self._requests)

    @property
    def status(self) -> ProviderHealthStatus:
        return self._status

    def set_status(self, status: ProviderHealthStatus | str) -> None:
        self._status = ProviderHealthStatus(status)

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        if not isinstance(request, InferenceRequest):
            raise TypeError("request must be an InferenceRequest")
        started_at = datetime.now(timezone.utc)
        started_counter = perf_counter()
        if self._latency_seconds:
            await asyncio.sleep(self._latency_seconds)
        if self._status is ProviderHealthStatus.UNAVAILABLE:
            raise ProviderUnavailableError(
                f"provider {self.metadata.provider_id!r} is unavailable"
            )
        self._requests.append(request)
        output = self._responses.pop(0) if self._responses else self._response
        return InferenceResult(
            request_id=request.request_id,
            output=output,
            provider_metadata=self.metadata,
            timing=_timing(started_at, started_counter),
            metadata={"mock": True},
        )

    async def health(self) -> ProviderHealthResult:
        started_at = datetime.now(timezone.utc)
        started_counter = perf_counter()
        if self._latency_seconds:
            await asyncio.sleep(self._latency_seconds)
        return ProviderHealthResult(
            status=self._status,
            provider_metadata=self.metadata,
            timing=_timing(started_at, started_counter),
            message="mock provider status",
            details={"configured": True},
        )


def _timing(started_at: datetime, started_counter: float) -> ProviderTiming:
    return ProviderTiming(
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
        duration_ms=(perf_counter() - started_counter) * 1000,
    )
