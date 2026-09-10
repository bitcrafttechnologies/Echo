"""Persistent motivational signals that influence, but do not force, behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class DriveProfile:
    """Immutable baseline drive strengths; activation belongs to runtime policy."""

    values: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        copied = dict(self.values)
        for name, value in copied.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("drive names must not be empty")
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0.0 <= value <= 1.0
            ):
                raise ValueError("drive values must be between 0 and 1")
            copied[name] = float(value)
        object.__setattr__(self, "values", MappingProxyType(copied))

    def to_dict(self) -> dict[str, float]:
        return dict(self.values)
