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
