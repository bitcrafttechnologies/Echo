"""Helpers for producing JSON-safe runtime inspection data."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
from enum import Enum
import math
from typing import Any


def json_safe(value: Any) -> Any:
    """Convert runtime values into detached, JSON-safe structures."""

    return _json_safe(value, set())


def _json_safe(value: Any, seen: set[int]) -> Any:
    if isinstance(value, Enum):
        return _json_safe(value.value, seen)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"type": "bytes", "hex": value.hex()}

    value_id = id(value)
    if isinstance(value, Mapping):
        if value_id in seen:
            return {"type": "cycle"}
        seen.add(value_id)
        try:
            return {
                str(key): _json_safe(item, seen)
                for key, item in value.items()
            }
        finally:
            seen.remove(value_id)
    if isinstance(value, (list, tuple, set, frozenset)):
        if value_id in seen:
            return [{"type": "cycle"}]
        seen.add(value_id)
        try:
            return [_json_safe(item, seen) for item in value]
        finally:
            seen.remove(value_id)

    try:
        representation = repr(value)
    except Exception:
        representation = "<unavailable>"
    return {
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
        "representation": representation,
    }
