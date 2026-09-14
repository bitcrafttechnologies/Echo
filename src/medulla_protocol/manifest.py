"""Versioned, data-only manifest contract for standalone Medulla Nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math
from typing import Any, Iterable, Mapping

from medulla_protocol.capability import (
    Capability,
    CapabilityProvider,
    CapabilityProviderHealthState,
    CapabilityProviderKind,
    CapabilityProviderLocation,
)
from medulla_protocol.messages import WireMessage, WireMessageType, make_wire_message
from medulla_protocol.requirements import ConnectionRequirements
from medulla_protocol.version import NODE_PROTOCOL, NODE_PROTOCOL_NAME, NODE_PROTOCOL_VERSION

MAX_NODE_MANIFEST_ITEMS = 256


class NodeProtocolError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MedullaNodeType(StrEnum):
    EMBEDDED = "embedded"
    EDGE = "edge"
    CONTROLLER = "controller"
    SENSOR = "sensor"
    ROBOT = "robot"
    COMPUTER = "computer"
    SERVICE = "service"
    OTHER = "other"


def _required_string(value: Any, path: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _safe_json(value: Any, path: str) -> Any:
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
    raise ValueError(f"{path} contains unsupported type {type(value).__module__}.{type(value).__qualname__}")


def _closed_object(value: Any, allowed: set[str], path: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{path} must be an ordinary object")
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{path} contains unknown fields: {sorted(unknown)!r}")
    return value


def _items(value: Any, path: str) -> list[Any]:
    if type(value) is not list:
        raise ValueError(f"{path} must be a list")
    if len(value) > MAX_NODE_MANIFEST_ITEMS:
        raise ValueError(f"{path} must contain at most {MAX_NODE_MANIFEST_ITEMS} items")
    return value


def _string_tuple(value: Iterable[str], path: str) -> tuple[str, ...]:
    if isinstance(value, str):
        raise ValueError(f"{path} must be a sequence of strings")
    result = tuple(value)
    if any(type(item) is not str or not item for item in result):
        raise ValueError(f"{path} must contain non-empty strings")
    if len(result) != len(set(result)):
        raise ValueError(f"{path} must not contain duplicates")
    return result


@dataclass(slots=True, frozen=True, kw_only=True)
class MedullaNodeIdentity:
    node_id: str
    type: MedullaNodeType
    display_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_string(self.node_id, "node.id")
        object.__setattr__(self, "type", MedullaNodeType(self.type))
        if self.display_name is not None:
            _required_string(self.display_name, "node.display_name")
        metadata = _safe_json(self.metadata, "node.metadata")
        if not isinstance(metadata, dict):
            raise ValueError("node.metadata must be an object")
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        result = {"id": self.node_id, "type": self.type.value, "metadata": _safe_json(self.metadata, "node.metadata")}
        if self.display_name is not None:
            result["display_name"] = self.display_name
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MedullaNodeIdentity:
        data = _closed_object(data, {"id", "type", "display_name", "metadata"}, "node")
        return cls(node_id=data.get("id"), type=data.get("type"), display_name=data.get("display_name"), metadata=data.get("metadata", {}))


@dataclass(slots=True, frozen=True, kw_only=True)
class MedullaNodeEndpoint:
    endpoint_id: str
    kind: CapabilityProviderKind
    address: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_string(self.endpoint_id, "endpoint.id")
        object.__setattr__(self, "kind", CapabilityProviderKind(self.kind))
        _required_string(self.address, "endpoint.address")
        metadata = _safe_json(self.metadata, "endpoint.metadata")
        if not isinstance(metadata, dict):
            raise ValueError("endpoint.metadata must be an object")
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.endpoint_id, "kind": self.kind.value, "address": self.address, "metadata": _safe_json(self.metadata, "endpoint.metadata")}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MedullaNodeEndpoint:
        data = _closed_object(data, {"id", "kind", "address", "metadata"}, "endpoint")
        return cls(endpoint_id=data.get("id"), kind=data.get("kind"), address=data.get("address"), metadata=data.get("metadata", {}))


@dataclass(slots=True, frozen=True, kw_only=True)
class MedullaNodeSignal:
    name: str
    schema: str | dict[str, Any]
    adapter: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        _required_string(self.name, "signal.name")
        if type(self.schema) is str:
            _required_string(self.schema, "signal.schema")
        else:
            schema = _safe_json(self.schema, "signal.schema")
            if not isinstance(schema, dict):
                raise ValueError("signal.schema must be a string or object")
            object.__setattr__(self, "schema", schema)
        for name, value in (("adapter", self.adapter), ("description", self.description)):
            if value is not None:
                _required_string(value, f"signal.{name}")

    @property
    def signal_type(self) -> str:
        return self.name

    def to_dict(self) -> dict[str, Any]:
        schema = self.schema if isinstance(self.schema, str) else _safe_json(self.schema, "signal.schema")
        result: dict[str, Any] = {"name": self.name, "schema": schema}
        if self.adapter is not None:
            result["adapter"] = self.adapter
        if self.description is not None:
            result["description"] = self.description
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MedullaNodeSignal:
        data = _closed_object(data, {"name", "schema", "adapter", "description"}, "signal")
        return cls(name=data.get("name"), schema=data.get("schema"), adapter=data.get("adapter"), description=data.get("description"))


@dataclass(slots=True, frozen=True, kw_only=True)
class MedullaNodeResource:
    resource_id: str
    description: str
    schema: str | dict[str, Any]
    media_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_string(self.resource_id, "resource.id")
        _required_string(self.description, "resource.description")
        if type(self.schema) is str:
            _required_string(self.schema, "resource.schema")
        else:
            schema = _safe_json(self.schema, "resource.schema")
            if not isinstance(schema, dict):
                raise ValueError("resource.schema must be a string or object")
            object.__setattr__(self, "schema", schema)
        if self.media_type is not None:
            _required_string(self.media_type, "resource.media_type")
        metadata = _safe_json(self.metadata, "resource.metadata")
        if not isinstance(metadata, dict):
            raise ValueError("resource.metadata must be an object")
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        schema = self.schema if isinstance(self.schema, str) else _safe_json(self.schema, "resource.schema")
        result: dict[str, Any] = {"id": self.resource_id, "description": self.description, "schema": schema, "metadata": _safe_json(self.metadata, "resource.metadata")}
        if self.media_type is not None:
            result["media_type"] = self.media_type
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MedullaNodeResource:
        data = _closed_object(data, {"id", "description", "schema", "media_type", "metadata"}, "resource")
        return cls(resource_id=data.get("id"), description=data.get("description"), schema=data.get("schema"), media_type=data.get("media_type"), metadata=data.get("metadata", {}))


@dataclass(slots=True, frozen=True, kw_only=True)
class MedullaNodeHealth:
    state: CapabilityProviderHealthState
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", CapabilityProviderHealthState(self.state))
        if self.message is not None:
            _required_string(self.message, "health.message")
        details = _safe_json(self.details, "health.details")
        if not isinstance(details, dict):
            raise ValueError("health.details must be an object")
        object.__setattr__(self, "details", details)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"state": self.state.value, "details": _safe_json(self.details, "health.details")}
        if self.message is not None:
            result["message"] = self.message
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MedullaNodeHealth:
        data = _closed_object(data, {"state", "message", "details"}, "health")
        return cls(state=data.get("state"), message=data.get("message"), details=data.get("details", {}))


@dataclass(slots=True, frozen=True, kw_only=True)
class MedullaNodeSecurity:
    pairing_required: bool = True
    authentication_methods: tuple[str, ...] = ()
    pairing: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.pairing_required) is not bool:
            raise TypeError("security.pairing_required must be a bool")
        object.__setattr__(self, "authentication_methods", _string_tuple(self.authentication_methods, "security.authentication_methods"))
        pairing = _safe_json(self.pairing, "security.pairing")
        if not isinstance(pairing, dict):
            raise ValueError("security.pairing must be an object")
        object.__setattr__(self, "pairing", pairing)

    def to_dict(self) -> dict[str, Any]:
        return {"pairing_required": self.pairing_required, "authentication_methods": list(self.authentication_methods), "pairing": _safe_json(self.pairing, "security.pairing")}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MedullaNodeSecurity:
        data = _closed_object(data, {"pairing_required", "authentication_methods", "pairing"}, "security")
        return cls(pairing_required=data.get("pairing_required", True), authentication_methods=data.get("authentication_methods", ()), pairing=data.get("pairing", {}))


@dataclass(slots=True, frozen=True, kw_only=True)
class MedullaNodeManifest:
    node: MedullaNodeIdentity
    provider: CapabilityProvider
    endpoints: tuple[MedullaNodeEndpoint, ...]
    capabilities: tuple[Capability, ...] = ()
    signals: tuple[MedullaNodeSignal, ...] = ()
    resources: tuple[MedullaNodeResource, ...] = ()
    health: MedullaNodeHealth = field(default_factory=lambda: MedullaNodeHealth(state=CapabilityProviderHealthState.UNKNOWN))
    security: MedullaNodeSecurity = field(default_factory=MedullaNodeSecurity)
    connection_requirements: ConnectionRequirements = field(default_factory=ConnectionRequirements)
    protocol: str = NODE_PROTOCOL

    def __post_init__(self) -> None:
        if self.protocol != NODE_PROTOCOL:
            code = "unsupported_version" if isinstance(self.protocol, str) and self.protocol.startswith(f"{NODE_PROTOCOL_NAME}/") else "unsupported_protocol"
            raise NodeProtocolError(code, f"unsupported node protocol: {self.protocol!r}")
        if not isinstance(self.node, MedullaNodeIdentity) or not isinstance(self.provider, CapabilityProvider):
            raise TypeError("node and provider must use Medulla protocol types")
        if self.provider.provider_id != self.node.node_id:
            raise ValueError("provider.id must match node.id")
        if self.provider.location is not CapabilityProviderLocation.REMOTE:
            raise ValueError("node provider must be remote")
        for field_name, item_type in (("endpoints", MedullaNodeEndpoint), ("capabilities", Capability), ("signals", MedullaNodeSignal), ("resources", MedullaNodeResource)):
            values = tuple(getattr(self, field_name))
            if len(values) > MAX_NODE_MANIFEST_ITEMS:
                raise ValueError(f"{field_name} must contain at most {MAX_NODE_MANIFEST_ITEMS} items")
            if any(not isinstance(item, item_type) for item in values):
                raise TypeError(f"{field_name} contains an invalid item")
            object.__setattr__(self, field_name, values)
        if not self.endpoints:
            raise ValueError("endpoints must not be empty")
        if self.provider.kind not in {item.kind for item in self.endpoints}:
            raise ValueError("provider kind must have a matching transport endpoint")
        for values, label in (([x.endpoint_id for x in self.endpoints], "endpoint ids"), ([x.name for x in self.signals], "signal names"), ([x.resource_id for x in self.resources], "resource ids"), ([x.capability_id for x in self.capabilities], "capability ids"), ([x.name for x in self.capabilities], "capability names")):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must not contain duplicates")
        if any(item.provider != self.provider for item in self.capabilities):
            raise ValueError("every capability must be owned by the node provider")
        if not isinstance(self.health, MedullaNodeHealth) or not isinstance(self.security, MedullaNodeSecurity):
            raise TypeError("health and security must use Medulla protocol types")
        if not isinstance(self.connection_requirements, ConnectionRequirements):
            raise TypeError("connection_requirements must use the Medulla protocol type")

    def to_dict(self) -> dict[str, Any]:
        return {"protocol": self.protocol, "node": self.node.to_dict(), "provider": self.provider.to_dict(), "endpoints": [x.to_dict() for x in self.endpoints], "capabilities": [x.to_dict() for x in self.capabilities], "signals": [x.to_dict() for x in self.signals], "resources": [x.to_dict() for x in self.resources], "health": self.health.to_dict(), "security": self.security.to_dict(), "connection_requirements": self.connection_requirements.to_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MedullaNodeManifest:
        try:
            data = _closed_object(data, {"protocol", "node", "provider", "endpoints", "capabilities", "signals", "resources", "health", "security", "connection_requirements"}, "manifest")
            protocol = data.get("protocol")
            if protocol != NODE_PROTOCOL:
                code = "unsupported_version" if isinstance(protocol, str) and protocol.startswith(f"{NODE_PROTOCOL_NAME}/") else "unsupported_protocol"
                raise NodeProtocolError(code, f"unsupported node protocol: {protocol!r}")
            return cls(protocol=protocol, node=MedullaNodeIdentity.from_dict(data.get("node")), provider=CapabilityProvider.from_dict(data.get("provider")), endpoints=tuple(MedullaNodeEndpoint.from_dict(x) for x in _items(data.get("endpoints"), "manifest.endpoints")), capabilities=tuple(Capability.from_dict(x) for x in _items(data.get("capabilities", []), "manifest.capabilities")), signals=tuple(MedullaNodeSignal.from_dict(x) for x in _items(data.get("signals", []), "manifest.signals")), resources=tuple(MedullaNodeResource.from_dict(x) for x in _items(data.get("resources", []), "manifest.resources")), health=MedullaNodeHealth.from_dict(data.get("health")), security=MedullaNodeSecurity.from_dict(data.get("security")), connection_requirements=ConnectionRequirements.from_dict(data.get("connection_requirements", {})))
        except NodeProtocolError:
            raise
        except (TypeError, ValueError) as error:
            raise NodeProtocolError("invalid_manifest", str(error)) from error


def node_manifest_message(manifest: MedullaNodeManifest, *, message_id: str | None = None) -> WireMessage:
    if not isinstance(manifest, MedullaNodeManifest):
        raise TypeError("manifest must be MedullaNodeManifest")
    return make_wire_message(WireMessageType.MANIFEST, {"manifest": manifest.to_dict()}, message_id=message_id)


def node_manifest_from_message(message: WireMessage) -> MedullaNodeManifest:
    if not isinstance(message, WireMessage):
        raise TypeError("message must be WireMessage")
    if message.type is not WireMessageType.MANIFEST:
        raise NodeProtocolError("unexpected_message_type", "message is not a manifest")
    if set(message.payload) != {"manifest"}:
        raise NodeProtocolError("invalid_manifest", "manifest payload must contain only 'manifest'")
    return MedullaNodeManifest.from_dict(message.payload["manifest"])


NodeEndpoint = MedullaNodeEndpoint
NodeHealth = MedullaNodeHealth
NodeIdentity = MedullaNodeIdentity
NodeManifest = MedullaNodeManifest
NodeResource = MedullaNodeResource
NodeSecurity = MedullaNodeSecurity
NodeSignal = MedullaNodeSignal
NodeType = MedullaNodeType

__all__ = [
    "MAX_NODE_MANIFEST_ITEMS", "NODE_PROTOCOL", "NODE_PROTOCOL_NAME", "NODE_PROTOCOL_VERSION",
    "MedullaNodeEndpoint", "MedullaNodeHealth", "MedullaNodeIdentity", "MedullaNodeManifest",
    "MedullaNodeResource", "MedullaNodeSecurity", "MedullaNodeSignal", "MedullaNodeType",
    "NodeEndpoint", "NodeHealth", "NodeIdentity", "NodeManifest", "NodeProtocolError", "NodeResource",
    "NodeSecurity", "NodeSignal", "NodeType", "node_manifest_from_message", "node_manifest_message",
]
