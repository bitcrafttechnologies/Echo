"""Discoverable console surface metadata shared by terminal frontends."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConsoleSurface:
    """A stable operator surface declaration, not a runtime object hook."""

    key: str
    title: str
    order: int
    capabilities: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "title": self.title,
            "order": self.order,
            "capabilities": list(self.capabilities),
        }


class ConsoleSurfaceRegistry:
    """Small registration point for operator-visible subsystem surfaces."""

    def __init__(self) -> None:
        self._surfaces: dict[str, ConsoleSurface] = {}

    def register(self, surface: ConsoleSurface) -> None:
        if not surface.key or surface.key in self._surfaces:
            raise ValueError(f"console surface already registered: {surface.key!r}")
        self._surfaces[surface.key] = surface

    def get(self, key: str) -> ConsoleSurface:
        try:
            return self._surfaces[key]
        except KeyError as error:
            raise KeyError(f"unknown console surface: {key}") from error

    def list(self) -> tuple[ConsoleSurface, ...]:
        return tuple(sorted(self._surfaces.values(), key=lambda item: item.order))

    def catalog(self) -> list[dict[str, object]]:
        """Return transport-safe discovery metadata for other interfaces."""
        return [surface.to_dict() for surface in self.list()]


def default_registry() -> ConsoleSurfaceRegistry:
    registry = ConsoleSurfaceRegistry()
    for surface in (
        ConsoleSurface("overview", "Overview", 1, ("status", "activity")),
        ConsoleSurface("chat", "Chat", 2, ("inspect", "send")),
        ConsoleSurface("signals", "Signals", 3, ("list", "inspect", "emit")),
        ConsoleSurface("tasks", "Tasks", 4, ("list", "inspect", "cancel")),
        ConsoleSurface("entity", "Entity", 5, ("list", "inspect", "relationships")),
        ConsoleSurface("providers", "Providers", 6, ("inspect", "switch")),
        ConsoleSurface("medulla", "Medulla Nodes", 7, ("inspect", "approve", "authorize", "decline", "block")),
        ConsoleSurface("configuration", "Configuration", 8, ("inspect", "edit", "reload")),
        ConsoleSurface("logs", "Logs", 9, ("list", "filter")),
    ):
        registry.register(surface)
    return registry
