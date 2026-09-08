"""The Entity public abstraction."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from echo.core.action import Action
from echo.core.handlers import Handler, HandlerRegistry, SignalKey
from echo.core.scheduler import SignalPriority
from echo.core.signal import Signal
from echo.core.task import Task
from echo.entity.attention import AttentionCandidate
from echo.entity.drives import DriveProfile
from echo.entity.identity import EntityIdentity
from echo.entity.influence import SignalInfluence
from echo.entity.relationships import RelationshipState, RelationshipStore
from echo.entity.self_model import SelfModel
from echo.entity.state import InternalState
from echo.entity.traits import TraitProfile

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
        self_model: SelfModel | None = None,
        internal_state: InternalState | None = None,
        drives: DriveProfile | None = None,
        drive_activations: Mapping[str, float] | None = None,
        relationships: RelationshipStore | None = None,
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
        self._id = entity_id
        self._identity = identity
        self._traits = traits or TraitProfile()
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
        self.state: dict[str, Any] = dict(state or {})
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
        return self._traits

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

    def inspect_relationship(self, subject_id: str) -> RelationshipState | None:
        return self._relationships.get(subject_id)

    def inspect_character(self) -> dict[str, Any]:
        """Return detached Entity-owned character state as structured data."""

        return {
            "identity": self.identity.to_dict(),
            "traits": self.traits.to_dict(),
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
