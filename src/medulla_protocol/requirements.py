"""Declarative connection requirements and negotiation states.

These models describe what a node asks Echo to provide. They contain no
grants, credential values, callbacks, imports, or approval policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math
from typing import Any, Iterable, Mapping

from medulla_protocol.capability import (
    CapabilityEffect,
    CapabilityPermissions,
    CapabilityRisk,
    CapabilityTimeout,
)


class EntityIdentityScope(StrEnum):
    BASIC = "identity.basic"
    PROFILE = "identity.profile"
    EMBODIMENT = "identity.embodiment"


class ConnectionNegotiationState(StrEnum):
    CONNECTED_TRANSPORT = "connected_transport"
    MANIFEST_RECEIVED = "manifest_received"
    COMPATIBLE = "compatible"
    REQUIREMENTS_RECEIVED = "requirements_received"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REQUIREMENTS_AUTHORIZED = "requirements_authorized"
    AUTHENTICATED = "authenticated"
    ACTIVE = "active"
    DECLINED = "declined"
    BLOCKED = "blocked"
    UNSATISFIED = "unsatisfied"
    DENIED_SCOPE = "denied_scope"
    AUTH_FAILED = "auth_failed"
    INCOMPATIBLE = "incompatible"


def _text(value: Any, path: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _closed(value: Any, allowed: set[str], path: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{path} must be an ordinary object")
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{path} contains unknown fields: {sorted(unknown)!r}")
    return value


def _strings(value: Iterable[str], path: str) -> tuple[str, ...]:
    if isinstance(value, str):
        raise ValueError(f"{path} must be a sequence of strings")
    result = tuple(value)
    if any(type(item) is not str or not item for item in result):
        raise ValueError(f"{path} must contain non-empty strings")
    if len(result) != len(set(result)):
        raise ValueError(f"{path} must not contain duplicates")
    return result


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
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{path} keys must be strings")
            copied[key] = _safe_json(item, f"{path}.{key}")
        return copied
    raise ValueError(
        f"{path} contains unsupported type "
        f"{type(value).__module__}.{type(value).__qualname__}"
    )


@dataclass(slots=True, frozen=True, kw_only=True)
class EntityIdentityBasic:
    """The complete payload allowed by the ``identity.basic`` scope."""

    entity_id: str
    display_name: str | None = None
    entity_type: str | None = None

    def __post_init__(self) -> None:
        _text(self.entity_id, "identity.basic.entity_id")
        if self.display_name is not None:
            _text(self.display_name, "identity.basic.display_name")
        if self.entity_type is not None:
            _text(self.entity_type, "identity.basic.entity_type")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"entity_id": self.entity_id}
        if self.display_name is not None:
            result["display_name"] = self.display_name
        if self.entity_type is not None:
            result["entity_type"] = self.entity_type
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EntityIdentityBasic:
        data = _closed(data, {"entity_id", "display_name", "entity_type"}, "identity.basic")
        return cls(
            entity_id=data.get("entity_id"),
            display_name=data.get("display_name"),
            entity_type=data.get("entity_type"),
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class EntityIdentityRequirement:
    required: bool = False
    scopes: tuple[EntityIdentityScope, ...] = ()

    def __post_init__(self) -> None:
        if type(self.required) is not bool:
            raise TypeError("connection_requirements.identity.required must be a bool")
        if isinstance(self.scopes, str):
            raise ValueError("connection_requirements.identity.scopes must be a sequence")
        scopes = tuple(EntityIdentityScope(item) for item in self.scopes)
        if len(scopes) != len(set(scopes)):
            raise ValueError("connection_requirements.identity.scopes must not contain duplicates")
        if self.required and not scopes:
            raise ValueError("required identity must declare at least one scope")
        object.__setattr__(self, "scopes", scopes)

    def to_dict(self) -> dict[str, Any]:
        return {"required": self.required, "scopes": [item.value for item in self.scopes]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EntityIdentityRequirement:
        data = _closed(data, {"required", "scopes"}, "connection_requirements.identity")
        return cls(required=data.get("required", False), scopes=data.get("scopes", ()))


@dataclass(slots=True, frozen=True, kw_only=True)
class MetadataRequirement:
    required: bool = False
    keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.required) is not bool:
            raise TypeError("connection_requirements.metadata.required must be a bool")
        object.__setattr__(self, "keys", _strings(self.keys, "connection_requirements.metadata.keys"))
        if self.required and not self.keys:
            raise ValueError("required metadata must declare at least one key")

    def to_dict(self) -> dict[str, Any]:
        return {"required": self.required, "keys": list(self.keys)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MetadataRequirement:
        data = _closed(data, {"required", "keys"}, "connection_requirements.metadata")
        return cls(required=data.get("required", False), keys=data.get("keys", ()))


@dataclass(slots=True, frozen=True, kw_only=True)
class CredentialRequirement:
    credential_id: str
    type: str
    required: bool = True

    def __post_init__(self) -> None:
        _text(self.credential_id, "connection_requirements.credentials.id")
        _text(self.type, "connection_requirements.credentials.type")
        if type(self.required) is not bool:
            raise TypeError("connection_requirements.credentials.required must be a bool")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.credential_id, "type": self.type, "required": self.required}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CredentialRequirement:
        data = _closed(data, {"id", "type", "required"}, "connection_requirements.credentials[]")
        return cls(
            credential_id=data.get("id"),
            type=data.get("type"),
            required=data.get("required", True),
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class SessionRequirement:
    required: bool = False
    features: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.required) is not bool:
            raise TypeError("connection_requirements.session.required must be a bool")
        object.__setattr__(self, "features", _strings(self.features, "connection_requirements.session.features"))
        metadata = _safe_json(self.metadata, "connection_requirements.session.metadata")
        if type(metadata) is not dict:
            raise ValueError("connection_requirements.session.metadata must be an object")
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "features": list(self.features),
            "metadata": _safe_json(self.metadata, "connection_requirements.session.metadata"),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SessionRequirement:
        data = _closed(data, {"required", "features", "metadata"}, "connection_requirements.session")
        return cls(
            required=data.get("required", False),
            features=data.get("features", ()),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class ConnectionRequirements:
    identity: EntityIdentityRequirement | None = None
    metadata: MetadataRequirement | None = None
    credentials: tuple[CredentialRequirement, ...] = ()
    permissions: tuple[str, ...] = ()
    protocol_features: tuple[str, ...] = ()
    entity_capabilities: tuple[str, ...] = ()
    session: SessionRequirement | None = None

    def __post_init__(self) -> None:
        if self.identity is not None and not isinstance(self.identity, EntityIdentityRequirement):
            raise TypeError("connection_requirements.identity is invalid")
        if self.metadata is not None and not isinstance(self.metadata, MetadataRequirement):
            raise TypeError("connection_requirements.metadata is invalid")
        if self.session is not None and not isinstance(self.session, SessionRequirement):
            raise TypeError("connection_requirements.session is invalid")
        credentials = tuple(self.credentials)
        if any(not isinstance(item, CredentialRequirement) for item in credentials):
            raise TypeError("connection_requirements.credentials contains an invalid item")
        ids = [item.credential_id for item in credentials]
        if len(ids) != len(set(ids)):
            raise ValueError("connection_requirements credential ids must not contain duplicates")
        object.__setattr__(self, "credentials", credentials)
        for name in ("permissions", "protocol_features", "entity_capabilities"):
            object.__setattr__(self, name, _strings(getattr(self, name), f"connection_requirements.{name}"))

    @property
    def empty(self) -> bool:
        return not any((
            self.identity,
            self.metadata,
            self.credentials,
            self.permissions,
            self.protocol_features,
            self.entity_capabilities,
            self.session,
        ))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "credentials": [item.to_dict() for item in self.credentials],
            "permissions": list(self.permissions),
            "protocol_features": list(self.protocol_features),
            "entity_capabilities": list(self.entity_capabilities),
        }
        if self.identity is not None:
            result["identity"] = self.identity.to_dict()
        if self.metadata is not None:
            result["metadata"] = self.metadata.to_dict()
        if self.session is not None:
            result["session"] = self.session.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ConnectionRequirements:
        data = _closed(
            data,
            {"identity", "metadata", "credentials", "permissions", "protocol_features", "entity_capabilities", "session"},
            "connection_requirements",
        )
        raw_credentials = data.get("credentials", [])
        if type(raw_credentials) is not list:
            raise ValueError("connection_requirements.credentials must be a list")
        return cls(
            identity=(EntityIdentityRequirement.from_dict(data["identity"]) if "identity" in data else None),
            metadata=(MetadataRequirement.from_dict(data["metadata"]) if "metadata" in data else None),
            credentials=tuple(CredentialRequirement.from_dict(item) for item in raw_credentials),
            permissions=data.get("permissions", ()),
            protocol_features=data.get("protocol_features", ()),
            entity_capabilities=data.get("entity_capabilities", ()),
            session=(SessionRequirement.from_dict(data["session"]) if "session" in data else None),
        )


class NodeApprovalDecision(StrEnum):
    APPROVE = "approve"
    DECLINE = "decline"
    BLOCK = "block"


class NodeApprovalMode(StrEnum):
    MANUAL = "manual"
    AUTONOMOUS = "autonomous"


@dataclass(slots=True, frozen=True)
class SecretReference:
    """A non-secret pointer to a locally resolved secret."""

    uri: str

    def __post_init__(self) -> None:
        _text(self.uri, "secret reference")
        if not self.uri.startswith("secret://") or not self.name:
            raise ValueError("secret reference must use secret://<name>")
        if any(character in self.name for character in ("/", "?", "#", "@")):
            raise ValueError("secret reference name contains forbidden characters")

    @property
    def name(self) -> str:
        return self.uri.removeprefix("secret://")

    def __str__(self) -> str:
        return self.uri


@dataclass(slots=True, frozen=True, kw_only=True)
class AuthorizedRequirements:
    """Values and grants explicitly selected by a human for one session."""

    identity_basic: EntityIdentityBasic | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    credentials: dict[str, SecretReference] = field(default_factory=dict)
    permissions: tuple[str, ...] = ()
    protocol_features: tuple[str, ...] = ()
    entity_capabilities: tuple[str, ...] = ()
    session_features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.identity_basic is not None and not isinstance(self.identity_basic, EntityIdentityBasic):
            raise TypeError("authorized identity_basic is invalid")
        metadata = _safe_json(self.metadata, "authorization.metadata")
        if type(metadata) is not dict:
            raise ValueError("authorization.metadata must be an object")
        object.__setattr__(self, "metadata", metadata)
        if type(self.credentials) is not dict:
            raise ValueError("authorization.credentials must be an object")
        credentials: dict[str, SecretReference] = {}
        for key, value in self.credentials.items():
            credential_id = _text(key, "authorization.credentials key")
            if isinstance(value, SecretReference):
                reference = value
            elif type(value) is str:
                reference = SecretReference(value)
            else:
                raise ValueError(f"authorization.credentials.{credential_id} must be a secret reference")
            credentials[credential_id] = reference
        object.__setattr__(self, "credentials", credentials)
        for name in ("permissions", "protocol_features", "entity_capabilities", "session_features"):
            object.__setattr__(self, name, _strings(getattr(self, name), f"authorization.{name}"))

    @property
    def identity_scopes(self) -> tuple[EntityIdentityScope, ...]:
        return (EntityIdentityScope.BASIC,) if self.identity_basic is not None else ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity_basic": self.identity_basic.to_dict() if self.identity_basic else None,
            "metadata": _safe_json(self.metadata, "authorization.metadata"),
            "credentials": {key: value.uri for key, value in self.credentials.items()},
            "permissions": list(self.permissions),
            "protocol_features": list(self.protocol_features),
            "entity_capabilities": list(self.entity_capabilities),
            "session_features": list(self.session_features),
        }

    def public_summary(self) -> dict[str, Any]:
        """Return an inspectable summary that never exposes credential values."""

        return {
            "identity_scopes": [item.value for item in self.identity_scopes],
            "metadata_keys": sorted(self.metadata),
            "credential_ids": sorted(self.credentials),
            "permissions": list(self.permissions),
            "protocol_features": list(self.protocol_features),
            "entity_capabilities": list(self.entity_capabilities),
            "session_features": list(self.session_features),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AuthorizedRequirements:
        data = _closed(
            data,
            {"identity_basic", "metadata", "credentials", "permissions", "protocol_features", "entity_capabilities", "session_features"},
            "authorization",
        )
        identity = data.get("identity_basic")
        return cls(
            identity_basic=(EntityIdentityBasic.from_dict(identity) if identity is not None else None),
            metadata=data.get("metadata", {}),
            credentials=data.get("credentials", {}),
            permissions=data.get("permissions", ()),
            protocol_features=data.get("protocol_features", ()),
            entity_capabilities=data.get("entity_capabilities", ()),
            session_features=data.get("session_features", ()),
        )

    def validate_for(self, requirements: ConnectionRequirements) -> tuple[ConnectionNegotiationState, str] | None:
        """Return the first fail-closed requirement error, or ``None``."""

        if not isinstance(requirements, ConnectionRequirements):
            raise TypeError("requirements must be ConnectionRequirements")
        requested_scopes = set(requirements.identity.scopes if requirements.identity else ())
        supplied_scopes = set(self.identity_scopes)
        if supplied_scopes - requested_scopes:
            return ConnectionNegotiationState.DENIED_SCOPE, "identity scope was not requested"
        if EntityIdentityScope.PROFILE in requested_scopes or EntityIdentityScope.EMBODIMENT in requested_scopes:
            return ConnectionNegotiationState.DENIED_SCOPE, "profile and embodiment payload authorization is not implemented"
        if requirements.identity and requirements.identity.required and not requested_scopes <= supplied_scopes:
            return ConnectionNegotiationState.UNSATISFIED, "required identity scope is missing"
        if requirements.metadata:
            requested = set(requirements.metadata.keys)
            supplied = set(self.metadata)
            if supplied - requested:
                return ConnectionNegotiationState.DENIED_SCOPE, "metadata key was not requested"
            if requirements.metadata.required and not requested <= supplied:
                return ConnectionNegotiationState.UNSATISFIED, "required metadata is missing"
        required_credentials = {
            item.credential_id for item in requirements.credentials if item.required
        }
        requested_credentials = {item.credential_id for item in requirements.credentials}
        if set(self.credentials) - requested_credentials:
            return ConnectionNegotiationState.DENIED_SCOPE, "credential was not requested"
        if not required_credentials <= set(self.credentials):
            return ConnectionNegotiationState.UNSATISFIED, "required credential is missing"
        if any(reference.name != credential_id for credential_id, reference in self.credentials.items()):
            return ConnectionNegotiationState.AUTH_FAILED, "credential reference does not match request id"
        for supplied, requested, label in (
            (set(self.permissions), set(requirements.permissions), "permission"),
            (set(self.protocol_features), set(requirements.protocol_features), "protocol feature"),
            (set(self.entity_capabilities), set(requirements.entity_capabilities), "Entity capability"),
            (set(self.session_features), set(requirements.session.features if requirements.session else ()), "session feature"),
        ):
            if supplied - requested:
                return ConnectionNegotiationState.DENIED_SCOPE, f"{label} was not requested"
            if requested - supplied:
                return ConnectionNegotiationState.UNSATISFIED, f"required {label} is missing"
        return None


__all__ = [
    "AuthorizedRequirements", "CapabilityEffect", "CapabilityPermissions", "CapabilityRisk", "CapabilityTimeout",
    "ConnectionNegotiationState", "ConnectionRequirements", "CredentialRequirement",
    "EntityIdentityBasic", "EntityIdentityRequirement", "EntityIdentityScope",
    "MetadataRequirement", "NodeApprovalDecision", "NodeApprovalMode", "SecretReference", "SessionRequirement",
]
