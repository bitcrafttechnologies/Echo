"""Typed, provider-independent memory owned by an Echo Entity."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar
from uuid import uuid4


class MemoryKind(StrEnum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PREFERENCE = "preference"
    RELATIONSHIP = "relationship"


@dataclass(slots=True, kw_only=True, frozen=True)
class MemoryImportance:
    """Normalized retention evidence evaluated by Echo, not a provider."""

    novelty: float = 0.0
    surprise: float = 0.0
    state_significance: float = 0.0
    relationship_significance: float = 0.0
    goal_relevance: float = 0.0
    future_usefulness: float = 0.0
    repetition: float = 0.0
    unresolved_importance: float = 0.0

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= float(value) <= 1
            ):
                raise ValueError(f"{name} must be between 0 and 1")

    @property
    def score(self) -> float:
        weights = {
            "novelty": 0.15,
            "surprise": 0.15,
            "state_significance": 0.12,
            "relationship_significance": 0.12,
            "goal_relevance": 0.12,
            "future_usefulness": 0.12,
            "repetition": 0.10,
            "unresolved_importance": 0.12,
        }
        return sum(float(getattr(self, name)) * weight for name, weight in weights.items())

    def to_dict(self) -> dict[str, float]:
        return {
            name: float(getattr(self, name))
            for name in self.__dataclass_fields__
        } | {"score": self.score}


@dataclass(slots=True, kw_only=True, frozen=True)
class MemoryRetentionDecision:
    record_id: str
    retained: bool
    score: float
    threshold: float
    reason: str
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "retained": self.retained,
            "score": self.score,
            "threshold": self.threshold,
            "reason": self.reason,
            "decided_at": self.decided_at.isoformat(),
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class MemoryConsolidationProposal:
    key: str
    target_kind: MemoryKind
    content: Mapping[str, Any]
    evidence_ids: tuple[str, ...]
    confidence: float
    subject_id: str | None = None

    def __post_init__(self) -> None:
        try:
            target = MemoryKind(self.target_kind)
        except (TypeError, ValueError) as error:
            raise ValueError("target_kind must be a memory kind") from error
        if target not in {
            MemoryKind.SEMANTIC,
            MemoryKind.PREFERENCE,
            MemoryKind.RELATIONSHIP,
        }:
            raise ValueError(
                "consolidation target must be semantic, preference, or relationship"
            )
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("consolidation key must not be empty")
        if not isinstance(self.content, Mapping) or not self.content:
            raise ValueError("consolidation content must not be empty")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("consolidation confidence must be between 0 and 1")
        if not isinstance(self.evidence_ids, tuple) or not all(
            isinstance(item, str) and item for item in self.evidence_ids
        ):
            raise ValueError("evidence_ids must be a tuple of non-empty strings")
        if self.subject_id is not None and (
            not isinstance(self.subject_id, str) or not self.subject_id.strip()
        ):
            raise ValueError("subject_id must be a non-empty string when provided")
        object.__setattr__(self, "target_kind", target)
        object.__setattr__(self, "content", _frozen(self.content))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))


@dataclass(slots=True, kw_only=True, frozen=True)
class MemoryConsolidationDecision:
    proposal_key: str
    target_kind: MemoryKind
    accepted: bool
    reason: str
    evidence_ids: tuple[str, ...]
    record_id: str | None = None
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_key": self.proposal_key,
            "target_kind": self.target_kind.value,
            "accepted": self.accepted,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
            "record_id": self.record_id,
            "decided_at": self.decided_at.isoformat(),
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _frozen(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(deepcopy(dict(value)))


@dataclass(slots=True, kw_only=True, frozen=True)
class CharacterMemoryRecord:
    """A retained memory value; inference providers never own this object."""

    content: Mapping[str, Any]
    source: str
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    kind: ClassVar[MemoryKind]

    def __post_init__(self) -> None:
        if not self.id or not self.source:
            raise ValueError("memory id and source must not be empty")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("memory created_at must be timezone-aware")
        object.__setattr__(self, "content", _frozen(self.content))
        object.__setattr__(self, "metadata", _frozen(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "content": deepcopy(dict(self.content)),
            "source": self.source,
            "created_at": self.created_at.isoformat(),
            "metadata": deepcopy(dict(self.metadata)),
        }


class WorkingMemory(CharacterMemoryRecord):
    kind = MemoryKind.WORKING


class EpisodicMemory(CharacterMemoryRecord):
    kind = MemoryKind.EPISODIC


class SemanticMemory(CharacterMemoryRecord):
    kind = MemoryKind.SEMANTIC


class PreferenceMemory(CharacterMemoryRecord):
    kind = MemoryKind.PREFERENCE


class RelationshipMemory(CharacterMemoryRecord):
    kind = MemoryKind.RELATIONSHIP


MemoryRecord = (
    WorkingMemory
    | EpisodicMemory
    | SemanticMemory
    | PreferenceMemory
    | RelationshipMemory
)
_MEMORY_RECORD_TYPES = (
    WorkingMemory,
    EpisodicMemory,
    SemanticMemory,
    PreferenceMemory,
    RelationshipMemory,
)


class CharacterMemory:
    """Small in-memory typed store establishing Entity ownership in Phase 5."""

    def __init__(
        self,
        records: tuple[MemoryRecord, ...] = (),
        *,
        max_records_per_kind: int = 100,
    ) -> None:
        if (
            isinstance(max_records_per_kind, bool)
            or not isinstance(max_records_per_kind, int)
            or max_records_per_kind < 1
        ):
            raise ValueError("max_records_per_kind must be a positive integer")
        self._max_records_per_kind = max_records_per_kind
        self._records = {
            kind: deque(maxlen=max_records_per_kind) for kind in MemoryKind
        }
        self._retention_audit: deque[MemoryRetentionDecision] = deque(
            maxlen=max_records_per_kind
        )
        self._consolidation_audit: deque[MemoryConsolidationDecision] = deque(
            maxlen=max_records_per_kind
        )
        for record in records:
            self.add(record)

    def add(self, record: MemoryRecord) -> None:
        if not isinstance(record, _MEMORY_RECORD_TYPES):
            raise TypeError("record must be a typed CharacterMemoryRecord")
        self._records[record.kind].append(record)

    @property
    def retention_audit(self) -> tuple[MemoryRetentionDecision, ...]:
        return tuple(reversed(self._retention_audit))

    @property
    def consolidation_audit(self) -> tuple[MemoryConsolidationDecision, ...]:
        return tuple(reversed(self._consolidation_audit))

    def consider(
        self,
        record: MemoryRecord,
        importance: MemoryImportance,
        *,
        threshold: float = 0.5,
    ) -> MemoryRetentionDecision:
        """Retain a candidate only when its explicit importance clears policy."""

        if not isinstance(record, _MEMORY_RECORD_TYPES):
            raise TypeError("record must be a typed CharacterMemoryRecord")
        if not isinstance(importance, MemoryImportance):
            raise TypeError("importance must be a MemoryImportance")
        if isinstance(threshold, bool) or not 0 <= threshold <= 1:
            raise ValueError("retention threshold must be between 0 and 1")
        retained = importance.score >= threshold
        if retained:
            self.add(record)
        decision = MemoryRetentionDecision(
            record_id=record.id,
            retained=retained,
            score=importance.score,
            threshold=float(threshold),
            reason=(
                "importance score met retention threshold"
                if retained
                else "importance score was below retention threshold"
            ),
        )
        self._retention_audit.append(decision)
        return decision

    def consolidate(
        self,
        proposal: MemoryConsolidationProposal,
        *,
        minimum_confidence: float = 0.65,
    ) -> MemoryConsolidationDecision:
        """Validate repeated evidence, scope, confidence, and contradictions."""

        if not isinstance(proposal, MemoryConsolidationProposal):
            raise TypeError("proposal must be a MemoryConsolidationProposal")
        if isinstance(minimum_confidence, bool) or not 0 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be between 0 and 1")
        evidence_ids = tuple(dict.fromkeys(proposal.evidence_ids))
        episodic_ids = {record.id for record in self._records[MemoryKind.EPISODIC]}
        reason: str | None = None
        if len(evidence_ids) < 2:
            reason = "at least two distinct evidence records are required"
        elif any(item not in episodic_ids for item in evidence_ids):
            reason = "all evidence must reference retained episodic memories"
        elif proposal.confidence < minimum_confidence:
            reason = "proposal confidence is below the consolidation threshold"
        elif (
            proposal.target_kind is MemoryKind.RELATIONSHIP
            and not proposal.subject_id
        ):
            reason = "relationship consolidation requires a subject scope"
        elif (
            proposal.target_kind is not MemoryKind.RELATIONSHIP
            and proposal.subject_id is not None
        ):
            reason = "subject scope is only valid for relationship consolidation"
        else:
            for existing in self._records[proposal.target_kind]:
                if (
                    existing.metadata.get("consolidation_key") == proposal.key
                    and existing.metadata.get("subject_id") == proposal.subject_id
                    and dict(existing.content) != dict(proposal.content)
                ):
                    reason = "proposal contradicts retained consolidated memory"
                    break

        record: MemoryRecord | None = None
        if reason is None:
            record_type = {
                MemoryKind.SEMANTIC: SemanticMemory,
                MemoryKind.PREFERENCE: PreferenceMemory,
                MemoryKind.RELATIONSHIP: RelationshipMemory,
            }[proposal.target_kind]
            metadata: dict[str, Any] = {
                "consolidation_key": proposal.key,
                "evidence_ids": evidence_ids,
                "confidence": proposal.confidence,
            }
            if proposal.subject_id is not None:
                metadata["subject_id"] = proposal.subject_id
            record = record_type(
                content=proposal.content,
                source="echo.consolidation",
                metadata=metadata,
            )
            self.add(record)
            reason = "proposal passed evidence, confidence, scope, and contradiction checks"

        decision = MemoryConsolidationDecision(
            proposal_key=proposal.key,
            target_kind=proposal.target_kind,
            accepted=record is not None,
            reason=reason,
            evidence_ids=evidence_ids,
            record_id=record.id if record else None,
        )
        self._consolidation_audit.append(decision)
        return decision

    def list(self, kind: MemoryKind | str | None = None) -> tuple[MemoryRecord, ...]:
        if kind is not None:
            return tuple(reversed(self._records[MemoryKind(kind)]))
        return tuple(
            record
            for memory_kind in MemoryKind
            for record in reversed(self._records[memory_kind])
        )

    def copy(self) -> CharacterMemory:
        copied = CharacterMemory(
            tuple(record for kind in MemoryKind for record in self._records[kind]),
            max_records_per_kind=self._max_records_per_kind,
        )
        copied._retention_audit.extend(self._retention_audit)
        copied._consolidation_audit.extend(self._consolidation_audit)
        return copied

    def audit_to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "retention": [item.to_dict() for item in self.retention_audit],
            "consolidation": [item.to_dict() for item in self.consolidation_audit],
        }

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            kind.value: [record.to_dict() for record in reversed(self._records[kind])]
            for kind in MemoryKind
        }
