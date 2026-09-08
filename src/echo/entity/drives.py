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
        if any(not 0.0 <= value <= 1.0 for value in copied.values()):
            raise ValueError("drive values must be between 0 and 1")
        object.__setattr__(self, "values", MappingProxyType(copied))
