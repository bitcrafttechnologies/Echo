"""Standalone Action and structured execution-result protocol models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import math
from typing import Any, Mapping
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any, path: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _optional_text(value: Any, path: str) -> str | None:
    return None if value is None else _text(value, path)


def _json(value: Any, path: str) -> Any:
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        return value
    if type(value) is list:
        return [_json(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if type(value) is dict:
        result: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{path} keys must be strings")
            result[key] = _json(item, f"{path}.{key}")
        return result
    raise ValueError(f"{path} contains unsupported type {type(value).__name__}")


def _timestamp(value: datetime, path: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{path} must be timezone-aware")
    return value


class NodeActionResultState(StrEnum):
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class NodeActionErrorCode(StrEnum):
    NOT_RUNNING = "not_running"
    UNKNOWN_ACTION = "unknown_action"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    INVALID_RESOURCE = "invalid_resource"
    INVALID_RESULT = "invalid_result"
    EXECUTION_FAILED = "execution_failed"
    AUTHORIZATION_REQUIRED = "authorization_required"


@dataclass(slots=True, frozen=True, kw_only=True)
class NodeAction:
    type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    resource_id: str | None = None
    correlation_id: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_now)
    entity_id: str | None = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.type, "action.type")
        _text(self.id, "action.id")
        _optional_text(self.resource_id, "action.resource_id")
        _optional_text(self.correlation_id, "action.correlation_id")
        _optional_text(self.entity_id, "action.entity_id")
        _optional_text(self.task_id, "action.task_id")
        _timestamp(self.created_at, "action.created_at")
        parameters = _json(self.parameters, "action.parameters")
        if type(parameters) is not dict:
            raise ValueError("action.parameters must be an object")
        object.__setattr__(self, "parameters", parameters)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "parameters": _json(self.parameters, "action.parameters"),
            "resource_id": self.resource_id,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at.isoformat(),
            "entity_id": self.entity_id,
            "task_id": self.task_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NodeAction:
        allowed = {"id", "type", "parameters", "resource_id", "correlation_id", "created_at", "entity_id", "task_id"}
        if type(data) is not dict or set(data) - allowed:
            raise ValueError("action must be a closed ordinary object")
        created_at = data.get("created_at")
        if type(created_at) is not str:
            raise ValueError("action.created_at must be an ISO-8601 string")
        return cls(
            id=data.get("id"), type=data.get("type"), parameters=data.get("parameters", {}),
            resource_id=data.get("resource_id"), correlation_id=data.get("correlation_id"),
            created_at=datetime.fromisoformat(created_at),
            entity_id=data.get("entity_id"), task_id=data.get("task_id"),
        )

    @classmethod
    def from_echo_dict(cls, data: Mapping[str, Any]) -> NodeAction:
        """Decode the existing Echo Action wire representation."""

        allowed = {"id", "type", "parameters", "entity_id", "task_id", "created_at"}
        if type(data) is not dict or set(data) - allowed:
            raise ValueError("Echo action must be a closed ordinary object")
        parameters = data.get("parameters", {})
        if type(parameters) is not dict:
            raise ValueError("action.parameters must be an object")
        resource_id = parameters.get("resource_id")
        if resource_id is not None and type(resource_id) is not str:
            raise ValueError("action.parameters.resource_id must be a string")
        created_at = data.get("created_at")
        if type(created_at) is not str:
            raise ValueError("action.created_at must be an ISO-8601 string")
        return cls(
            id=data.get("id"), type=data.get("type"), parameters=parameters,
            resource_id=resource_id, correlation_id=data.get("id"),
            entity_id=data.get("entity_id"), task_id=data.get("task_id"),
            created_at=datetime.fromisoformat(created_at),
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class NodeActionError:
    code: NodeActionErrorCode
    message: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", NodeActionErrorCode(self.code))
        _text(self.message, "action_result.error.message")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.message}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NodeActionError:
        if type(data) is not dict or set(data) != {"code", "message"}:
            raise ValueError("action_result.error must contain code and message")
        return cls(code=data.get("code"), message=data.get("message"))


@dataclass(slots=True, frozen=True, kw_only=True)
class NodeActionResult:
    action_id: str
    state: NodeActionResultState
    node_id: str
    provider_id: str
    adapter_id: str | None = None
    capability_id: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: NodeActionError | None = None
    completed_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        for path, value in (("action_id", self.action_id), ("node_id", self.node_id), ("provider_id", self.provider_id)):
            _text(value, f"action_result.{path}")
        _optional_text(self.adapter_id, "action_result.adapter_id")
        _optional_text(self.capability_id, "action_result.capability_id")
        object.__setattr__(self, "state", NodeActionResultState(self.state))
        _timestamp(self.completed_at, "action_result.completed_at")
        result = _json(self.result, "action_result.result")
        if type(result) is not dict:
            raise ValueError("action_result.result must be an object")
        object.__setattr__(self, "result", result)
        failed = self.state in {NodeActionResultState.REJECTED, NodeActionResultState.FAILED}
        if failed is (self.error is None):
            raise ValueError("rejected/failed results require an error; completed results forbid one")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id, "state": self.state.value,
            "node_id": self.node_id, "provider_id": self.provider_id,
            "adapter_id": self.adapter_id, "capability_id": self.capability_id,
            "result": _json(self.result, "action_result.result"),
            "error": self.error.to_dict() if self.error else None,
            "completed_at": self.completed_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NodeActionResult:
        allowed = {
            "action_id", "state", "node_id", "provider_id", "adapter_id",
            "capability_id", "result", "error", "completed_at",
        }
        if type(data) is not dict or set(data) != allowed:
            raise ValueError("action result must be a closed complete object")
        completed_at = data.get("completed_at")
        if type(completed_at) is not str:
            raise ValueError("action_result.completed_at must be an ISO-8601 string")
        error = data.get("error")
        return cls(
            action_id=data.get("action_id"), state=data.get("state"),
            node_id=data.get("node_id"), provider_id=data.get("provider_id"),
            adapter_id=data.get("adapter_id"), capability_id=data.get("capability_id"),
            result=data.get("result", {}),
            error=NodeActionError.from_dict(error) if error is not None else None,
            completed_at=datetime.fromisoformat(completed_at),
        )


Action = NodeAction
ActionError = NodeActionError
ActionResult = NodeActionResult

__all__ = [
    "Action", "ActionError", "ActionResult", "NodeAction", "NodeActionError",
    "NodeActionErrorCode", "NodeActionResult", "NodeActionResultState",
]
