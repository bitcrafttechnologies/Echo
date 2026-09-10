"""Rapidly changing, non-biological control state for an Entity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(slots=True)
class InternalState:
    """Normalized control variables from which descriptions may be derived."""

    valence: float = 0.5
    arousal: float = 0.5
    confidence: float = 0.5
    frustration: float = 0.0
    fatigue: float = 0.0
    curiosity: float = 0.5
    stress: float = 0.0
    social_satisfaction: float = 0.5
    attention_load: float = 0.0
    urgency: float = 0.0
    resource_pressure: float = 0.0

    DIMENSIONS: ClassVar[tuple[str, ...]] = (
        "valence",
        "arousal",
        "confidence",
        "frustration",
        "fatigue",
        "curiosity",
        "stress",
        "social_satisfaction",
        "attention_load",
        "urgency",
        "resource_pressure",
    )

    def __post_init__(self) -> None:
        for name in self.DIMENSIONS:
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0.0 <= value <= 1.0
            ):
                raise ValueError(f"{name} must be between 0 and 1")
            setattr(self, name, float(value))

    def adjust(self, **deltas: float) -> None:
        """Apply named deltas while keeping every control variable normalized."""

        for name, delta in deltas.items():
            if name not in self.DIMENSIONS:
                raise KeyError(f"unknown internal state dimension: {name}")
            if (
                isinstance(delta, bool)
                or not isinstance(delta, (int, float))
                or not -1.0 <= delta <= 1.0
            ):
                raise ValueError("internal state deltas must be between -1 and 1")
        for name, delta in deltas.items():
            setattr(self, name, _clamp(getattr(self, name) + delta))

    def to_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in self.DIMENSIONS}
