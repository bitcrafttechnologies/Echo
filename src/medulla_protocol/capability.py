"""Serializable, transport-neutral capability descriptions for Medulla.

This module is deliberately independent of both Echo and the Medulla Node
runtime so the same versioned contract can be installed on either side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping


CAPABILITY_CONTRACT_VERSION = 1

_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")


def _required_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _identifier(value: Any, path: str) -> str:
    value = _required_string(value, path)
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(
            f"{path} must contain only lowercase letters, numbers, '.', '_', or '-'"
        )
    return value


def _safe_json(value: Any, path: str = "value") -> Any:
    """Return a detached value from the ordinary JSON data model."""

    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        return value
    if type(value) is list:
        return [_safe_json(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if type(value) is dict:
        result: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{path} keys must be strings")
            result[key] = _safe_json(item, f"{path}.{key}")
        return result
    raise ValueError(
        f"{path} contains unsupported type "
        f"{type(value).__module__}.{type(value).__qualname__}"
    )


def _string_tuple(value: Iterable[str], path: str) -> tuple[str, ...]:
    if isinstance(value, str):
        raise ValueError(f"{path} must be a sequence of strings")
    result = tuple(value)
    if any(not isinstance(item, str) or not item for item in result):
        raise ValueError(f"{path} must contain non-empty strings")
    if len(set(result)) != len(result):
        raise ValueError(f"{path} must not contain duplicates")
    return result


class CapabilityProviderKind(StrEnum):
    """Implementation family, never an instruction for Echo Core."""

    NATIVE = "native"
    MCP = "mcp"
    WEBSOCKET = "websocket"
    MQTT = "mqtt"
    SERIAL = "serial"
    HTTP = "http"
    GPIO = "gpio"
    CAN = "can"
    ROS = "ros"


class CapabilityProviderLocation(StrEnum):
    LOCAL = "local"
    REMOTE = "remote"


class CapabilityAvailabilityState(StrEnum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class CapabilityProviderHealthState(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class CapabilityRisk(StrEnum):
    """Broad risk class used by later, external authorization policy."""

    NONE = "none"
    READ = "read"
    STATE_CHANGE = "state_change"
    NETWORK = "network"
    SENSITIVE_DATA = "sensitive_data"
    PHYSICAL_MOTION = "physical_motion"
    PRIVILEGED = "privileged"


class CapabilityEffect(StrEnum):
    READ_ONLY = "read_only"
    STATE_CHANGING = "state_changing"


@dataclass(slots=True, frozen=True, kw_only=True)
class CapabilityProvider:
    """Stable identity and placement of a capability provider."""

    provider_id: str
    kind: CapabilityProviderKind
    location: CapabilityProviderLocation
    display_name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _identifier(self.provider_id, "provider_id"))
        object.__setattr__(self, "kind", CapabilityProviderKind(self.kind))
        object.__setattr__(self, "location", CapabilityProviderLocation(self.location))
        if self.display_name is not None:
            _required_string(self.display_name, "display_name")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.provider_id,
            "kind": self.kind.value,
            "location": self.location.value,
        }
        if self.display_name is not None:
            result["display_name"] = self.display_name
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapabilityProvider:
        data = _closed_object(data, {"id", "kind", "location", "display_name"}, "provider")
        return cls(
            provider_id=data.get("id"),
            kind=data.get("kind"),
            location=data.get("location"),
            display_name=data.get("display_name"),
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class CapabilityProviderHealth:
    """Transport-neutral health observation for one registered provider."""

    provider_id: str
    state: CapabilityProviderHealthState = CapabilityProviderHealthState.UNKNOWN
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _identifier(self.provider_id, "provider_id"))
        object.__setattr__(self, "state", CapabilityProviderHealthState(self.state))
        if self.message is not None:
            _required_string(self.message, "provider_health.message")
        details = _safe_json(self.details, "provider_health.details")
        if not isinstance(details, dict):
            raise ValueError("provider_health.details must be an object")
        object.__setattr__(self, "details", details)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "provider_id": self.provider_id,
            "state": self.state.value,
            "details": _safe_json(self.details, "provider_health.details"),
        }
        if self.message is not None:
            result["message"] = self.message
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapabilityProviderHealth:
        data = _closed_object(
            data, {"provider_id", "state", "message", "details"}, "provider_health"
        )
        return cls(
            provider_id=data.get("provider_id"),
            state=data.get("state", CapabilityProviderHealthState.UNKNOWN.value),
            message=data.get("message"),
            details=data.get("details", {}),
        )


ProviderHealth = CapabilityProviderHealth
ProviderHealthState = CapabilityProviderHealthState


@dataclass(slots=True, frozen=True, kw_only=True)
class CapabilityAvailability:
    """Inspectable current availability without protocol-specific state."""

    state: CapabilityAvailabilityState = CapabilityAvailabilityState.UNKNOWN
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", CapabilityAvailabilityState(self.state))
        if self.message is not None:
            _required_string(self.message, "availability.message")
        details = _safe_json(self.details, "availability.details")
        if not isinstance(details, dict):
            raise ValueError("availability.details must be an object")
        object.__setattr__(self, "details", details)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "state": self.state.value,
            "details": _safe_json(self.details, "availability.details"),
        }
        if self.message is not None:
            result["message"] = self.message
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapabilityAvailability:
        data = _closed_object(data, {"state", "message", "details"}, "availability")
        return cls(
            state=data.get("state", CapabilityAvailabilityState.UNKNOWN.value),
            message=data.get("message"),
            details=data.get("details", {}),
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class CapabilityPermissions:
    """Declarative permission needs; policy and grants live outside this contract."""

    risk: CapabilityRisk
    required: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "risk", CapabilityRisk(self.risk))
        object.__setattr__(self, "required", _string_tuple(self.required, "permissions.required"))

    def to_dict(self) -> dict[str, Any]:
        return {"risk": self.risk.value, "required": list(self.required)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapabilityPermissions:
        data = _closed_object(data, {"risk", "required"}, "permissions")
        return cls(risk=data.get("risk"), required=data.get("required", ()))


@dataclass(slots=True, frozen=True, kw_only=True)
class CapabilityTimeout:
    """Normal and hard-limit timeout expectations in seconds."""

    expected_seconds: float
    maximum_seconds: float

    def __post_init__(self) -> None:
        for name, value in (
            ("expected_seconds", self.expected_seconds),
            ("maximum_seconds", self.maximum_seconds),
        ):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"timeout.{name} must be a positive finite number")
        if self.maximum_seconds < self.expected_seconds:
            raise ValueError("timeout.maximum_seconds must be >= expected_seconds")

    def to_dict(self) -> dict[str, float]:
        return {
            "expected_seconds": float(self.expected_seconds),
            "maximum_seconds": float(self.maximum_seconds),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapabilityTimeout:
        data = _closed_object(data, {"expected_seconds", "maximum_seconds"}, "timeout")
        return cls(
            expected_seconds=data.get("expected_seconds"),
            maximum_seconds=data.get("maximum_seconds"),
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class Capability:
    """One stable, serializable description of what the world can do.

    This is data only. It contains no callback, transport object, executable
    implementation, routing choice, permission grant, or cognitive behavior.
    """

    capability_id: str
    name: str
    description: str
    provider: CapabilityProvider
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    availability: CapabilityAvailability
    permissions: CapabilityPermissions
    effect: CapabilityEffect
    timeout: CapabilityTimeout
    contract_version: int = CAPABILITY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_id", _identifier(self.capability_id, "capability_id"))
        object.__setattr__(self, "name", _identifier(self.name, "name"))
        _required_string(self.description, "description")
        if not isinstance(self.provider, CapabilityProvider):
            raise TypeError("provider must be a CapabilityProvider")
        for field_name in ("input_schema", "output_schema"):
            schema = _safe_json(getattr(self, field_name), field_name)
            if not isinstance(schema, dict):
                raise ValueError(f"{field_name} must be an object")
            object.__setattr__(self, field_name, schema)
        if not isinstance(self.availability, CapabilityAvailability):
            raise TypeError("availability must be CapabilityAvailability")
        if not isinstance(self.permissions, CapabilityPermissions):
            raise TypeError("permissions must be CapabilityPermissions")
        object.__setattr__(self, "effect", CapabilityEffect(self.effect))
        if not isinstance(self.timeout, CapabilityTimeout):
            raise TypeError("timeout must be CapabilityTimeout")
        if (
            type(self.contract_version) is not int
            or self.contract_version != CAPABILITY_CONTRACT_VERSION
        ):
            raise ValueError(f"contract_version must be {CAPABILITY_CONTRACT_VERSION}")

    @property
    def qualified_name(self) -> str:
        """Provider-qualified lookup key; equal names never silently collide."""

        return f"{self.provider.provider_id}:{self.name}"

    @property
    def state_changing(self) -> bool:
        return self.effect is CapabilityEffect.STATE_CHANGING

    @property
    def read_only(self) -> bool:
        return self.effect is CapabilityEffect.READ_ONLY

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "id": self.capability_id,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "description": self.description,
            "provider": self.provider.to_dict(),
            "input_schema": _safe_json(self.input_schema, "input_schema"),
            "output_schema": _safe_json(self.output_schema, "output_schema"),
            "availability": self.availability.to_dict(),
            "permissions": self.permissions.to_dict(),
            "effect": self.effect.value,
            "state_changing": self.state_changing,
            "timeout": self.timeout.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Capability:
        data = _closed_object(
            data,
            {
                "contract_version", "id", "name", "qualified_name", "description",
                "provider", "input_schema", "output_schema", "availability",
                "permissions", "effect", "state_changing", "timeout",
            },
            "capability",
        )
        capability = cls(
            contract_version=data.get("contract_version"),
            capability_id=data.get("id"),
            name=data.get("name"),
            description=data.get("description"),
            provider=CapabilityProvider.from_dict(data.get("provider")),
            input_schema=data.get("input_schema"),
            output_schema=data.get("output_schema"),
            availability=CapabilityAvailability.from_dict(data.get("availability")),
            permissions=CapabilityPermissions.from_dict(data.get("permissions")),
            effect=data.get("effect"),
            timeout=CapabilityTimeout.from_dict(data.get("timeout")),
        )
        qualified_name = data.get("qualified_name")
        if qualified_name is not None and qualified_name != capability.qualified_name:
            raise ValueError("qualified_name does not match provider and name")
        state_changing = data.get("state_changing")
        if state_changing is not None and state_changing is not capability.state_changing:
            raise ValueError("state_changing does not match effect")
        return capability

    def with_availability(self, availability: CapabilityAvailability) -> Capability:
        """Return an updated observation without mutating the stable definition."""

        return Capability(
            capability_id=self.capability_id,
            name=self.name,
            description=self.description,
            provider=self.provider,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            availability=availability,
            permissions=self.permissions,
            effect=self.effect,
            timeout=self.timeout,
            contract_version=self.contract_version,
        )


# Descriptive alias for callers that prefer to distinguish the contract data
# from a future executable capability provider.
CapabilityDefinition = Capability


class CapabilityRegistryError(ValueError):
    pass


class CapabilityNameCollisionError(CapabilityRegistryError):
    pass


class CapabilityRegistry:
    """In-memory catalog with explicit, deterministic collision behavior."""

    def __init__(self, capabilities: Iterable[Capability] = ()) -> None:
        self._by_id: dict[str, Capability] = {}
        self._by_qualified_name: dict[str, str] = {}
        self._by_name: dict[str, set[str]] = {}
        self._providers: dict[str, CapabilityProvider] = {}
        self._provider_health: dict[str, CapabilityProviderHealth] = {}
        for capability in capabilities:
            self.register(capability)

    def register_provider(
        self,
        provider: CapabilityProvider,
        *,
        health: CapabilityProviderHealth | None = None,
    ) -> None:
        if not isinstance(provider, CapabilityProvider):
            raise TypeError("provider must be a CapabilityProvider")
        current = self._providers.get(provider.provider_id)
        if current is not None and current != provider:
            raise CapabilityRegistryError(
                f"provider id has conflicting metadata: {provider.provider_id}"
            )
        if health is not None:
            if not isinstance(health, CapabilityProviderHealth):
                raise TypeError("health must be CapabilityProviderHealth")
            if health.provider_id != provider.provider_id:
                raise CapabilityRegistryError(
                    "provider health provider_id does not match provider"
                )
        self._providers[provider.provider_id] = provider
        self._provider_health.setdefault(
            provider.provider_id,
            health
            or CapabilityProviderHealth(provider_id=provider.provider_id),
        )
        if health is not None:
            self._provider_health[provider.provider_id] = health

    def register(self, capability: Capability) -> None:
        if not isinstance(capability, Capability):
            raise TypeError("capability must be a Capability")
        if capability.capability_id in self._by_id:
            raise CapabilityRegistryError(
                f"duplicate capability id: {capability.capability_id}"
            )
        if capability.qualified_name in self._by_qualified_name:
            raise CapabilityRegistryError(
                f"duplicate provider-qualified capability name: {capability.qualified_name}"
            )
        self.register_provider(capability.provider)
        self._by_id[capability.capability_id] = capability
        self._by_qualified_name[capability.qualified_name] = capability.capability_id
        self._by_name.setdefault(capability.name, set()).add(capability.capability_id)

    def get(self, capability_id: str) -> Capability | None:
        return self._by_id.get(capability_id)

    def provider(self, provider_id: str) -> CapabilityProvider | None:
        return self._providers.get(provider_id)

    def providers(self) -> tuple[CapabilityProvider, ...]:
        return tuple(self._providers[item] for item in sorted(self._providers))

    def health(self, provider_id: str) -> CapabilityProviderHealth | None:
        return self._provider_health.get(provider_id)

    def update_provider_health(
        self, health: CapabilityProviderHealth
    ) -> CapabilityProviderHealth:
        if not isinstance(health, CapabilityProviderHealth):
            raise TypeError("health must be CapabilityProviderHealth")
        if health.provider_id not in self._providers:
            raise KeyError(health.provider_id)
        self._provider_health[health.provider_id] = health
        return health

    def capabilities_for_provider(self, provider_id: str) -> tuple[Capability, ...]:
        return tuple(
            item for item in self.inspect() if item.provider.provider_id == provider_id
        )

    def find(self, name: str) -> tuple[Capability, ...]:
        return tuple(
            self._by_id[item] for item in sorted(self._by_name.get(name, ()))
        )

    def resolve(self, name: str, *, provider_id: str | None = None) -> Capability | None:
        """Resolve one name or fail rather than choosing across providers."""

        if provider_id is not None:
            identifier = self._by_qualified_name.get(f"{provider_id}:{name}")
            return self._by_id.get(identifier) if identifier is not None else None
        identifiers = sorted(self._by_name.get(name, ()))
        if not identifiers:
            return None
        if len(identifiers) > 1:
            providers = sorted(self._by_id[item].provider.provider_id for item in identifiers)
            raise CapabilityNameCollisionError(
                f"capability name {name!r} is ambiguous across providers: {providers!r}"
            )
        return self._by_id[identifiers[0]]

    def remove(self, capability_id: str) -> Capability | None:
        capability = self._by_id.pop(capability_id, None)
        if capability is None:
            return None
        self._by_qualified_name.pop(capability.qualified_name, None)
        identifiers = self._by_name[capability.name]
        identifiers.remove(capability_id)
        if not identifiers:
            del self._by_name[capability.name]
        return capability

    def remove_provider(self, provider_id: str) -> tuple[Capability, ...]:
        removed = self.capabilities_for_provider(provider_id)
        for capability in removed:
            self.remove(capability.capability_id)
        self._providers.pop(provider_id, None)
        self._provider_health.pop(provider_id, None)
        return removed

    def inspect(self) -> tuple[Capability, ...]:
        return tuple(self._by_id[item] for item in sorted(self._by_id))

    def availability(self) -> Mapping[str, CapabilityAvailability]:
        """Return a read-only, capability-id keyed availability snapshot."""

        return MappingProxyType(
            {item.capability_id: item.availability for item in self.inspect()}
        )

    def update_availability(
        self, capability_id: str, availability: CapabilityAvailability
    ) -> Capability:
        capability = self._by_id.get(capability_id)
        if capability is None:
            raise KeyError(capability_id)
        updated = capability.with_availability(availability)
        self._by_id[capability_id] = updated
        return updated

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CAPABILITY_CONTRACT_VERSION,
            "providers": [item.to_dict() for item in self.providers()],
            "provider_health": [
                self._provider_health[item].to_dict()
                for item in sorted(self._provider_health)
            ],
            "capabilities": [item.to_dict() for item in self.inspect()],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapabilityRegistry:
        data = _closed_object(
            data,
            {"contract_version", "providers", "provider_health", "capabilities"},
            "registry",
        )
        if data.get("contract_version") != CAPABILITY_CONTRACT_VERSION:
            raise ValueError(
                f"registry.contract_version must be {CAPABILITY_CONTRACT_VERSION}"
            )
        capabilities = data.get("capabilities")
        if type(capabilities) is not list:
            raise ValueError("registry.capabilities must be a list")
        providers = data.get("providers", [])
        provider_health = data.get("provider_health", [])
        if type(providers) is not list:
            raise ValueError("registry.providers must be a list")
        if type(provider_health) is not list:
            raise ValueError("registry.provider_health must be a list")
        registry = cls()
        for item in providers:
            registry.register_provider(CapabilityProvider.from_dict(item))
        for item in capabilities:
            registry.register(Capability.from_dict(item))
        for item in provider_health:
            registry.update_provider_health(CapabilityProviderHealth.from_dict(item))
        return registry


def _closed_object(
    value: Any, allowed: set[str], path: str
) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{path} must be an ordinary object")
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{path} contains unknown fields: {sorted(unknown)!r}")
    return value


__all__ = [
    "CAPABILITY_CONTRACT_VERSION",
    "Capability",
    "CapabilityDefinition",
    "CapabilityAvailability",
    "CapabilityAvailabilityState",
    "CapabilityEffect",
    "CapabilityNameCollisionError",
    "CapabilityPermissions",
    "CapabilityProvider",
    "CapabilityProviderHealth",
    "CapabilityProviderHealthState",
    "CapabilityProviderKind",
    "CapabilityProviderLocation",
    "CapabilityRegistry",
    "CapabilityRegistryError",
    "CapabilityRisk",
    "CapabilityTimeout",
    "ProviderHealth",
    "ProviderHealthState",
]
