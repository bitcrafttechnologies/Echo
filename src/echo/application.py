"""Small embeddable application composition for one Echo Entity."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from echo.core.action import Action
from echo.core.entity import Entity
from echo.core.runtime import Runtime
from echo.entity.config import load_entity_seed
from echo.medulla.supervisor import MedullaStatus, MedullaSupervisor
from echo.medulla.transport import ActionDispatchResult, Transport


class EchoApplication:
    """Compose one Entity, one Runtime, and one Medulla supervisor.

    This is an embedding convenience, not a new Core abstraction. Applications
    remain responsible for registering handlers and deciding which authorized
    Actions to dispatch.
    """

    def __init__(
        self,
        entity: Entity,
        transports: Iterable[Transport],
        *,
        character_guidance: str | None = None,
        default_transport_id: str | None = None,
    ) -> None:
        if not isinstance(entity, Entity):
            raise TypeError("entity must be an Entity")
        if character_guidance is not None and not character_guidance.strip():
            raise ValueError("character_guidance must not be empty")
        self.entity = entity
        self.character_guidance = (
            character_guidance.strip() if character_guidance is not None else None
        )
        self.runtime = Runtime([entity], auto_start=False)
        self.medulla = MedullaSupervisor(
            self.runtime,
            transports,
            default_transport_id=default_transport_id,
        )

    @classmethod
    def from_entity_directory(
        cls,
        directory: str | Path,
        transports: Iterable[Transport],
        *,
        default_transport_id: str | None = None,
    ) -> EchoApplication:
        """Load a project-owned Entity seed and compose an application."""

        seed = load_entity_seed(directory)
        return cls(
            seed.create_entity(),
            transports,
            character_guidance=seed.character_guidance,
            default_transport_id=default_transport_id,
        )

    async def start(self) -> MedullaStatus:
        self.runtime.start()
        return await self.medulla.start()

    async def stop(self) -> MedullaStatus:
        status = await self.medulla.stop()
        self.runtime.stop()
        return status

    async def dispatch(
        self,
        action: Action,
        *,
        transport_id: str | None = None,
    ) -> ActionDispatchResult:
        return await self.medulla.dispatch(action, transport_id=transport_id)

    async def __aenter__(self) -> EchoApplication:
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()
