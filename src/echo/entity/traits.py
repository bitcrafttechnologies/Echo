"""Slow-changing traits, reflection evidence, and bounded evolution policy."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math
from types import MappingProxyType
from typing import Literal, Mapping
from uuid import uuid4

from echo.entity.audit import (
    CharacterMutationAuditRecord,
    CharacterMutationDecision,
    CharacterMutationTarget,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _unit_float(name: str, value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 <= value <= 1.0
    ):
        raise ValueError(f"{name} must be a finite number between 0 and 1")
    return float(value)


def _bounded(values: Mapping[str, float]) -> Mapping[str, float]:
    copied: dict[str, float] = {}
    for name, value in dict(values).items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("trait names must be non-empty strings")
        copied[name] = _unit_float(f"trait {name!r}", value)
    return MappingProxyType(copied)


@dataclass(frozen=True, slots=True)
class TraitProfile:
    """An immutable trait snapshot controlled by the character service."""

    values: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", _bounded(self.values))

    def to_dict(self) -> dict[str, float]:
        return dict(self.values)


@dataclass(frozen=True, slots=True)
class TraitEvidence:
    """A provider- or runtime-proposed observation, never a direct mutation."""

    trait: str
    evidence_id: str
    direction: Literal["increase", "decrease"]
    strength: float
    source: str
    confidence: float = 1.0
    observed_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.trait, self.evidence_id, self.source)
        ):
            raise ValueError("trait evidence identifiers must not be empty")
        if self.direction not in ("increase", "decrease"):
            raise ValueError("trait evidence direction must be increase or decrease")
        object.__setattr__(self, "strength", _unit_float("strength", self.strength))
        object.__setattr__(
            self, "confidence", _unit_float("confidence", self.confidence)
        )
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise ValueError("trait evidence observed_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "trait": self.trait,
            "evidence_id": self.evidence_id,
            "direction": self.direction,
            "strength": self.strength,
            "source": self.source,
            "confidence": self.confidence,
            "observed_at": self.observed_at.isoformat(),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class TraitReflection:
    """A bounded batch of evidence evaluated as one character reflection."""

    trait: str
    evidence: tuple[TraitEvidence, ...]
    source: str = "reflection"
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.trait, self.source, self.id)
        ):
            raise ValueError("trait reflection identifiers must not be empty")
        if not self.evidence:
            raise ValueError("trait reflection must include evidence")
        if any(not isinstance(item, TraitEvidence) for item in self.evidence):
            raise TypeError("reflection evidence must contain TraitEvidence values")
        if any(item.trait != self.trait for item in self.evidence):
            raise ValueError("all reflection evidence must target the same trait")
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise ValueError("trait reflection created_at must be timezone-aware")


@dataclass(frozen=True, slots=True, kw_only=True)
class TraitEvolutionPolicy:
    """Core-owned guardrails for gradual trait evolution."""

    minimum_evidence: int = 2
    minimum_confidence: float = 0.75
    minimum_net_support: float = 0.5
    maximum_adjustment: float = 0.05
    maximum_contradiction_ratio: float = 0.5
    maximum_evidence_age: timedelta = timedelta(days=30)
    maximum_future_skew: timedelta = timedelta(minutes=5)
    evidence_history_size: int = 256
    audit_history_size: int = 256
    maximum_evidence_per_reflection: int = 32
    mutable_traits: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.minimum_evidence < 2:
            raise ValueError("minimum_evidence must be at least 2")
        for name in (
            "minimum_confidence",
            "minimum_net_support",
            "maximum_adjustment",
            "maximum_contradiction_ratio",
        ):
            _unit_float(name, getattr(self, name))
        if self.maximum_adjustment <= 0.0:
            raise ValueError("maximum_adjustment must be greater than zero")
        if self.maximum_evidence_age <= timedelta(0):
            raise ValueError("maximum_evidence_age must be positive")
        if self.maximum_future_skew < timedelta(0):
            raise ValueError("maximum_future_skew must not be negative")
        if (
            self.evidence_history_size < 1
            or self.audit_history_size < 1
            or self.maximum_evidence_per_reflection < self.minimum_evidence
        ):
            raise ValueError("trait history sizes must be positive")
        if self.mutable_traits is not None:
            mutable = frozenset(self.mutable_traits)
            if any(not isinstance(name, str) or not name.strip() for name in mutable):
                raise ValueError("mutable trait names must be non-empty strings")
            object.__setattr__(self, "mutable_traits", mutable)


@dataclass(frozen=True, slots=True)
class TraitEvolutionDecision:
    """Terminal result of evaluating one reflection."""

    reflection_id: str
    trait: str
    decision: CharacterMutationDecision
    reason: str
    before: float | None
    after: float | None
    adjustment: float
    evidence_ids: tuple[str, ...]
    audit_id: str

    @property
    def accepted(self) -> bool:
        return self.decision is CharacterMutationDecision.ACCEPTED

    def to_dict(self) -> dict[str, object]:
        return {
            "reflection_id": self.reflection_id,
            "trait": self.trait,
            "decision": self.decision.value,
            "reason": self.reason,
            "before": self.before,
            "after": self.after,
            "adjustment": self.adjustment,
            "evidence_ids": list(self.evidence_ids),
            "audit_id": self.audit_id,
        }


class TraitEvolutionService:
    """Own trait updates so inference can provide evidence but cannot assign values."""

    def __init__(
        self,
        entity_id: str,
        profile: TraitProfile,
        *,
        policy: TraitEvolutionPolicy | None = None,
    ) -> None:
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise ValueError("entity_id must not be empty")
        if not isinstance(profile, TraitProfile):
            raise TypeError("profile must be a TraitProfile")
        self._entity_id = entity_id
        self._profile = profile
        self._policy = policy or TraitEvolutionPolicy()
        self._evidence: deque[TraitEvidence] = deque(
            maxlen=self._policy.evidence_history_size
        )
        self._seen_evidence_ids: set[str] = set()
        self._audit: deque[CharacterMutationAuditRecord] = deque(
            maxlen=self._policy.audit_history_size
        )

    @property
    def entity_id(self) -> str:
        return self._entity_id

    @property
    def profile(self) -> TraitProfile:
        return self._profile

    @property
    def policy(self) -> TraitEvolutionPolicy:
        return self._policy

    @property
    def evidence(self) -> tuple[TraitEvidence, ...]:
        return tuple(self._evidence)

    @property
    def audit_records(self) -> tuple[CharacterMutationAuditRecord, ...]:
        return tuple(self._audit)

    def consider(
        self,
        reflection: TraitReflection,
        *,
        now: datetime | None = None,
    ) -> TraitEvolutionDecision:
        """Evaluate evidence and apply at most one policy-bounded adjustment."""

        if not isinstance(reflection, TraitReflection):
            raise TypeError("reflection must be a TraitReflection")
        evaluated_at = now or _utc_now()
        if not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        evidence_ids = tuple(item.evidence_id for item in reflection.evidence)
        duplicate_ids = len(evidence_ids) != len(set(evidence_ids))
        reused_ids = set(evidence_ids).intersection(self._seen_evidence_ids)
        for item in reflection.evidence:
            if item.evidence_id not in self._seen_evidence_ids:
                self._remember_evidence(item)

        before = self._profile.values.get(reflection.trait)
        rejection: str | None = None
        if before is None:
            rejection = "trait is not present in the authored profile"
        elif (
            self._policy.mutable_traits is not None
            and reflection.trait not in self._policy.mutable_traits
        ):
            rejection = "trait is protected by the evolution policy"
        elif duplicate_ids:
            rejection = "reflection contains duplicate evidence identifiers"
        elif len(reflection.evidence) > self._policy.maximum_evidence_per_reflection:
            rejection = "reflection exceeds the evidence batch limit"
        elif reused_ids:
            rejection = "evidence has already been evaluated"
        elif any(
            evaluated_at - item.observed_at > self._policy.maximum_evidence_age
            for item in reflection.evidence
        ):
            rejection = "reflection contains stale evidence"
        elif any(
            item.observed_at - evaluated_at > self._policy.maximum_future_skew
            for item in reflection.evidence
        ):
            rejection = "reflection contains evidence dated too far in the future"

        increasing = tuple(
            item for item in reflection.evidence if item.direction == "increase"
        )
        decreasing = tuple(
            item for item in reflection.evidence if item.direction == "decrease"
        )
        increase_support = sum(item.strength * item.confidence for item in increasing)
        decrease_support = sum(item.strength * item.confidence for item in decreasing)
        dominant = increasing if increase_support > decrease_support else decreasing
        dominant_support = max(increase_support, decrease_support)
        opposing_support = min(increase_support, decrease_support)
        direction = 1.0 if increase_support > decrease_support else -1.0

        if rejection is None and increase_support == decrease_support:
            rejection = "reflection evidence has no dominant direction"
        if rejection is None and len(dominant) < self._policy.minimum_evidence:
            rejection = "reflection lacks repeated evidence in one direction"
        average_confidence = (
            sum(item.confidence for item in dominant) / len(dominant)
            if dominant
            else 0.0
        )
        if rejection is None and average_confidence < self._policy.minimum_confidence:
            rejection = "dominant evidence confidence is below policy threshold"
        contradiction_ratio = (
            opposing_support / dominant_support if dominant_support else 1.0
        )
        if (
            rejection is None
            and contradiction_ratio >= self._policy.maximum_contradiction_ratio
        ):
            rejection = "contradictory evidence exceeds policy threshold"
        net_support = (
            (dominant_support - opposing_support) / len(dominant)
            if dominant
            else 0.0
        )
        if rejection is None and net_support < self._policy.minimum_net_support:
            rejection = "net evidence support is below policy threshold"

        if rejection is not None:
            return self._record_decision(
                reflection,
                CharacterMutationDecision.REJECTED,
                rejection,
                before,
                before,
                0.0,
                evidence_ids,
            )

        requested_adjustment = direction * min(
            self._policy.maximum_adjustment,
            self._policy.maximum_adjustment * net_support,
        )
        assert before is not None
        after = max(0.0, min(1.0, before + requested_adjustment))
        adjustment = after - before
        if adjustment == 0.0:
            return self._record_decision(
                reflection,
                CharacterMutationDecision.REJECTED,
                "trait is already at its policy boundary",
                before,
                before,
                0.0,
                evidence_ids,
            )

        values = self._profile.to_dict()
        values[reflection.trait] = after
        self._profile = TraitProfile(values)
        return self._record_decision(
            reflection,
            CharacterMutationDecision.ACCEPTED,
            "repeated, recent evidence satisfied trait evolution policy",
            before,
            after,
            adjustment,
            evidence_ids,
        )

    def copy(self) -> TraitEvolutionService:
        """Copy evolving character state for an Entity reconstruction."""

        copied = TraitEvolutionService(self.entity_id, self.profile, policy=self.policy)
        copied._evidence.extend(self._evidence)
        copied._seen_evidence_ids.update(self._seen_evidence_ids)
        copied._audit.extend(self._audit)
        return copied

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
            "audit": [item.to_dict() for item in self.audit_records],
            "policy": {
                "minimum_evidence": self.policy.minimum_evidence,
                "minimum_confidence": self.policy.minimum_confidence,
                "minimum_net_support": self.policy.minimum_net_support,
                "maximum_adjustment": self.policy.maximum_adjustment,
                "maximum_contradiction_ratio": self.policy.maximum_contradiction_ratio,
                "maximum_evidence_age_seconds": (
                    self.policy.maximum_evidence_age.total_seconds()
                ),
                "maximum_evidence_per_reflection": (
                    self.policy.maximum_evidence_per_reflection
                ),
                "mutable_traits": (
                    sorted(self.policy.mutable_traits)
                    if self.policy.mutable_traits is not None
                    else None
                ),
            },
        }

    def _remember_evidence(self, evidence: TraitEvidence) -> None:
        if len(self._evidence) == self._evidence.maxlen:
            evicted = self._evidence[0]
            self._seen_evidence_ids.discard(evicted.evidence_id)
        self._evidence.append(evidence)
        self._seen_evidence_ids.add(evidence.evidence_id)

    def _record_decision(
        self,
        reflection: TraitReflection,
        decision: CharacterMutationDecision,
        reason: str,
        before: float | None,
        after: float | None,
        adjustment: float,
        evidence_ids: tuple[str, ...],
    ) -> TraitEvolutionDecision:
        record = CharacterMutationAuditRecord(
            entity_id=self.entity_id,
            target=CharacterMutationTarget.TRAITS,
            decision=decision,
            source=reflection.source,
            reason=reason,
            evidence_ids=evidence_ids,
            before={} if before is None else {reflection.trait: before},
            after={} if after is None else {reflection.trait: after},
            metadata={
                "reflection_id": reflection.id,
                "adjustment": adjustment,
                "evidence_count": len(evidence_ids),
            },
        )
        self._audit.append(record)
        return TraitEvolutionDecision(
            reflection_id=reflection.id,
            trait=reflection.trait,
            decision=decision,
            reason=reason,
            before=before,
            after=after,
            adjustment=adjustment,
            evidence_ids=evidence_ids,
            audit_id=record.id,
        )
