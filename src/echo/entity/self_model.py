"""An Entity's current embodiment, capabilities, condition, and situation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SelfModel:
    """Mutable operational awareness, explicitly separate from identity."""

    entity_id: str
    embodiment_id: str | None = None
    capabilities: dict[str, bool] = field(default_factory=dict)
    condition: dict[str, Any] = field(default_factory=dict)
    location: str | None = None
    companion_ids: list[str] = field(default_factory=list)
    active_goal_ids: list[str] = field(default_factory=list)
    available_systems: set[str] = field(default_factory=set)
    recent_event_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.entity_id:
            raise ValueError("entity_id must not be empty")

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "entity_id":
            try:
                object.__getattribute__(self, "entity_id")
            except AttributeError:
                pass
            else:
                raise AttributeError("self-model entity_id is immutable")
        object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "embodiment_id": self.embodiment_id,
            "capabilities": self.capabilities.copy(),
            "condition": self.condition.copy(),
            "location": self.location,
            "companion_ids": list(self.companion_ids),
            "active_goal_ids": list(self.active_goal_ids),
            "available_systems": sorted(self.available_systems),
            "recent_event_ids": list(self.recent_event_ids),
        }
