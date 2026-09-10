"""First-party terminal operator interface for Echo."""

from echo.tui.app import EchoTui, render_snapshot
from echo.tui.client import HttpEchoClient, LocalEchoClient
from echo.tui.registry import ConsoleSurface, ConsoleSurfaceRegistry, default_registry
from echo.tui.tmux import TmuxSessionManager

__all__ = [
    "ConsoleSurface",
    "ConsoleSurfaceRegistry",
    "EchoTui",
    "HttpEchoClient",
    "LocalEchoClient",
    "TmuxSessionManager",
    "default_registry",
    "render_snapshot",
]
