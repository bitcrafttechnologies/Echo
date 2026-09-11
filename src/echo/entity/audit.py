"""Vocabulary for auditable character mutation decisions."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        copied = deepcopy(dict(value))
    except Exception:
        copied = dict(value)
    return MappingProxyType(copied)


class CharacterMutationTarget(StrEnum):
    IDENTITY = "identity"
    TRAITS = "traits"
    SELF_MODEL = "self_model"


class CharacterMutationDecision(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(slots=True, kw_only=True, frozen=True)
class CharacterMutationAuditRecord:
    """Structured, immutable record of a character mutation decision."""

    entity_id: str
    target: CharacterMutationTarget
    decision: CharacterMutationDecision
    source: str
    reason: str
    evidence_ids: tuple[str, ...] = ()
    before: Mapping[str, Any] = field(default_factory=dict)
    after: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.target, str):
            object.__setattr__(self, "target", CharacterMutationTarget(self.target))
        if isinstance(self.decision, str):
            object.__setattr__(
                self,
                "decision",
                CharacterMutationDecision(self.decision),
            )
        if not self.entity_id or not self.source or not self.reason:
            raise ValueError("mutation audit identifiers and reason must not be empty")
        if self.timestamp.tzinfo is None:
            raise ValueError("mutation audit timestamp must be timezone-aware")
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        object.__setattr__(self, "before", _immutable_mapping(self.before))
        object.__setattr__(self, "after", _immutable_mapping(self.after))
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "target": self.target.value,
            "decision": self.decision.value,
            "source": self.source,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
            "before": dict(self.before),
            "after": dict(self.after),
            "metadata": dict(self.metadata),
            "timestamp": self.timestamp.isoformat(),
        }
