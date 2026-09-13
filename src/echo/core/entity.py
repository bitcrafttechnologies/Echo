"""The Entity public abstraction."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, MutableMapping
from types import MappingProxyType
import json
from typing import TYPE_CHECKING, Any

from echo.core.action import Action
from echo.core.handlers import Handler, HandlerRegistry, SignalKey
from echo.core.scheduler import SignalPriority
from echo.core.signal import Signal
from echo.core.task import Task
from echo.entity.attention import AttentionCandidate
from echo.entity.behavior import (
    BehaviorContext,
    BehaviorController,
    BehaviorDecision,
    CuriosityGoalStatus,
    Intention,
)
from echo.entity.drives import DriveProfile
from echo.entity.identity import EntityIdentity
from echo.entity.influence import SignalInfluence
from echo.entity.memory import (
    CharacterMemory,
    MemoryConsolidationDecision,
    MemoryConsolidationProposal,
    MemoryImportance,
    MemoryKind,
    MemoryRecord,
    MemoryRetentionDecision,
)
from echo.entity.memory_store import (
    DurableMemoryRecord,
    DurableMemoryStatus,
    DurableMemoryType,
    MemoryCandidate,
    MemoryCommitDecision,
    MemoryService,
    MemorySourceType,
)
from echo.entity.relationships import RelationshipState, RelationshipStore
from echo.entity.self_model import SelfModel
from echo.entity.state import InternalState
from echo.entity.state_store import (
    EntityStateSnapshot,
    InMemoryStateStore,
    StateCategory,
    StateStore,
    StateView,
)
from echo.entity.traits import (
    TraitEvolutionDecision,
    TraitEvolutionService,
    TraitProfile,
    TraitReflection,
)

if TYPE_CHECKING:
    from echo.core.runtime import Runtime


class Entity:
    """A persistent actor's identity, state, and registered handlers."""

    def __init__(
        self,
        entity_id: str,
        *,
        state: dict[str, Any] | None = None,
        identity: EntityIdentity | None = None,
        traits: TraitProfile | None = None,
        trait_evolution: TraitEvolutionService | None = None,
        self_model: SelfModel | None = None,
        internal_state: InternalState | None = None,
        drives: DriveProfile | None = None,
        drive_activations: Mapping[str, float] | None = None,
        relationships: RelationshipStore | None = None,
        memory: CharacterMemory | None = None,
        memory_service: MemoryService | None = None,
        state_store: StateStore | None = None,
        behavior: BehaviorController | None = None,
    ) -> None:
        if not entity_id:
            raise ValueError("entity id must not be empty")
        identity = identity or EntityIdentity(
            entity_id=entity_id,
            name=entity_id,
            entity_type="entity",
            presentation="unspecified",
            worldview="",
            core_values=(),
        )
        self_model = self_model or SelfModel(entity_id=entity_id)
        if identity.entity_id != entity_id:
            raise ValueError("identity entity_id must match Entity id")
        if self_model.entity_id != entity_id:
            raise ValueError("self-model entity_id must match Entity id")
        if traits is not None and not isinstance(traits, TraitProfile):
            raise TypeError("traits must be a TraitProfile")
        if trait_evolution is not None and not isinstance(
            trait_evolution, TraitEvolutionService
        ):
            raise TypeError("trait_evolution must be a TraitEvolutionService")
        if trait_evolution is not None and trait_evolution.entity_id != entity_id:
            raise ValueError("trait_evolution entity_id must match Entity id")
        if (
            trait_evolution is not None
            and traits is not None
            and trait_evolution.profile != traits
        ):
            raise ValueError("traits must match the trait evolution profile")
        if internal_state is not None and not isinstance(
            internal_state, InternalState
        ):
            raise ValueError("internal_state must be an InternalState")
        if drives is not None and not isinstance(drives, DriveProfile):
            raise ValueError("drives must be a DriveProfile")
        if drive_activations is not None and not isinstance(
            drive_activations, Mapping
        ):
            raise ValueError("drive_activations must be a mapping")
        if relationships is not None and not isinstance(
            relationships, RelationshipStore
        ):
            raise ValueError("relationships must be a RelationshipStore")
        if memory is not None and not isinstance(memory, CharacterMemory):
            raise ValueError("memory must be a CharacterMemory")
        if memory_service is not None and not isinstance(memory_service, MemoryService):
            raise ValueError("memory_service must be a MemoryService")
        if memory_service is not None and memory_service.entity_id != entity_id:
            raise ValueError("memory_service entity_id must match Entity id")
        if behavior is not None and not isinstance(behavior, BehaviorController):
            raise TypeError("behavior must be a BehaviorController")
        if behavior is not None and behavior.entity_id != entity_id:
            raise ValueError("behavior entity_id must match Entity id")
        self._id = entity_id
        self._identity = identity
        self._trait_evolution = trait_evolution or TraitEvolutionService(
            entity_id, traits or TraitProfile()
        )
        self._self_model = self_model
        self._internal_state = InternalState(
            **(internal_state or InternalState()).to_dict()
        )
        self._drives = drives or DriveProfile()
        supplied_activations = dict(drive_activations or {})
        unknown_drives = set(supplied_activations) - set(self._drives.values)
        if unknown_drives:
            raise ValueError(
                f"drive activations require configured baselines: "
                f"{sorted(unknown_drives)}"
            )
        self._drive_activations = {
            name: self._validate_activation(supplied_activations.get(name, 0.0))
            for name in self._drives.values
        }
        self._attention_candidates: deque[AttentionCandidate] = deque(maxlen=100)
        self._relationships = RelationshipStore(
            dict((relationships or RelationshipStore()).relationships)
        )
        self._memory = (memory or CharacterMemory()).copy()
        self._memory_service = memory_service or MemoryService(entity_id)
        self._behavior = behavior or BehaviorController(entity_id)
        self._state_store = state_store or InMemoryStateStore()
        if not isinstance(self._state_store, StateStore):
            raise TypeError("state_store must implement StateStore")
        self.state: MutableMapping[str, Any] = StateView(
            self._state_store, self._id, StateCategory.SESSION
        )
        if state is not None:
            self._state_store.save(
                self._id,
                {
                    **self._state_store.load(self._id),
                    StateCategory.SESSION: dict(state),
                },
            )
        self.handlers = HandlerRegistry()
        self.active_tasks: dict[str, Task] = {}
        self._runtime: Runtime | None = None

    @property
    def id(self) -> str:
        return self._id

    @property
    def identity(self) -> EntityIdentity:
        return self._identity

    @property
    def traits(self) -> TraitProfile:
        return self._trait_evolution.profile

    @property
    def trait_evolution(self) -> TraitEvolutionService:
        return self._trait_evolution

    @property
    def self_model(self) -> SelfModel:
        return self._self_model

    @property
    def internal_state(self) -> InternalState:
        """Return a detached snapshot of rapidly changing control state."""

        return InternalState(**self._internal_state.to_dict())

    @property
    def drives(self) -> DriveProfile:
        return self._drives

    @property
    def drive_activations(self) -> Mapping[str, float]:
        """Return a read-only snapshot of current drive activation."""

        return MappingProxyType(dict(self._drive_activations))

    @property
    def attention_candidates(self) -> tuple[AttentionCandidate, ...]:
        """Return retained candidates newest first."""

        return tuple(reversed(self._attention_candidates))

    @property
    def relationships(self) -> tuple[RelationshipState, ...]:
        """Return detached per-person social-state snapshots."""

        return self._relationships.list()

    @property
    def state_store(self) -> StateStore:
        """Return the storage boundary backing this Entity's ordinary state."""

        return self._state_store

    @property
    def runtime(self) -> Runtime | None:
        return self._runtime

    @property
    def memory_service(self) -> MemoryService:
        return self._memory_service

    @property
    def behavior(self) -> BehaviorController:
        return self._behavior

    def arbitrate_intention(
        self, intention: Intention, context: BehaviorContext
    ) -> BehaviorDecision:
        """Evaluate untrusted proposed behavior without executing its Action."""

        return self._behavior.arbitrate(intention, context)

    def get_state(
        self,
        key: str,
        *,
        category: StateCategory | str = StateCategory.SESSION,
        default: Any = None,
    ) -> Any:
        return self._state_store.get(
            self.id, key, category=category, default=default
        )

    def set_state(
        self,
        key: str,
        value: Any,
        *,
        category: StateCategory | str = StateCategory.SESSION,
    ) -> None:
        self._state_store.set(self.id, key, value, category=category)

    def delete_state(
        self,
        key: str,
        *,
        category: StateCategory | str = StateCategory.SESSION,
    ) -> bool:
        return self._state_store.delete(self.id, key, category=category)

    def list_state(
        self,
        *,
        category: StateCategory | str = StateCategory.SESSION,
    ) -> dict[str, Any]:
        return self._state_store.list(self.id, category=category)

    def snapshot_state(self) -> dict[StateCategory, dict[str, Any]]:
        return self._state_store.snapshot(self.id)

    def load_state(self) -> dict[StateCategory, dict[str, Any]]:
        """Load a detached complete Entity state through its store."""

        return self._state_store.load(self.id)

    def save_state(self, state: EntityStateSnapshot | None = None) -> None:
        """Save a complete Entity state through its store."""

        self._state_store.save(
            self.id, self.snapshot_state() if state is None else state
        )

    def copy_for_restart(self) -> Entity:
        """Reconstruct this Entity while retaining its owned character."""

        copied = Entity(
            self.id,
            identity=self.identity,
            trait_evolution=self._trait_evolution.copy(),
            self_model=SelfModel(**self.self_model.to_dict()),
            internal_state=self.internal_state,
            drives=self.drives,
            drive_activations=self.drive_activations,
            relationships=self._relationships,
            memory=self._memory,
            memory_service=self._memory_service,
            state_store=self.state_store,
            behavior=self._behavior.copy(),
        )
        copied._attention_candidates.extend(self._attention_candidates)
        return copied

    def inspect_relationship(self, subject_id: str) -> RelationshipState | None:
        return self._relationships.get(subject_id)

    def reflect_on_traits(
        self, reflection: TraitReflection
    ) -> TraitEvolutionDecision:
        """Submit evidence to Echo-owned policy; inference cannot assign traits."""

        return self._trait_evolution.consider(reflection)

    @property
    def memories(self) -> tuple[MemoryRecord, ...]:
        return self._memory.list()

    def get_memories(
        self, kind: MemoryKind | str | None = None
    ) -> tuple[MemoryRecord, ...]:
        """Return detached immutable records, optionally restricted by type."""

        return self._memory.list(kind)

    def remember(self, record: MemoryRecord) -> None:
        """Explicit Entity API for accepting an already validated memory."""

        self._memory.add(record)

    @property
    def durable_memories(self) -> tuple[DurableMemoryRecord, ...]:
        return self._memory_service.list(status=DurableMemoryStatus.ACTIVE)

    def commit_memory_candidate(
        self,
        candidate: MemoryCandidate,
        *,
        trusted_provenance: bool = False,
    ) -> MemoryCommitDecision:
        return self._memory_service.commit(
            candidate, trusted_provenance=trusted_provenance
        )

    def query_durable_memories(
        self,
        situation: str,
        *,
        limit: int | None = None,
        memory_type: DurableMemoryType | str | None = None,
    ) -> tuple[DurableMemoryRecord, ...]:
        return self._memory_service.query(
            situation, limit=limit, memory_type=memory_type
        )

    def consider_memory(
        self,
        record: MemoryRecord,
        importance: MemoryImportance,
        *,
        threshold: float = 0.5,
    ) -> MemoryRetentionDecision:
        return self._memory.consider(record, importance, threshold=threshold)

    def consolidate_memory(
        self,
        proposal: MemoryConsolidationProposal,
        *,
        minimum_confidence: float = 0.65,
    ) -> MemoryConsolidationDecision:
        decision = self._memory.consolidate(
            proposal, minimum_confidence=minimum_confidence
        )
        if decision.accepted and decision.record_id is not None:
            record = next(
                item for item in self._memory.list(proposal.target_kind)
                if item.id == decision.record_id
            )
            self._memory_service.commit(
                MemoryCandidate(
                    content=json.dumps(dict(record.content), sort_keys=True),
                    memory_type=DurableMemoryType.SEMANTIC,
                    source_type=MemorySourceType.REFLECTION,
                    source_refs=tuple(
                        f"memory:{item}" for item in decision.evidence_ids
                    ),
                    confidence=proposal.confidence,
                    importance=0.7,
                    canonical_key=f"{proposal.target_kind.value}:{proposal.key}",
                    subject_id=proposal.subject_id,
                )
            )
        return decision

    def inspect_character(self) -> dict[str, Any]:
        """Return detached Entity-owned character state as structured data."""

        return {
            "identity": self.identity.to_dict(),
            "traits": self.traits.to_dict(),
            "trait_evolution": self._trait_evolution.to_dict(),
            "self_model": self.self_model.to_dict(),
            "internal_state": self._internal_state.to_dict(),
            "drives": {
                "baselines": self.drives.to_dict(),
                "activations": dict(self._drive_activations),
            },
            "attention_candidates": [
                candidate.to_dict() for candidate in self.attention_candidates
            ],
            "relationships": self._relationships.to_dict(),
            "memory": self._memory.to_dict(),
            "durable_memory": [
                record.to_dict() for record in self._memory_service.list()
            ],
            "memory_audit": self._memory.audit_to_dict(),
            "behavior": self._behavior.to_dict(),
        }

    def _apply_signal_influence(
        self,
        signal_id: str,
        influence: SignalInfluence,
    ) -> dict[str, Any]:
        """Apply a validated Signal influence for RuntimeService coordination."""

        unknown_state = set(influence.state_deltas) - set(InternalState.DIMENSIONS)
        if unknown_state:
            raise ValueError(
                f"unknown internal state dimensions: {sorted(unknown_state)}"
            )
        referenced_drives = set(influence.drive_deltas)
        if influence.attention is not None:
            referenced_drives.update(influence.attention.drive_names)
        unknown_drives = referenced_drives - set(self.drives.values)
        if unknown_drives:
            raise ValueError(f"unknown drives: {sorted(unknown_drives)}")

        state_before = self._internal_state.to_dict()
        drives_before = dict(self._drive_activations)
        self._internal_state.adjust(**dict(influence.state_deltas))
        for name, delta in influence.drive_deltas.items():
            self._drive_activations[name] = max(
                0.0,
                min(1.0, self._drive_activations[name] + delta),
            )

        candidate = None
        if influence.attention is not None:
            proposal = influence.attention
            contributions = {
                name: self.drives.values[name] * self._drive_activations[name]
                for name in proposal.drive_names
            }
            drive_weight = max(contributions.values(), default=0.0)
            candidate = AttentionCandidate(
                entity_id=self.id,
                signal_id=signal_id,
                subject=proposal.subject,
                reason=proposal.reason,
                salience=proposal.salience,
                score=min(1.0, proposal.salience * 0.75 + drive_weight * 0.25),
                drive_contributions=contributions,
            )
            self._attention_candidates.append(candidate)
            goal = self._behavior.consider_curiosity(
                candidate,
                active_noninterruptible_work=bool(self.active_tasks),
            )
            if (
                goal is not None
                and goal.status is CuriosityGoalStatus.ACTIVE
                and goal.id not in self._self_model.active_goal_ids
            ):
                self._self_model.active_goal_ids.append(goal.id)

        state_after = self._internal_state.to_dict()
        drives_after = dict(self._drive_activations)
        return {
            "internal_state": state_after,
            "drive_activations": drives_after,
            "internal_state_changes": {
                name: {"before": state_before[name], "after": state_after[name]}
                for name in influence.state_deltas
                if state_before[name] != state_after[name]
            },
            "drive_activation_changes": {
                name: {"before": drives_before[name], "after": drives_after[name]}
                for name in influence.drive_deltas
                if drives_before[name] != drives_after[name]
            },
            "attention_candidate": candidate,
        }

    @staticmethod
    def _validate_activation(value: float) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0.0 <= value <= 1.0
        ):
            raise ValueError("drive activations must be between 0 and 1")
        return float(value)

    def on(self, signal: SignalKey) -> Callable[[Handler], Handler]:
        """Return a decorator that performs ordinary handler registration."""

        def decorator(handler: Handler) -> Handler:
            return self.handlers.register(signal, handler)

        return decorator

    async def action(self, action_type: str, **parameters: Any) -> Action:
        """Create and record an Action in this Entity's Runtime."""

        if self._runtime is None:
            raise RuntimeError("entity must be registered with a Runtime")
        action = Action(type=action_type, parameters=parameters)
        return self._runtime.record_action(action, self)

    async def act(self, action_type: str, **parameters: Any) -> Action:
        return await self.action(action_type, **parameters)

    async def emit(
        self,
        signal: Signal,
        *,
        priority: SignalPriority | int | str = SignalPriority.NORMAL,
    ) -> None:
        if self._runtime is None:
            raise RuntimeError("entity must be registered with a Runtime")
        await self._runtime.emit(signal, priority=priority)

    def __repr__(self) -> str:
        return f"Entity({self.id!r})"
