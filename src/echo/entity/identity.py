"""Stable identity data owned by Echo, never by an inference provider."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EntityIdentity:
    """A deliberately immutable snapshot of an Entity's identity core."""

    entity_id: str
    name: str
    entity_type: str
    presentation: str
    worldview: str
    core_values: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.entity_id.strip():
            raise ValueError("entity_id must not be empty")
        if not self.name.strip():
            raise ValueError("name must not be empty")
        object.__setattr__(self, "core_values", tuple(self.core_values))
