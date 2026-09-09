"""Provider-neutral contracts for optional intelligence resources."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        copied = deepcopy(dict(value))
    except Exception:
        copied = dict(value)
    return MappingProxyType(copied)


def _mapping_to_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return deepcopy(dict(value))
    except Exception:
        return dict(value)


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class ProviderCapability(StrEnum):
    """Operations a provider advertises without implying an implementation."""

    INFER = "infer"
    EMBED = "embed"
    CLASSIFY = "classify"


class ProviderHealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ProviderError(Exception):
    """Base error raised at the intelligence-provider boundary."""


class ProviderUnavailableError(ProviderError):
    """The selected provider cannot currently serve the request."""


@dataclass(slots=True, kw_only=True, frozen=True)
class ProviderMetadata:
    """Stable, implementation-neutral facts published by a provider."""

    provider_id: str
    name: str
    capabilities: tuple[ProviderCapability, ...] = (ProviderCapability.INFER,)
    model: str | None = None
    version: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.provider_id, "provider_id")
        _require_text(self.name, "name")
        capabilities = tuple(
            capability
            if isinstance(capability, ProviderCapability)
            else ProviderCapability(capability)
            for capability in self.capabilities
        )
        if not capabilities:
            raise ValueError("capabilities must not be empty")
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("capabilities must not contain duplicates")
        if self.model is not None:
            _require_text(self.model, "model")
        if self.version is not None:
            _require_text(self.version, "version")
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "attributes", _immutable_mapping(self.attributes))

    def supports(self, capability: ProviderCapability | str) -> bool:
        return ProviderCapability(capability) in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "name": self.name,
            "capabilities": [item.value for item in self.capabilities],
            "model": self.model,
            "version": self.version,
            "attributes": _mapping_to_dict(self.attributes),
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class ProviderTiming:
    started_at: datetime
    completed_at: datetime
    duration_ms: float

    def __post_init__(self) -> None:
        _require_aware(self.started_at, "started_at")
        _require_aware(self.completed_at, "completed_at")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        if self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "duration_ms": self.duration_ms,
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class InferenceRequest:
    """Provider-neutral text inference input assembled by Echo."""

    prompt: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        _require_text(self.prompt, "prompt")
        _require_text(self.request_id, "request_id")
        _require_aware(self.created_at, "created_at")
        object.__setattr__(self, "parameters", _immutable_mapping(self.parameters))
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "prompt": self.prompt,
            "parameters": _mapping_to_dict(self.parameters),
            "metadata": _mapping_to_dict(self.metadata),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class InferenceResult:
    request_id: str
    output: str
    provider_metadata: ProviderMetadata
    timing: ProviderTiming
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.request_id, "request_id")
        if not isinstance(self.output, str):
            raise ValueError("output must be a string")
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))

    @property
    def provider(self) -> ProviderMetadata:
        """Concise alias for callers inspecting the serving provider."""

        return self.provider_metadata

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "output": self.output,
            "provider": self.provider_metadata.to_dict(),
            "timing": self.timing.to_dict(),
            "metadata": _mapping_to_dict(self.metadata),
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class ProviderHealthResult:
    status: ProviderHealthStatus
    provider_metadata: ProviderMetadata
    timing: ProviderTiming
    message: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            object.__setattr__(self, "status", ProviderHealthStatus(self.status))
        object.__setattr__(self, "details", _immutable_mapping(self.details))

    @property
    def provider(self) -> ProviderMetadata:
        return self.provider_metadata

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "provider": self.provider_metadata.to_dict(),
            "timing": self.timing.to_dict(),
            "message": self.message,
            "details": _mapping_to_dict(self.details),
        }


@runtime_checkable
class IntelligenceProvider(Protocol):
    """Small async contract implemented by every intelligence provider."""

    @property
    def metadata(self) -> ProviderMetadata: ...

    async def infer(self, request: InferenceRequest) -> InferenceResult: ...

    async def health(self) -> ProviderHealthResult: ...


# These capability-specific contracts are extension seams, not requirements on
# every provider. Their request/result vocabulary can evolve when the features
# are implemented without expanding the base IntelligenceProvider contract.
@dataclass(slots=True, kw_only=True, frozen=True)
class EmbeddingRequest:
    inputs: tuple[str, ...]
    parameters: Mapping[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        inputs = tuple(self.inputs)
        if not inputs:
            raise ValueError("inputs must not be empty")
        for value in inputs:
            _require_text(value, "embedding input")
        _require_text(self.request_id, "request_id")
        object.__setattr__(self, "inputs", inputs)
        object.__setattr__(self, "parameters", _immutable_mapping(self.parameters))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "inputs": list(self.inputs),
            "parameters": _mapping_to_dict(self.parameters),
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class EmbeddingResult:
    request_id: str
    vectors: tuple[tuple[float, ...], ...]
    provider_metadata: ProviderMetadata
    timing: ProviderTiming

    def __post_init__(self) -> None:
        _require_text(self.request_id, "request_id")
        vectors = tuple(
            tuple(float(value) for value in vector) for vector in self.vectors
        )
        object.__setattr__(self, "vectors", vectors)

    @property
    def provider(self) -> ProviderMetadata:
        return self.provider_metadata

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "vectors": [list(vector) for vector in self.vectors],
            "provider": self.provider_metadata.to_dict(),
            "timing": self.timing.to_dict(),
        }


@runtime_checkable
class EmbeddingProvider(IntelligenceProvider, Protocol):
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult: ...


@dataclass(slots=True, kw_only=True, frozen=True)
class ClassificationRequest:
    input: str
    labels: tuple[str, ...]
    parameters: Mapping[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        _require_text(self.input, "input")
        labels = tuple(self.labels)
        if not labels:
            raise ValueError("labels must not be empty")
        for value in labels:
            _require_text(value, "label")
        _require_text(self.request_id, "request_id")
        object.__setattr__(self, "labels", labels)
        object.__setattr__(self, "parameters", _immutable_mapping(self.parameters))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "input": self.input,
            "labels": list(self.labels),
            "parameters": _mapping_to_dict(self.parameters),
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class ClassificationResult:
    request_id: str
    label: str
    scores: Mapping[str, float]
    provider_metadata: ProviderMetadata
    timing: ProviderTiming

    def __post_init__(self) -> None:
        _require_text(self.request_id, "request_id")
        _require_text(self.label, "label")
        scores = {str(label): float(score) for label, score in self.scores.items()}
        object.__setattr__(self, "scores", _immutable_mapping(scores))

    @property
    def provider(self) -> ProviderMetadata:
        return self.provider_metadata

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "label": self.label,
            "scores": dict(self.scores),
            "provider": self.provider_metadata.to_dict(),
            "timing": self.timing.to_dict(),
        }


@runtime_checkable
class ClassificationProvider(IntelligenceProvider, Protocol):
    async def classify(
        self, request: ClassificationRequest
    ) -> ClassificationResult: ...
