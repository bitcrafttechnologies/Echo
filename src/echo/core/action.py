"""Actions: structured records of Entity intent."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True, kw_only=True)
class Action:
    type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    entity_id: str | None = None
    task_id: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.type:
            raise ValueError("action type must not be empty")
        self.parameters = dict(self.parameters)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "parameters": self.parameters.copy(),
            "entity_id": self.entity_id,
            "task_id": self.task_id,
            "created_at": self.created_at.isoformat(),
        }

