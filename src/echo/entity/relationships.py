"""Per-person relationship state kept separate from global personality."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RelationshipState:
    subject_id: str
    familiarity: float = 0.0
    trust: float = 0.0
    interaction_count: int = 0
    communication_preferences: dict[str, Any] = field(default_factory=dict)
    known_interests: set[str] = field(default_factory=set)
    boundaries: set[str] = field(default_factory=set)
    important_memory_ids: list[str] = field(default_factory=list)
    current_context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.subject_id:
            raise ValueError("subject_id must not be empty")
        if not 0.0 <= self.familiarity <= 1.0 or not 0.0 <= self.trust <= 1.0:
            raise ValueError("relationship scores must be between 0 and 1")


@dataclass(slots=True)
class RelationshipStore:
    """In-memory base container; durable storage is a later phase concern."""

    relationships: dict[str, RelationshipState] = field(default_factory=dict)

    def get_or_create(self, subject_id: str) -> RelationshipState:
        if subject_id not in self.relationships:
            self.relationships[subject_id] = RelationshipState(subject_id)
        return self.relationships[subject_id]
