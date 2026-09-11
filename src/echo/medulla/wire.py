"""Versioned, non-executable JSON messages for Medulla transports."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import math
from typing import Any, Mapping
from uuid import uuid4

from echo.core.action import Action
from echo.core.signal import Signal
from echo.medulla.transport import (
    external_signal_from_dict,
    validate_outbound_action,
)


WIRE_PROTOCOL = "echo.medulla"
WIRE_VERSION = 1
DEFAULT_MAX_MESSAGE_BYTES = 1_048_576


class WireMessageType(StrEnum):
    SIGNAL = "signal"
    ACTION = "action"
    RESULT = "result"
    STATUS = "status"
    HELLO = "hello"
    MANIFEST = "manifest"
    HEARTBEAT = "heartbeat"
    ERROR = "error"


class WireProtocolError(ValueError):
    """A safely reportable wire-envelope or payload error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        message_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message_id = message_id


def _required_string(value: Any, path: str) -> str:
    if type(value) is not str or not value:
        raise WireProtocolError("invalid_payload", f"{path} must be a non-empty string")
    return value


def _safe_json(value: Any, path: str = "payload") -> Any:
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise WireProtocolError("invalid_payload", f"{path} must be finite")
        return value
    if type(value) is list:
        return [_safe_json(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if type(value) is dict:
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise WireProtocolError("invalid_payload", f"{path} keys must be strings")
            copied[key] = _safe_json(item, f"{path}.{key}")
        return copied
    raise WireProtocolError(
        "invalid_payload",
        f"{path} contains unsupported type {type(value).__module__}.{type(value).__qualname__}",
    )


@dataclass(slots=True, frozen=True, kw_only=True)
class WireMessage:
    type: WireMessageType
    payload: dict[str, Any]
    id: str
    version: int = WIRE_VERSION
    protocol: str = WIRE_PROTOCOL

    def __post_init__(self) -> None:
        if self.protocol != WIRE_PROTOCOL:
            raise WireProtocolError("unsupported_protocol", "unsupported wire protocol")
        if type(self.version) is not int or self.version != WIRE_VERSION:
            raise WireProtocolError(
                "unsupported_version", f"unsupported wire version: {self.version!r}"
            )
        if not isinstance(self.type, WireMessageType):
            raise WireProtocolError("unknown_message_type", "unknown wire message type")
        _required_string(self.id, "message.id")
        payload = _safe_json(self.payload)
        if type(payload) is not dict:
            raise WireProtocolError("invalid_payload", "message.payload must be an object")
        object.__setattr__(self, "payload", payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "version": self.version,
            "type": self.type.value,
            "id": self.id,
            "payload": _safe_json(self.payload),
        }


def make_wire_message(
    message_type: WireMessageType,
    payload: Mapping[str, Any],
    *,
    message_id: str | None = None,
) -> WireMessage:
    if type(payload) is not dict:
        raise WireProtocolError("invalid_payload", "message.payload must be an ordinary object")
    return WireMessage(
        type=message_type,
        payload=dict(payload),
        id=message_id or str(uuid4()),
    )


def signal_message(signal: Signal, *, message_id: str | None = None) -> WireMessage:
    safe_signal = external_signal_from_dict(signal.to_dict())
    return make_wire_message(
        WireMessageType.SIGNAL,
        {"signal": safe_signal.to_dict()},
        message_id=message_id,
    )


def action_message(action: Action, *, message_id: str | None = None) -> WireMessage:
    safe_action = validate_outbound_action(action)
    return make_wire_message(
        WireMessageType.ACTION,
        {"action": safe_action.to_dict()},
        message_id=message_id,
    )


def signal_from_message(message: WireMessage) -> Signal:
    if message.type is not WireMessageType.SIGNAL:
        raise WireProtocolError(
            "unexpected_message_type",
            "message is not a signal",
            message_id=message.id,
        )
    if set(message.payload) != {"signal"}:
        raise WireProtocolError(
            "invalid_payload",
            "signal payload must contain only 'signal'",
            message_id=message.id,
        )
    signal = message.payload["signal"]
    if type(signal) is not dict:
        raise WireProtocolError(
            "invalid_payload",
            "payload.signal must be an object",
            message_id=message.id,
        )
    try:
        return external_signal_from_dict(signal)
    except ValueError as error:
        raise WireProtocolError(
            "invalid_signal", str(error), message_id=message.id
        ) from error


def _reject_constant(value: str) -> None:
    raise WireProtocolError("invalid_json", f"non-finite JSON number is forbidden: {value}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WireProtocolError("invalid_json", f"duplicate object key: {key}")
        result[key] = value
    return result


def decode_wire_message(
    data: str | bytes,
    *,
    max_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
) -> WireMessage:
    """Decode one JSON object without imports, pickle, or object construction hooks."""

    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    if type(data) is bytes:
        if len(data) > max_bytes:
            raise WireProtocolError("message_too_large", "wire message exceeds size limit")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise WireProtocolError("invalid_encoding", "wire message must be UTF-8") from error
    elif type(data) is str:
        try:
            encoded_size = len(data.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise WireProtocolError(
                "invalid_encoding", "wire message must contain valid Unicode"
            ) from error
        if encoded_size > max_bytes:
            raise WireProtocolError("message_too_large", "wire message exceeds size limit")
        text = data
    else:
        raise WireProtocolError("invalid_encoding", "wire message must be text or UTF-8 bytes")

    try:
        decoded = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_object_without_duplicates,
        )
    except WireProtocolError:
        raise
    except (json.JSONDecodeError, UnicodeError) as error:
        raise WireProtocolError("invalid_json", "wire message must be valid JSON") from error
    if type(decoded) is not dict:
        raise WireProtocolError("invalid_envelope", "wire message must be an object")
    allowed = {"protocol", "version", "type", "id", "payload"}
    unknown = set(decoded) - allowed
    missing = allowed - set(decoded)
    if unknown:
        raise WireProtocolError("invalid_envelope", f"unknown envelope fields: {sorted(unknown)!r}")
    if missing:
        raise WireProtocolError("invalid_envelope", f"missing envelope fields: {sorted(missing)!r}")
    if decoded["protocol"] != WIRE_PROTOCOL:
        raise WireProtocolError("unsupported_protocol", "unsupported wire protocol")
    if type(decoded["version"]) is not int or decoded["version"] != WIRE_VERSION:
        raise WireProtocolError(
            "unsupported_version", f"unsupported wire version: {decoded['version']!r}"
        )
    try:
        message_type = WireMessageType(decoded["type"])
    except (TypeError, ValueError) as error:
        raise WireProtocolError(
            "unknown_message_type", f"unknown wire message type: {decoded['type']!r}"
        ) from error
    message_id = _required_string(decoded["id"], "message.id")
    if type(decoded["payload"]) is not dict:
        raise WireProtocolError(
            "invalid_payload", "message.payload must be an object", message_id=message_id
        )
    return WireMessage(
        protocol=decoded["protocol"],
        version=decoded["version"],
        type=message_type,
        id=message_id,
        payload=decoded["payload"],
    )


def encode_wire_message(message: WireMessage) -> str:
    if not isinstance(message, WireMessage):
        raise TypeError("message must be a WireMessage")
    return json.dumps(
        message.to_dict(),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
