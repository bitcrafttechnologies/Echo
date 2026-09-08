"""The Entity public abstraction."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from echo.core.action import Action
from echo.core.handlers import Handler, HandlerRegistry, SignalKey
from echo.core.scheduler import SignalPriority
from echo.core.signal import Signal
from echo.core.task import Task
from echo.entity.identity import EntityIdentity
from echo.entity.self_model import SelfModel
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
        self._id = entity_id
        self._identity = identity
        self._traits = traits or TraitProfile()
        self._self_model = self_model
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

    def inspect_character(self) -> dict[str, Any]:
        """Return the Phase 2 character slice as structured data."""

        return {
            "identity": self.identity.to_dict(),
            "traits": self.traits.to_dict(),
            "self_model": self.self_model.to_dict(),
        }

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
