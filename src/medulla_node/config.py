"""Strict, declarative configuration for a standalone Medulla Node."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from medulla_node._yaml import SafeYamlError, load_yaml_mapping
from medulla_protocol import (
    CAPABILITY_CONTRACT_VERSION,
    NODE_PROTOCOL,
    Capability,
    CapabilityAvailability,
    CapabilityAvailabilityState,
    CapabilityEffect,
    CapabilityPermissions,
    CapabilityProvider,
    CapabilityProviderHealthState,
    CapabilityProviderKind,
    CapabilityProviderLocation,
    CapabilityRisk,
    CapabilityTimeout,
    ConnectionRequirements,
    MedullaNodeEndpoint,
    MedullaNodeResource,
    MedullaNodeSignal,
    MedullaNodeType,
    NodeApprovalMode,
)


DEFAULT_CONFIG_PATH = Path("medulla-node.yaml")
DEFAULT_ENDPOINT_ADDRESS = "ws://127.0.0.1:8765/medulla"
DEFAULT_IMPLEMENTATION_VERSION = "0.9"


@dataclass(slots=True, frozen=True, kw_only=True)
class EchoConnectionConfig:
    transport: str = "websocket"
    endpoint: str
    heartbeat_interval: float = 5.0
    heartbeat_timeout: float = 15.0
    reconnect_initial_delay: float = 0.1
    reconnect_max_delay: float = 5.0

    def __post_init__(self) -> None:
        if self.transport != "websocket":
            raise ValueError("echo.transport must be 'websocket'")
        _text(self.endpoint, "echo.endpoint")
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise ValueError("echo.endpoint must use ws:// or wss:// and include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("echo.endpoint must not contain credentials")
        if parsed.fragment:
            raise ValueError("echo.endpoint must not contain a fragment")
        for name in (
            "heartbeat_interval", "heartbeat_timeout",
            "reconnect_initial_delay", "reconnect_max_delay",
        ):
            value = getattr(self, name)
            if type(value) not in (int, float) or value <= 0:
                raise ValueError(f"echo.{name} must be a positive number")
            object.__setattr__(self, name, float(value))
        if self.heartbeat_timeout <= self.heartbeat_interval:
            raise ValueError("echo.heartbeat_timeout must exceed heartbeat_interval")
        if self.reconnect_max_delay < self.reconnect_initial_delay:
            raise ValueError("echo.reconnect_max_delay must be >= reconnect_initial_delay")


class NodeConfigurationError(ValueError):
    """Configuration is unreadable, unsafe, or cannot form a valid manifest."""

    def __init__(self, message: str, *, source: str | Path | None = None) -> None:
        self.source = str(source) if source is not None else None
        prefix = (
            f"Invalid Medulla Node configuration from {self.source}"
            if self.source
            else "Invalid Medulla Node configuration"
        )
        super().__init__(f"{prefix}: {message}")


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{path} must be a mapping")
    return value


def _closed(value: Any, allowed: set[str], path: str) -> dict[str, Any]:
    value = _mapping(value, path)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{path} contains unknown fields: {sorted(unknown)!r}")
    return value


def _text(value: Any, path: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _items(value: Any, path: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if type(value) is not list:
        raise ValueError(f"{path} must be a sequence")
    return [_mapping(item, f"{path}[{index}]") for index, item in enumerate(value)]


@dataclass(slots=True, frozen=True, kw_only=True)
class MedullaNodeConfig:
    node_id: str
    node_type: MedullaNodeType = MedullaNodeType.OTHER
    display_name: str | None = None
    provider_id: str | None = None
    provider_display_name: str | None = None
    transport_type: CapabilityProviderKind = CapabilityProviderKind.WEBSOCKET
    endpoint_id: str = "primary"
    endpoint_address: str = DEFAULT_ENDPOINT_ADDRESS
    transport_metadata: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(
        default_factory=lambda: {
            "implementation": "medulla-node",
            "version": DEFAULT_IMPLEMENTATION_VERSION,
        }
    )
    capabilities: tuple[Capability, ...] = ()
    signals: tuple[MedullaNodeSignal, ...] = ()
    resources: tuple[MedullaNodeResource, ...] = ()
    health_supported: bool = True
    health_state: CapabilityProviderHealthState = CapabilityProviderHealthState.UNKNOWN
    health_message: str | None = None
    health_details: dict[str, Any] = field(default_factory=dict)
    protocol: str = NODE_PROTOCOL
    echo: EchoConnectionConfig | None = None
    connection_requirements: ConnectionRequirements = field(default_factory=ConnectionRequirements)
    approval_mode: NodeApprovalMode = NodeApprovalMode.MANUAL

    def __post_init__(self) -> None:
        _text(self.node_id, "node.id")
        object.__setattr__(self, "node_type", MedullaNodeType(self.node_type))
        if self.display_name is not None:
            _text(self.display_name, "node.name")
        provider_id = self.provider_id or self.node_id
        _text(provider_id, "provider.id")
        object.__setattr__(self, "provider_id", provider_id)
        if self.provider_display_name is not None:
            _text(self.provider_display_name, "provider.name")
        object.__setattr__(self, "transport_type", CapabilityProviderKind(self.transport_type))
        _text(self.endpoint_id, "transport.id")
        _text(self.endpoint_address, "transport.address")
        if type(self.health_supported) is not bool:
            raise TypeError("health.supported must be a bool")
        object.__setattr__(self, "health_state", CapabilityProviderHealthState(self.health_state))
        if self.health_message is not None:
            _text(self.health_message, "health.message")
        # Shared protocol constructors perform the finite, ordinary-JSON check.
        MedullaNodeEndpoint(
            endpoint_id=self.endpoint_id,
            kind=self.transport_type,
            address=self.endpoint_address,
            metadata=self.transport_metadata,
        )
        from medulla_protocol import MedullaNodeIdentity
        MedullaNodeIdentity(
            node_id=self.node_id,
            type=self.node_type,
            display_name=self.display_name,
            metadata=self.metadata,
        )
        if type(self.health_details) is not dict:
            raise ValueError("health.details must be a mapping")
        for name, values, item_type in (
            ("capabilities", self.capabilities, Capability),
            ("signals", self.signals, MedullaNodeSignal),
            ("resources", self.resources, MedullaNodeResource),
        ):
            values = tuple(values)
            if any(not isinstance(item, item_type) for item in values):
                raise TypeError(f"{name} contains an invalid item")
            object.__setattr__(self, name, values)
        if self.echo is not None and not isinstance(self.echo, EchoConnectionConfig):
            raise TypeError("echo must be EchoConnectionConfig")
        if not isinstance(self.connection_requirements, ConnectionRequirements):
            raise TypeError("connection_requirements must be ConnectionRequirements")
        object.__setattr__(self, "approval_mode", NodeApprovalMode(self.approval_mode))

    @property
    def provider(self) -> CapabilityProvider:
        return CapabilityProvider(
            provider_id=self.provider_id,
            kind=self.transport_type,
            location=CapabilityProviderLocation.REMOTE,
            display_name=self.provider_display_name,
        )

    def to_dict(self) -> dict[str, Any]:
        transport: dict[str, Any] = {
            "id": self.endpoint_id,
            "type": self.transport_type.value,
            "address": self.endpoint_address,
        }
        if self.transport_metadata:
            transport["metadata"] = dict(self.transport_metadata)
        health: dict[str, Any] = {
            "supported": self.health_supported,
            "state": self.health_state.value,
            "details": dict(self.health_details),
        }
        if self.health_message is not None:
            health["message"] = self.health_message
        result = {
            "protocol": self.protocol,
            "node": {"id": self.node_id, "name": self.display_name, "type": self.node_type.value},
            "provider": {"id": self.provider_id, "name": self.provider_display_name},
            "transport": transport,
            "metadata": dict(self.metadata),
            "capabilities": [_capability_config_dict(item) for item in self.capabilities],
            "signals": [item.to_dict() for item in self.signals],
            "resources": [item.to_dict() for item in self.resources],
            "health": health,
            "connection_requirements": self.connection_requirements.to_dict(),
            "discovery": {"approval_mode": self.approval_mode.value},
        }
        if self.echo is not None:
            result["echo"] = {
                "transport": self.echo.transport,
                "endpoint": self.echo.endpoint,
                "heartbeat_interval": self.echo.heartbeat_interval,
                "heartbeat_timeout": self.echo.heartbeat_timeout,
                "reconnect_initial_delay": self.echo.reconnect_initial_delay,
                "reconnect_max_delay": self.echo.reconnect_max_delay,
            }
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MedullaNodeConfig:
        data = _closed(
            data,
            {"protocol", "node", "provider", "transport", "metadata", "capabilities", "signals", "resources", "health", "echo", "connection_requirements", "discovery"},
            "configuration",
        )
        node = _closed(data.get("node"), {"id", "name", "type"}, "node")
        provider_data = _closed(data.get("provider", {}), {"id", "name"}, "provider")
        transport = _closed(
            data.get("transport"),
            {"id", "type", "address", "metadata"},
            "transport",
        )
        metadata = _mapping(data.get("metadata", {}), "metadata")
        provider_id = provider_data.get("id", node.get("id"))
        provider = CapabilityProvider(
            provider_id=provider_id,
            kind=transport.get("type"),
            location=CapabilityProviderLocation.REMOTE,
            display_name=provider_data.get("name"),
        )
        capabilities = tuple(
            _capability(item, provider)
            for item in _items(data.get("capabilities"), "capabilities")
        )
        signals = tuple(
            MedullaNodeSignal.from_dict(item)
            for item in _items(data.get("signals"), "signals")
        )
        resources = tuple(
            MedullaNodeResource.from_dict(item)
            for item in _items(data.get("resources"), "resources")
        )
        health = _closed(
            data.get("health", {}),
            {"supported", "state", "message", "details"},
            "health",
        )
        echo_data = data.get("echo")
        echo = None
        if echo_data is not None:
            echo_data = _closed(
                echo_data,
                {"transport", "endpoint", "heartbeat_interval", "heartbeat_timeout", "reconnect_initial_delay", "reconnect_max_delay"},
                "echo",
            )
            echo = EchoConnectionConfig(
                transport=echo_data.get("transport", "websocket"),
                endpoint=echo_data.get("endpoint"),
                heartbeat_interval=echo_data.get("heartbeat_interval", 5.0),
                heartbeat_timeout=echo_data.get("heartbeat_timeout", 15.0),
                reconnect_initial_delay=echo_data.get("reconnect_initial_delay", 0.1),
                reconnect_max_delay=echo_data.get("reconnect_max_delay", 5.0),
            )
        discovery = _closed(
            data.get("discovery", {}),
            {"approval_mode"},
            "discovery",
        )
        return cls(
            node_id=node.get("id"),
            node_type=node.get("type", MedullaNodeType.OTHER.value),
            display_name=node.get("name"),
            provider_id=provider_id,
            provider_display_name=provider_data.get("name"),
            transport_type=transport.get("type"),
            endpoint_id=transport.get("id", "primary"),
            endpoint_address=transport.get("address", _default_address(transport.get("type"))),
            transport_metadata=transport.get("metadata", {}),
            metadata=metadata,
            capabilities=capabilities,
            signals=signals,
            resources=resources,
            health_supported=health.get("supported", True),
            health_state=health.get("state", CapabilityProviderHealthState.UNKNOWN.value),
            health_message=health.get("message"),
            health_details=health.get("details", {}),
            protocol=data.get("protocol", NODE_PROTOCOL),
            echo=echo,
            connection_requirements=ConnectionRequirements.from_dict(
                _mapping(data.get("connection_requirements", {}), "connection_requirements")
            ),
            approval_mode=discovery.get("approval_mode", NodeApprovalMode.MANUAL.value),
        )


def _default_address(kind: Any) -> str:
    return {
        "websocket": DEFAULT_ENDPOINT_ADDRESS,
        "mqtt": "mqtt://127.0.0.1:1883/medulla",
        "http": "http://127.0.0.1:8080/medulla",
        "serial": "serial:///dev/ttyUSB0",
    }.get(kind, f"{kind or 'unknown'}://unconfigured")


def _capability(data: dict[str, Any], provider: CapabilityProvider) -> Capability:
    data = _closed(
        data,
        {"contract_version", "id", "name", "description", "input_schema", "output_schema", "availability", "permissions", "effect", "timeout"},
        "capability",
    )
    availability = _closed(
        data.get("availability", {}),
        {"state", "message", "details"},
        "capability.availability",
    )
    permissions = _closed(
        data.get("permissions", {}),
        {"risk", "required"},
        "capability.permissions",
    )
    timeout = _closed(
        data.get("timeout", {}),
        {"expected_seconds", "maximum_seconds"},
        "capability.timeout",
    )
    return Capability(
        contract_version=data.get("contract_version", CAPABILITY_CONTRACT_VERSION),
        capability_id=data.get("id"),
        name=data.get("name"),
        description=data.get("description", data.get("name")),
        provider=provider,
        input_schema=data.get("input_schema", {}),
        output_schema=data.get("output_schema", {}),
        availability=CapabilityAvailability(
            state=availability.get("state", CapabilityAvailabilityState.UNKNOWN.value),
            message=availability.get("message"),
            details=availability.get("details", {}),
        ),
        permissions=CapabilityPermissions(
            risk=permissions.get("risk", CapabilityRisk.NONE.value),
            required=permissions.get("required", ()),
        ),
        effect=data.get("effect", CapabilityEffect.READ_ONLY.value),
        timeout=CapabilityTimeout(
            expected_seconds=timeout.get("expected_seconds", 1.0),
            maximum_seconds=timeout.get("maximum_seconds", 30.0),
        ),
    )


def _capability_config_dict(capability: Capability) -> dict[str, Any]:
    result = capability.to_dict()
    # These values are derived from the node's provider and other fields.
    result.pop("provider")
    result.pop("qualified_name")
    result.pop("state_changing")
    return result


def load_node_config(path: str | Path = DEFAULT_CONFIG_PATH) -> MedullaNodeConfig:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
        data = load_yaml_mapping(text, source=source)
        return MedullaNodeConfig.from_dict(data)
    except NodeConfigurationError:
        raise
    except (OSError, SafeYamlError, TypeError, ValueError) as error:
        raise NodeConfigurationError(str(error), source=source) from error


NodeConfig = MedullaNodeConfig

__all__ = [
    "DEFAULT_CONFIG_PATH", "DEFAULT_ENDPOINT_ADDRESS", "EchoConnectionConfig", "MedullaNodeConfig",
    "NodeConfig", "NodeConfigurationError", "load_node_config",
]
