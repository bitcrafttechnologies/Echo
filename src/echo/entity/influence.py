"""Explicit, bounded ways a Signal may influence Entity control state."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from echo.entity.attention import AttentionProposal


def _deltas(values: Mapping[str, float], label: str) -> Mapping[str, float]:
    copied = dict(values)
    for name, value in copied.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{label} names must not be empty")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not -1.0 <= value <= 1.0
        ):
            raise ValueError(f"{label} deltas must be between -1 and 1")
        copied[name] = float(value)
    return MappingProxyType(copied)


@dataclass(slots=True, frozen=True, kw_only=True)
class SignalInfluence:
    """A bounded control-state proposal explicitly attributed to one Signal."""

    state_deltas: Mapping[str, float] = field(default_factory=dict)
    drive_deltas: Mapping[str, float] = field(default_factory=dict)
    attention: AttentionProposal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "state_deltas",
            _deltas(self.state_deltas, "internal state"),
        )
        object.__setattr__(
            self,
            "drive_deltas",
            _deltas(self.drive_deltas, "drive"),
        )
        if self.attention is not None and not isinstance(
            self.attention, AttentionProposal
        ):
            raise ValueError("attention must be an AttentionProposal")
        if not self.state_deltas and not self.drive_deltas and self.attention is None:
            raise ValueError("signal influence must contain at least one change")
