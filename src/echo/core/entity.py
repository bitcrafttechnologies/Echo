"""The Entity public abstraction."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from echo.core.handlers import Handler, HandlerRegistry, SignalKey


class Entity:
    """A persistent actor's identity, state, and registered handlers."""

    def __init__(self, entity_id: str, *, state: dict[str, Any] | None = None) -> None:
        if not entity_id:
            raise ValueError("entity id must not be empty")
        self.id = entity_id
        self.state: dict[str, Any] = dict(state or {})
        self.handlers = HandlerRegistry()

    def on(self, signal: SignalKey) -> Callable[[Handler], Handler]:
        """Return a decorator that performs ordinary handler registration."""

        def decorator(handler: Handler) -> Handler:
            return self.handlers.register(signal, handler)

        return decorator

    def __repr__(self) -> str:
        return f"Entity({self.id!r})"

