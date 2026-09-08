"""Per-person relationship state kept separate from global personality."""

from __future__ import annotations

from copy import deepcopy
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
        if (
            isinstance(self.interaction_count, bool)
            or not isinstance(self.interaction_count, int)
            or self.interaction_count < 0
        ):
            raise ValueError("interaction_count must be a non-negative integer")
        self.communication_preferences = deepcopy(self.communication_preferences)
        self.known_interests = set(self.known_interests)
        self.boundaries = set(self.boundaries)
        self.important_memory_ids = list(self.important_memory_ids)
        self.current_context = deepcopy(self.current_context)

    def to_dict(self) -> dict[str, Any]:
        """Return a detached, JSON-safe social-state snapshot."""

        return {
            "subject_id": self.subject_id,
            "familiarity": self.familiarity,
            "trust": self.trust,
            "interaction_count": self.interaction_count,
            "communication_preferences": deepcopy(self.communication_preferences),
            "known_interests": sorted(self.known_interests),
            "boundaries": sorted(self.boundaries),
            "important_memory_ids": list(self.important_memory_ids),
            "current_context": deepcopy(self.current_context),
        }

    def snapshot(self) -> RelationshipState:
        """Return a detached relationship value for public inspection."""

        return RelationshipState(**self.to_dict())


@dataclass(slots=True)
class RelationshipStore:
    """In-memory base container; durable storage is a later phase concern."""

    relationships: dict[str, RelationshipState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.relationships, dict) or any(
            not isinstance(relationship, RelationshipState)
            for relationship in self.relationships.values()
        ):
            raise ValueError(
                "relationships must map subject IDs to RelationshipState"
            )
        self.relationships = {
            subject_id: relationship.snapshot()
            for subject_id, relationship in self.relationships.items()
        }
        if any(
            subject_id != value.subject_id
            for subject_id, value in self.relationships.items()
        ):
            raise ValueError("relationship keys must match subject IDs")

    def get_or_create(self, subject_id: str) -> RelationshipState:
        if subject_id not in self.relationships:
            self.relationships[subject_id] = RelationshipState(subject_id)
        return self.relationships[subject_id]

    def get(self, subject_id: str) -> RelationshipState | None:
        value = self.relationships.get(subject_id)
        return value.snapshot() if value is not None else None

    def list(self) -> tuple[RelationshipState, ...]:
        return tuple(
            self.relationships[subject_id].snapshot()
            for subject_id in sorted(self.relationships)
        )

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {
            relationship.subject_id: relationship.to_dict()
            for relationship in self.list()
        }
