"""Explicit handler registration and signal resolution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

from echo.core.signal import Signal


Handler: TypeAlias = Callable[[Signal], Awaitable[Any]]
SignalKey: TypeAlias = str | type[Signal]


class HandlerRegistry:
    """Ordered mappings from signal identifiers to async handlers."""

    def __init__(self) -> None:
        self._registrations: list[tuple[SignalKey, Handler]] = []

    def register(self, signal: SignalKey, handler: Handler) -> Handler:
        if not isinstance(signal, str) and not (
            isinstance(signal, type) and issubclass(signal, Signal)
        ):
            raise TypeError("signal key must be a type string or Signal subclass")
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._registrations.append((signal, handler))
        return handler

    def resolve(self, signal: Signal) -> tuple[Handler, ...]:
        """Return matching handlers in registration order, without duplicates."""

        matches: list[Handler] = []
        for key, handler in self._registrations:
            matched = key == signal.type if isinstance(key, str) else isinstance(signal, key)
            if matched and handler not in matches:
                matches.append(handler)
        return tuple(matches)

    def __len__(self) -> int:
        return len(self._registrations)

