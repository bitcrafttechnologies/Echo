"""Provider-neutral Phase 8 intention and behavior arbitration primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import json
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from echo.core.action import Action
from echo.core.scheduler import SignalPriority
from echo.entity.attention import AttentionCandidate


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value.strip()


def _unit(value: float, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= value <= 1
    ):
        raise ValueError(f"{name} must be between 0 and 1")
    return float(value)


def _json_mapping(value: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise TypeError(f"{name} must be a mapping with string keys")
    try:
        copied = json.loads(json.dumps(dict(value), allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must contain only safe JSON values") from error
    return MappingProxyType(copied)


class IntentionType(StrEnum):
    SPEAK = "speak"
    OBSERVE = "observe"
    INVESTIGATE = "investigate"
    ASK = "ask"
    WAIT = "wait"
    REMEMBER = "remember"
    MOVE = "move"
    INSPECT = "inspect"
    USE_TOOL = "use_tool"
    FOLLOW = "follow"
    IGNORE = "ignore"
    CREATE_GOAL = "create_goal"


class BehaviorOutcome(StrEnum):
    APPROVED = "approved"
    DEFERRED = "deferred"
    REJECTED = "rejected"


class CuriosityGoalStatus(StrEnum):
    ACTIVE = "active"
    DEFERRED = "deferred"


@dataclass(slots=True, frozen=True, kw_only=True)
class Intention:
    entity_id: str
    type: IntentionType
    parameters: Mapping[str, Any] = field(default_factory=dict)
    source_signal_id: str | None = None
    urgency: float = 0.5
    confidence: float = 1.0
    interruptible: bool = True
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _identifier(self.id, "intention id"))
        object.__setattr__(
            self, "entity_id", _identifier(self.entity_id, "intention entity_id")
        )
        if not isinstance(self.type, IntentionType):
            object.__setattr__(self, "type", IntentionType(self.type))
        if self.source_signal_id is not None:
            object.__setattr__(
                self,
                "source_signal_id",
                _identifier(self.source_signal_id, "source_signal_id"),
            )
        object.__setattr__(self, "urgency", _unit(self.urgency, "urgency"))
        object.__setattr__(
            self, "confidence", _unit(self.confidence, "confidence")
        )
        if not isinstance(self.interruptible, bool):
            raise TypeError("intention interruptible must be a boolean")
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise ValueError("intention created_at must be timezone-aware")
        object.__setattr__(
            self, "parameters", _json_mapping(self.parameters, "intention parameters")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "type": self.type.value,
            "parameters": dict(self.parameters),
            "source_signal_id": self.source_signal_id,
            "urgency": self.urgency,
            "confidence": self.confidence,
            "interruptible": self.interruptible,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(slots=True, frozen=True, kw_only=True)
class BehaviorContext:
    active_task: bool = False
    active_task_interruptible: bool = True
    user_attention_available: bool = True
    resources_available: bool = True
    capabilities: frozenset[str] = frozenset()
    relationship_trust: float = 0.5
    allow_external_actions: bool = False

    def __post_init__(self) -> None:
        for name in (
            "active_task",
            "active_task_interruptible",
            "user_attention_available",
            "resources_available",
            "allow_external_actions",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")
        try:
            capabilities = frozenset(self.capabilities)
        except TypeError as error:
            raise TypeError("capabilities must be iterable") from error
        if any(not isinstance(value, str) or not value for value in capabilities):
            raise ValueError("capabilities must contain non-empty strings")
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(
            self,
            "relationship_trust",
            _unit(self.relationship_trust, "relationship_trust"),
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class BehaviorDecision:
    intention: Intention
    outcome: BehaviorOutcome
    reason: str
    action: Action | None = None
    decided_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.intention, Intention):
            raise TypeError("behavior decision intention must be an Intention")
        if not isinstance(self.outcome, BehaviorOutcome):
            object.__setattr__(self, "outcome", BehaviorOutcome(self.outcome))
        object.__setattr__(
            self, "reason", _identifier(self.reason, "behavior decision reason")
        )
        if self.outcome is BehaviorOutcome.APPROVED and self.action is None:
            raise ValueError("approved behavior requires an Action intent")
        if self.outcome is not BehaviorOutcome.APPROVED and self.action is not None:
            raise ValueError("deferred or rejected behavior cannot contain an Action")
        if self.decided_at.tzinfo is None:
            raise ValueError("behavior decided_at must be timezone-aware")

    def to_dict(self) -> dict[str, Any]:
        return {
            "intention": self.intention.to_dict(),
            "outcome": self.outcome.value,
            "reason": self.reason,
            "action": self.action.to_dict() if self.action is not None else None,
            "decided_at": self.decided_at.isoformat(),
        }


@dataclass(slots=True, frozen=True, kw_only=True)
class CuriosityGoal:
    entity_id: str
    subject: str
    reason: str
    source_signal_id: str
    attention_candidate_id: str
    salience: float
    status: CuriosityGoalStatus
    priority: int = int(SignalPriority.BACKGROUND)
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        for name in (
            "id",
            "entity_id",
            "subject",
            "reason",
            "source_signal_id",
            "attention_candidate_id",
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        object.__setattr__(self, "salience", _unit(self.salience, "salience"))
        if not isinstance(self.status, CuriosityGoalStatus):
            object.__setattr__(self, "status", CuriosityGoalStatus(self.status))
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("curiosity goal priority must be an integer")
        if self.priority < int(SignalPriority.BACKGROUND):
            raise ValueError("curiosity goals must remain low priority")
        if self.created_at.tzinfo is None:
            raise ValueError("curiosity goal created_at must be timezone-aware")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "subject": self.subject,
            "reason": self.reason,
            "source_signal_id": self.source_signal_id,
            "attention_candidate_id": self.attention_candidate_id,
            "salience": self.salience,
            "status": self.status.value,
            "priority": self.priority,
            "created_at": self.created_at.isoformat(),
        }


class BehaviorPolicy:
    """Deterministic Core-owned gate between untrusted intent and Action intent."""

    _EXTERNAL = frozenset(
        {
            IntentionType.SPEAK,
            IntentionType.ASK,
            IntentionType.MOVE,
            IntentionType.USE_TOOL,
            IntentionType.FOLLOW,
        }
    )
    _CAPABILITY = {
        IntentionType.SPEAK: "speech",
        IntentionType.ASK: "speech",
        IntentionType.MOVE: "movement",
        IntentionType.FOLLOW: "movement",
        IntentionType.USE_TOOL: "tools",
        IntentionType.OBSERVE: "sensing",
        IntentionType.INSPECT: "sensing",
    }

    def __init__(self, *, minimum_confidence: float = 0.35) -> None:
        self.minimum_confidence = _unit(minimum_confidence, "minimum_confidence")

    def evaluate(
        self, intention: Intention, context: BehaviorContext
    ) -> BehaviorDecision:
        if not isinstance(intention, Intention):
            raise TypeError("behavior intention must be an Intention")
        if not isinstance(context, BehaviorContext):
            raise TypeError("behavior context must be a BehaviorContext")
        if intention.confidence < self.minimum_confidence:
            return self._decision(
                intention, BehaviorOutcome.REJECTED, "confidence below policy threshold"
            )
        capability = self._CAPABILITY.get(intention.type)
        if capability is not None and capability not in context.capabilities:
            return self._decision(
                intention, BehaviorOutcome.REJECTED, f"capability unavailable: {capability}"
            )
        if intention.type in self._EXTERNAL and not context.allow_external_actions:
            return self._decision(
                intention,
                BehaviorOutcome.REJECTED,
                "external Actions require explicit policy authorization",
            )
        if not context.resources_available:
            return self._decision(
                intention, BehaviorOutcome.DEFERRED, "required resources are unavailable"
            )
        if (
            context.active_task
            and not context.active_task_interruptible
            and intention.urgency < 0.9
        ):
            return self._decision(
                intention,
                BehaviorOutcome.DEFERRED,
                "active work is not interruptible",
            )
        if (
            intention.type in {IntentionType.SPEAK, IntentionType.ASK}
            and not context.user_attention_available
            and intention.urgency < 0.8
        ):
            return self._decision(
                intention, BehaviorOutcome.DEFERRED, "user attention is unavailable"
            )
        if intention.type is IntentionType.FOLLOW and context.relationship_trust < 0.5:
            return self._decision(
                intention, BehaviorOutcome.REJECTED, "relationship trust is insufficient"
            )
        action = Action(
            type=f"intention.{intention.type.value}",
            entity_id=intention.entity_id,
            parameters={"intention_id": intention.id, **dict(intention.parameters)},
        )
        return BehaviorDecision(
            intention=intention,
            outcome=BehaviorOutcome.APPROVED,
            reason="approved by behavior policy",
            action=action,
        )

    @staticmethod
    def _decision(
        intention: Intention, outcome: BehaviorOutcome, reason: str
    ) -> BehaviorDecision:
        return BehaviorDecision(
            intention=intention, outcome=outcome, reason=reason
        )


class BehaviorController:
    """Entity-owned bounded intention, decision, and curiosity state."""

    def __init__(
        self,
        entity_id: str,
        *,
        policy: BehaviorPolicy | None = None,
        retention: int = 100,
    ) -> None:
        self.entity_id = _identifier(entity_id, "behavior entity_id")
        if isinstance(retention, bool) or not isinstance(retention, int) or retention < 1:
            raise ValueError("behavior retention must be a positive integer")
        if policy is not None and not isinstance(policy, BehaviorPolicy):
            raise TypeError("behavior policy must be a BehaviorPolicy")
        self.policy = policy or BehaviorPolicy()
        self.retention = retention
        self._intentions: list[Intention] = []
        self._decisions: list[BehaviorDecision] = []
        self._curiosity_goals: list[CuriosityGoal] = []

    @property
    def intentions(self) -> tuple[Intention, ...]:
        return tuple(self._intentions)

    @property
    def decisions(self) -> tuple[BehaviorDecision, ...]:
        return tuple(self._decisions)

    @property
    def curiosity_goals(self) -> tuple[CuriosityGoal, ...]:
        return tuple(self._curiosity_goals)

    def arbitrate(
        self, intention: Intention, context: BehaviorContext
    ) -> BehaviorDecision:
        if not isinstance(intention, Intention):
            raise TypeError("behavior intention must be an Intention")
        if intention.entity_id != self.entity_id:
            raise ValueError("intention belongs to another Entity")
        if not isinstance(context, BehaviorContext):
            raise TypeError("behavior context must be a BehaviorContext")
        decision = self.policy.evaluate(intention, context)
        self._append(self._intentions, intention)
        self._append(self._decisions, decision)
        return decision

    def consider_curiosity(
        self,
        candidate: AttentionCandidate,
        *,
        active_noninterruptible_work: bool,
        minimum_score: float = 0.65,
    ) -> CuriosityGoal | None:
        if not isinstance(candidate, AttentionCandidate):
            raise TypeError("curiosity candidate must be an AttentionCandidate")
        if not isinstance(active_noninterruptible_work, bool):
            raise TypeError("active_noninterruptible_work must be a boolean")
        if candidate.entity_id != self.entity_id:
            raise ValueError("attention candidate belongs to another Entity")
        threshold = _unit(minimum_score, "minimum_score")
        if candidate.score < threshold:
            return None
        goal = CuriosityGoal(
            entity_id=self.entity_id,
            subject=candidate.subject,
            reason=candidate.reason,
            source_signal_id=candidate.signal_id,
            attention_candidate_id=candidate.id,
            salience=candidate.score,
            status=(
                CuriosityGoalStatus.DEFERRED
                if active_noninterruptible_work
                else CuriosityGoalStatus.ACTIVE
            ),
        )
        self._append(self._curiosity_goals, goal)
        return goal

    def copy(self) -> BehaviorController:
        copied = BehaviorController(
            self.entity_id, policy=self.policy, retention=self.retention
        )
        copied._intentions = list(self._intentions)
        copied._decisions = list(self._decisions)
        copied._curiosity_goals = list(self._curiosity_goals)
        return copied

    def to_dict(self) -> dict[str, Any]:
        return {
            "intentions": [value.to_dict() for value in self._intentions],
            "decisions": [value.to_dict() for value in self._decisions],
            "curiosity_goals": [value.to_dict() for value in self._curiosity_goals],
        }

    def _append(self, values: list[Any], value: Any) -> None:
        values.append(value)
        del values[:-self.retention]
