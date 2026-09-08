"""Rapidly changing, non-biological control state for an Entity."""

from __future__ import annotations

from dataclasses import dataclass


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

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")

    def adjust(self, **deltas: float) -> None:
        """Apply named deltas while keeping every control variable normalized."""

        for name, delta in deltas.items():
            if name not in self.__dataclass_fields__:
                raise KeyError(f"unknown internal state dimension: {name}")
            setattr(self, name, _clamp(getattr(self, name) + delta))
