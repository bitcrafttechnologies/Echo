"""Slow-changing character traits and evidence proposed for later review."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping


def _bounded(values: Mapping[str, float]) -> Mapping[str, float]:
    copied = dict(values)
    invalid = {name: value for name, value in copied.items() if not 0.0 <= value <= 1.0}
    if invalid:
        raise ValueError(f"trait values must be between 0 and 1: {invalid}")
    return MappingProxyType(copied)


@dataclass(frozen=True, slots=True)
class TraitProfile:
    """An immutable trait snapshot; updates require a future character service."""

    values: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", _bounded(self.values))

    def to_dict(self) -> dict[str, float]:
        return dict(self.values)


@dataclass(frozen=True, slots=True)
class TraitEvidence:
    """A model- or runtime-proposed observation, not a direct trait mutation."""

    trait: str
    evidence_id: str
    direction: Literal["increase", "decrease"]
    strength: float
    source: str

    def __post_init__(self) -> None:
        if not self.trait or not self.evidence_id or not self.source:
            raise ValueError("trait evidence identifiers must not be empty")
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError("trait evidence strength must be between 0 and 1")
