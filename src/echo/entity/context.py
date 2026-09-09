"""Compact, provider-neutral Character context assembly."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import json
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from echo.core.inspection import json_safe
from echo.entity.memory import MemoryKind, MemoryRecord
from echo.entity.state_store import StateCategory

if TYPE_CHECKING:
    from echo.core.entity import Entity


_TOKEN = re.compile(r"[a-z0-9_]+")


def _frozen(values: Mapping[str, Any]) -> Mapping[str, Any]:
    safe = json_safe(values)
    assert isinstance(safe, dict)
    return MappingProxyType(safe)


def _tokens(value: Any) -> frozenset[str]:
    try:
        text = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return frozenset(_TOKEN.findall(text.lower()))


@dataclass(slots=True, kw_only=True, frozen=True)
class CharacterContextRequest:
    """Situation and hard size bounds used for relevant retrieval."""

    situation: str
    subject_id: str | None = None
    environment: Mapping[str, Any] = field(default_factory=dict)
    max_memories: int = 8
    max_attention: int = 5
    max_state_items: int = 24

    def __post_init__(self) -> None:
        if not isinstance(self.situation, str) or not self.situation.strip():
            raise ValueError("context situation must not be empty")
        if self.subject_id is not None and (
            not isinstance(self.subject_id, str) or not self.subject_id.strip()
        ):
            raise ValueError("context subject_id must not be empty")
        if not isinstance(self.environment, Mapping):
            raise TypeError("context environment must be a mapping")
        for name in ("max_memories", "max_attention", "max_state_items"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        object.__setattr__(self, "situation", self.situation.strip())
        if self.subject_id is not None:
            object.__setattr__(self, "subject_id", self.subject_id.strip())
        object.__setattr__(self, "environment", _frozen(self.environment))


@dataclass(slots=True, kw_only=True, frozen=True)
class CharacterContext:
    """Detached cognition context with no provider-specific representation."""

    entity_id: str
    situation: str
    identity: Mapping[str, Any]
    traits: Mapping[str, float]
    state: Mapping[str, Mapping[str, Any]]
    internal_state: Mapping[str, float]
    drives: Mapping[str, Mapping[str, float]]
    attention: tuple[Mapping[str, Any], ...]
    relationships: tuple[Mapping[str, Any], ...]
    memories: tuple[Mapping[str, Any], ...]
    goals: tuple[str, ...]
    self_model: Mapping[str, Any]
    environment: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "situation": self.situation,
            "identity": deepcopy(dict(self.identity)),
            "traits": dict(self.traits),
            "state": {
                category: deepcopy(dict(values))
                for category, values in self.state.items()
            },
            "internal_state": dict(self.internal_state),
            "drives": {
                name: dict(values) for name, values in self.drives.items()
            },
            "attention": [deepcopy(dict(value)) for value in self.attention],
            "relationships": [deepcopy(dict(value)) for value in self.relationships],
            "memories": [deepcopy(dict(value)) for value in self.memories],
            "goals": list(self.goals),
            "self_model": deepcopy(dict(self.self_model)),
            "environment": deepcopy(dict(self.environment)),
        }


class CharacterContextBuilder:
    """Select compact character evidence relevant to one cognition request."""

    def build(
        self,
        entity: Entity,
        request: CharacterContextRequest,
    ) -> CharacterContext:
        from echo.core.entity import Entity

        if not isinstance(entity, Entity):
            raise TypeError("context entity must be an Entity")
        if not isinstance(request, CharacterContextRequest):
            raise TypeError("context request must be a CharacterContextRequest")

        terms = _tokens(
            {
                "situation": request.situation,
                "subject_id": request.subject_id,
                "environment": request.environment,
            }
        )
        state = self._select_state(entity, terms, request.max_state_items)
        relationships = self._select_relationships(
            entity, terms, request.subject_id
        )
        memories = self._select_memories(
            entity.memories,
            terms,
            request.subject_id,
            request.max_memories,
        )
        attention = sorted(
            entity.attention_candidates,
            key=lambda value: (
                -len(terms & _tokens(value.to_dict())),
                -value.score,
                -value.created_at.timestamp(),
                value.id,
            ),
        )[: request.max_attention]
        self_model = entity.self_model.to_dict()
        environment = self._select_mapping(
            request.environment, terms, request.max_state_items
        )
        return CharacterContext(
            entity_id=entity.id,
            situation=request.situation,
            identity=_frozen(entity.identity.to_dict()),
            traits=MappingProxyType(entity.traits.to_dict()),
            state=MappingProxyType(
                {
                    category: _frozen(values)
                    for category, values in state.items()
                }
            ),
            internal_state=MappingProxyType(entity.internal_state.to_dict()),
            drives=MappingProxyType(
                {
                    "baselines": MappingProxyType(entity.drives.to_dict()),
                    "activations": MappingProxyType(dict(entity.drive_activations)),
                }
            ),
            attention=tuple(_frozen(value.to_dict()) for value in attention),
            relationships=tuple(_frozen(value) for value in relationships),
            memories=tuple(_frozen(record.to_dict()) for record in memories),
            goals=tuple(self_model["active_goal_ids"]),
            self_model=_frozen(self_model),
            environment=_frozen(environment),
        )

    def _select_state(
        self,
        entity: Entity,
        terms: frozenset[str],
        limit: int,
    ) -> dict[str, dict[str, Any]]:
        ranked: list[tuple[int, str, str, Any]] = []
        for category in StateCategory:
            for key, value in entity.list_state(category=category).items():
                ranked.append(
                    (
                        len(terms & _tokens({key: value})),
                        category.value,
                        key,
                        value,
                    )
                )
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        selected = {category.value: {} for category in StateCategory}
        for _score, category, key, value in ranked[:limit]:
            selected[category][key] = json_safe(value)
        return selected

    def _select_relationships(
        self,
        entity: Entity,
        terms: frozenset[str],
        subject_id: str | None,
    ) -> tuple[dict[str, Any], ...]:
        ranked: list[tuple[int, float, str, dict[str, Any]]] = []
        for relationship in entity.relationships:
            value = relationship.to_dict()
            exact = 100 if relationship.subject_id == subject_id else 0
            relevance = len(terms & _tokens(value))
            ranked.append(
                (exact + relevance, relationship.familiarity, relationship.subject_id, value)
            )
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return tuple(value for _score, _familiarity, _subject, value in ranked[:3])

    def _select_memories(
        self,
        records: tuple[MemoryRecord, ...],
        terms: frozenset[str],
        subject_id: str | None,
        limit: int,
    ) -> tuple[MemoryRecord, ...]:
        ranked: list[tuple[int, float, str, MemoryRecord]] = []
        for record in records:
            value = record.to_dict()
            relevance = len(terms & _tokens(value))
            if subject_id is not None and record.metadata.get("subject_id") == subject_id:
                relevance += 100
            if record.kind is MemoryKind.WORKING:
                relevance += 1
            ranked.append((relevance, record.created_at.timestamp(), record.id, record))
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return tuple(record for _score, _created, _id, record in ranked[:limit])

    def _select_mapping(
        self,
        values: Mapping[str, Any],
        terms: frozenset[str],
        limit: int,
    ) -> dict[str, Any]:
        ranked = [
            (len(terms & _tokens({key: value})), key, value)
            for key, value in values.items()
        ]
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return {
            key: json_safe(value) for _score, key, value in ranked[:limit]
        }
