"""The Entity public abstraction."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from echo.core.action import Action
from echo.core.handlers import Handler, HandlerRegistry, SignalKey
from echo.core.scheduler import SignalPriority
from echo.core.signal import Signal
from echo.core.task import Task

if TYPE_CHECKING:
    from echo.core.runtime import Runtime


class Entity:
    """A persistent actor's identity, state, and registered handlers."""

    def __init__(self, entity_id: str, *, state: dict[str, Any] | None = None) -> None:
        if not entity_id:
            raise ValueError("entity id must not be empty")
        self.id = entity_id
        self.state: dict[str, Any] = dict(state or {})
        self.handlers = HandlerRegistry()
        self.active_tasks: dict[str, Task] = {}
        self._runtime: Runtime | None = None

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
