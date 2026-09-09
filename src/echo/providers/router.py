"""Centralized selection and bounded fallback for intelligence providers."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from inspect import isawaitable
from time import perf_counter
from typing import Any

from echo.providers.base import (
    InferenceRequest,
    InferenceResult,
    IntelligenceProvider,
    ProviderCapability,
    ProviderError,
    ProviderHealthResult,
    ProviderHealthStatus,
    ProviderMetadata,
    ProviderTiming,
    ProviderUnavailableError,
)


class ProviderMode(StrEnum):
    AUTO = "auto"
    REMOTE = "remote"
    LAN = "lan"
    OFFLINE = "offline"


class ProviderSlot(StrEnum):
    # Phase 5B reserves REMOTE for the default OpenRouter implementation that
    # arrives in a later concrete-provider phase.
    REMOTE = "remote"
    LAN = "lan"
    OFFLINE = "offline"


_AUTO_ORDER = (
    ProviderSlot.REMOTE,
    ProviderSlot.LAN,
    ProviderSlot.OFFLINE,
)


@dataclass(slots=True, kw_only=True, frozen=True)
class ProviderAttempt:
    slot: ProviderSlot
    provider_metadata: ProviderMetadata
    timing: ProviderTiming
    succeeded: bool
    error_reason: str | None = None

    @property
    def provider(self) -> ProviderMetadata:
        return self.provider_metadata

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot.value,
            "provider": self.provider_metadata.to_dict(),
            "timing": self.timing.to_dict(),
            "succeeded": self.succeeded,
            "error_reason": self.error_reason,
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class InferenceRouteRecord:
    request_id: str
    mode: ProviderMode
    served_by: ProviderMetadata | None
    attempts: tuple[ProviderAttempt, ...]
    latency_ms: float
    completed_at: datetime
    error_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "mode": self.mode.value,
            "served_by": self.served_by.to_dict() if self.served_by else None,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "latency_ms": self.latency_ms,
            "completed_at": self.completed_at.isoformat(),
            "error_reason": self.error_reason,
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class ProviderRouterStatus:
    mode: ProviderMode
    preference: tuple[ProviderSlot, ...]
    configured_providers: tuple[tuple[ProviderSlot, ProviderMetadata], ...]
    current_selected_provider: ProviderMetadata | None
    attempts: tuple[ProviderAttempt, ...]
    latency_ms: float | None
    error_reason: str | None
    request_id: str | None

    @property
    def failed_attempts(self) -> tuple[ProviderAttempt, ...]:
        return tuple(attempt for attempt in self.attempts if not attempt.succeeded)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "preference": [slot.value for slot in self.preference],
            "configured_providers": {
                slot.value: metadata.to_dict()
                for slot, metadata in self.configured_providers
            },
            "current_selected_provider": (
                self.current_selected_provider.to_dict()
                if self.current_selected_provider is not None
                else None
            ),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "failed_attempts": [
                attempt.to_dict() for attempt in self.failed_attempts
            ],
            "latency_ms": self.latency_ms,
            "error_reason": self.error_reason,
            "request_id": self.request_id,
        }


class InferenceUnavailableError(ProviderUnavailableError):
    """No provider allowed by the routing mode could serve an inference."""

    code = "inference_unavailable"

    def __init__(
        self,
        *,
        request_id: str,
        mode: ProviderMode,
        attempts: tuple[ProviderAttempt, ...],
        latency_ms: float,
        reason: str,
    ) -> None:
        super().__init__(reason)
        self.request_id = request_id
        self.mode = mode
        self.attempts = attempts
        self.latency_ms = latency_ms
        self.reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.reason,
            "request_id": self.request_id,
            "mode": self.mode.value,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "latency_ms": self.latency_ms,
        }


class ProviderNotAcceptingRequestsError(ProviderUnavailableError):
    """The provider router is quiescing or closed for restart."""


class ProviderShutdownError(ProviderError):
    """One or more configured providers failed to close cleanly."""


class ProviderRouter:
    """Select one allowed provider and perform a single bounded fallback pass."""

    def __init__(
        self,
        providers: Iterable[IntelligenceProvider] | None = None,
        *,
        remote: IntelligenceProvider | None = None,
        lan: IntelligenceProvider | None = None,
        offline: IntelligenceProvider | None = None,
        mode: ProviderMode | str = ProviderMode.AUTO,
        history_limit: int = 100,
        preference: Iterable[ProviderSlot | str] = _AUTO_ORDER,
    ) -> None:
        if providers is not None:
            if any(provider is not None for provider in (remote, lan, offline)):
                raise ValueError(
                    "positional providers cannot be combined with provider slots"
                )
            ordered = tuple(providers)
            if len(ordered) > len(_AUTO_ORDER):
                raise ValueError("ProviderRouter accepts at most three providers")
            slots = dict(zip(_AUTO_ORDER, ordered, strict=False))
        else:
            slots = {
                ProviderSlot.REMOTE: remote,
                ProviderSlot.LAN: lan,
                ProviderSlot.OFFLINE: offline,
            }

        configured = {
            slot: provider for slot, provider in slots.items() if provider is not None
        }
        provider_ids = [provider.metadata.provider_id for provider in configured.values()]
        if len(set(provider_ids)) != len(provider_ids):
            raise ValueError("configured providers must have unique provider IDs")

        if isinstance(history_limit, bool) or history_limit < 1:
            raise ValueError("history_limit must be a positive integer")
        preference = _validate_preference(preference)
        self._providers = configured
        self._mode = ProviderMode(mode)
        self._preference = preference
        self._metadata = _router_metadata(preference)
        self._current_provider: IntelligenceProvider | None = None
        self._current_selected_provider: ProviderMetadata | None = None
        self._attempts: tuple[ProviderAttempt, ...] = ()
        self._latency_ms: float | None = None
        self._error_reason: str | None = None
        self._request_id: str | None = None
        self._history: deque[InferenceRouteRecord] = deque(maxlen=history_limit)
        self._inference_lock = asyncio.Lock()
        self._accepting_requests = True

    @property
    def metadata(self) -> ProviderMetadata:
        return self._metadata

    @property
    def mode(self) -> ProviderMode:
        return self._mode

    @property
    def current_selected_provider(self) -> ProviderMetadata | None:
        return self._current_selected_provider

    @property
    def current_provider(self) -> IntelligenceProvider | None:
        return self._current_provider

    @property
    def failed_attempts(self) -> tuple[ProviderAttempt, ...]:
        return tuple(attempt for attempt in self._attempts if not attempt.succeeded)

    @property
    def latency_ms(self) -> float | None:
        return self._latency_ms

    @property
    def error_reason(self) -> str | None:
        return self._error_reason

    @property
    def accepting_requests(self) -> bool:
        return self._accepting_requests

    def quiesce(self) -> None:
        """Reject new inference while an in-flight request may finish."""

        self._accepting_requests = False

    async def aclose(self) -> None:
        """Wait for current inference, then close every capable provider."""

        self.quiesce()
        errors: list[str] = []
        async with self._inference_lock:
            for provider in self._providers.values():
                method = next(
                    (
                        candidate
                        for name in ("aclose", "close", "unload")
                        if callable(
                            candidate := getattr(provider, name, None)
                        )
                    ),
                    None,
                )
                if method is None:
                    continue
                try:
                    outcome = method()
                    if isawaitable(outcome):
                        await outcome
                except Exception as error:
                    errors.append(
                        f"{provider.metadata.provider_id}: "
                        f"{type(error).__name__}: {error}"
                    )
        if errors:
            raise ProviderShutdownError("; ".join(errors))

    def set_mode(self, mode: ProviderMode | str) -> None:
        self._mode = ProviderMode(mode)

    @property
    def preference(self) -> tuple[ProviderSlot, ...]:
        return self._preference

    @property
    def history_limit(self) -> int:
        return self._history.maxlen or 0

    def set_preference(
        self, preference: Iterable[ProviderSlot | str]
    ) -> None:
        self._preference = _validate_preference(preference)
        self._metadata = _router_metadata(self._preference)

    def resize_history(self, history_limit: int) -> None:
        if (
            isinstance(history_limit, bool)
            or not isinstance(history_limit, int)
            or history_limit < 1
        ):
            raise ValueError("history_limit must be a positive integer")
        self._history = deque(self._history, maxlen=history_limit)

    def recent_inferences(
        self, limit: int | None = None
    ) -> tuple[InferenceRouteRecord, ...]:
        if limit is not None and (isinstance(limit, bool) or limit < 0):
            raise ValueError("limit must be a non-negative integer")
        values = tuple(reversed(self._history))
        return values if limit is None else values[:limit]

    async def health_all(
        self,
    ) -> tuple[tuple[ProviderSlot, ProviderHealthResult], ...]:
        """Inspect every configured provider, independent of routing mode."""

        async def inspect(
            slot: ProviderSlot,
            provider: IntelligenceProvider,
        ) -> tuple[ProviderSlot, ProviderHealthResult]:
            started_at = datetime.now(timezone.utc)
            started = perf_counter()
            try:
                health = await provider.health()
                if not isinstance(health, ProviderHealthResult):
                    raise TypeError("provider returned a non-ProviderHealthResult value")
            except Exception as error:
                health = ProviderHealthResult(
                    status=ProviderHealthStatus.UNAVAILABLE,
                    provider_metadata=provider.metadata,
                    timing=_timing(started_at, started),
                    message="provider health check failed",
                    details={"error_reason": _error_reason(error)},
                )
            return slot, health

        configured = tuple(
            (slot, self._providers[slot])
            for slot in _AUTO_ORDER
            if slot in self._providers
        )
        return tuple(
            await asyncio.gather(
                *(inspect(slot, provider) for slot, provider in configured)
            )
        )

    def status(self) -> ProviderRouterStatus:
        return ProviderRouterStatus(
            mode=self._mode,
            preference=self._preference,
            configured_providers=tuple(
                (slot, self._providers[slot].metadata)
                for slot in _AUTO_ORDER
                if slot in self._providers
            ),
            current_selected_provider=self._current_selected_provider,
            attempts=self._attempts,
            latency_ms=self._latency_ms,
            error_reason=self._error_reason,
            request_id=self._request_id,
        )

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        if not isinstance(request, InferenceRequest):
            raise TypeError("request must be an InferenceRequest")
        self._require_accepting_requests()

        async with self._inference_lock:
            self._require_accepting_requests()
            route_started = perf_counter()
            route_mode = self._mode
            self._reset_route_state(request.request_id)
            attempts: list[ProviderAttempt] = []

            for slot, provider in self._allowed_providers(route_mode):
                attempt_started_at = datetime.now(timezone.utc)
                attempt_started = perf_counter()
                try:
                    result = await provider.infer(request)
                    if not isinstance(result, InferenceResult):
                        raise TypeError("provider returned a non-InferenceResult value")
                    if result.request_id != request.request_id:
                        raise ValueError("provider result request_id does not match request")
                except Exception as error:
                    reason = _error_reason(error)
                    attempts.append(
                        ProviderAttempt(
                            slot=slot,
                            provider_metadata=provider.metadata,
                            timing=_timing(attempt_started_at, attempt_started),
                            succeeded=False,
                            error_reason=reason,
                        )
                    )
                    continue

                attempts.append(
                    ProviderAttempt(
                        slot=slot,
                        provider_metadata=provider.metadata,
                        timing=_timing(attempt_started_at, attempt_started),
                        succeeded=True,
                    )
                )
                self._attempts = tuple(attempts)
                self._latency_ms = (perf_counter() - route_started) * 1000
                self._current_provider = provider
                self._current_selected_provider = provider.metadata
                self._record(
                    request.request_id,
                    route_mode,
                    provider.metadata,
                    self._attempts,
                )
                return result

            self._attempts = tuple(attempts)
            self._latency_ms = (perf_counter() - route_started) * 1000
            if attempts:
                self._error_reason = "; ".join(
                    f"{attempt.slot.value}: {attempt.error_reason}"
                    for attempt in attempts
                )
            else:
                self._error_reason = (
                    f"no provider is configured for mode {route_mode.value!r}"
                )
            self._record(
                request.request_id,
                route_mode,
                None,
                self._attempts,
                error_reason=self._error_reason,
            )
            raise InferenceUnavailableError(
                request_id=request.request_id,
                mode=route_mode,
                attempts=self._attempts,
                latency_ms=self._latency_ms,
                reason=self._error_reason,
            )

    def _require_accepting_requests(self) -> None:
        if not self._accepting_requests:
            raise ProviderNotAcceptingRequestsError(
                "provider router is not accepting new requests"
            )

    async def health(self) -> ProviderHealthResult:
        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        health_results: list[dict[str, Any]] = []
        statuses: list[ProviderHealthStatus] = []

        for slot, provider in self._allowed_providers():
            try:
                result = await provider.health()
                if not isinstance(result, ProviderHealthResult):
                    raise TypeError("provider returned a non-ProviderHealthResult value")
                statuses.append(result.status)
                health_results.append(
                    {"slot": slot.value, "health": result.to_dict()}
                )
            except Exception as error:
                statuses.append(ProviderHealthStatus.UNAVAILABLE)
                health_results.append(
                    {
                        "slot": slot.value,
                        "provider": provider.metadata.to_dict(),
                        "status": ProviderHealthStatus.UNAVAILABLE.value,
                        "error_reason": _error_reason(error),
                    }
                )

        status = _aggregate_health(statuses)
        return ProviderHealthResult(
            status=status,
            provider_metadata=self.metadata,
            timing=_timing(started_at, started),
            message="provider router health",
            details={
                "mode": self._mode.value,
                "providers": health_results,
                "routing_status": self.status().to_dict(),
            },
        )

    def _allowed_providers(
        self,
        mode: ProviderMode | None = None,
    ) -> tuple[tuple[ProviderSlot, IntelligenceProvider], ...]:
        mode = mode or self._mode
        slots = (
            self._preference
            if mode is ProviderMode.AUTO
            else (ProviderSlot(mode.value),)
        )
        return tuple(
            (slot, self._providers[slot])
            for slot in slots
            if slot in self._providers
        )

    def _reset_route_state(self, request_id: str) -> None:
        self._current_provider = None
        self._current_selected_provider = None
        self._attempts = ()
        self._latency_ms = None
        self._error_reason = None
        self._request_id = request_id

    def _record(
        self,
        request_id: str,
        mode: ProviderMode,
        served_by: ProviderMetadata | None,
        attempts: tuple[ProviderAttempt, ...],
        *,
        error_reason: str | None = None,
    ) -> None:
        assert self._latency_ms is not None
        self._history.append(
            InferenceRouteRecord(
                request_id=request_id,
                mode=mode,
                served_by=served_by,
                attempts=attempts,
                latency_ms=self._latency_ms,
                completed_at=datetime.now(timezone.utc),
                error_reason=error_reason,
            )
        )


def _validate_preference(
    preference: Iterable[ProviderSlot | str],
) -> tuple[ProviderSlot, ...]:
    if isinstance(preference, (str, ProviderSlot)):
        raise ValueError("preference must list every provider slot once")
    try:
        slots = tuple(ProviderSlot(slot) for slot in preference)
    except (TypeError, ValueError) as error:
        raise ValueError("preference contains an unknown provider slot") from error
    if len(slots) != len(_AUTO_ORDER) or set(slots) != set(_AUTO_ORDER):
        raise ValueError("preference must list remote, lan, and offline exactly once")
    return slots


def _router_metadata(preference: tuple[ProviderSlot, ...]) -> ProviderMetadata:
    return ProviderMetadata(
        provider_id="provider-router",
        name="Provider Router",
        capabilities=(ProviderCapability.INFER,),
        attributes={
            "preference": [slot.value for slot in preference],
            "slots": [slot.value for slot in _AUTO_ORDER],
        },
    )


def _timing(started_at: datetime, started_counter: float) -> ProviderTiming:
    return ProviderTiming(
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
        duration_ms=(perf_counter() - started_counter) * 1000,
    )


def _error_reason(error: Exception) -> str:
    detail = str(error).strip()
    return f"{type(error).__name__}: {detail}" if detail else type(error).__name__


def _aggregate_health(
    statuses: list[ProviderHealthStatus],
) -> ProviderHealthStatus:
    if not statuses:
        return ProviderHealthStatus.UNAVAILABLE
    if ProviderHealthStatus.HEALTHY in statuses:
        return ProviderHealthStatus.HEALTHY
    if ProviderHealthStatus.DEGRADED in statuses:
        return ProviderHealthStatus.DEGRADED
    if ProviderHealthStatus.UNKNOWN in statuses:
        return ProviderHealthStatus.UNKNOWN
    return ProviderHealthStatus.UNAVAILABLE
