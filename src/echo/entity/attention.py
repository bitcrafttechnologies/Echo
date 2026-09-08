"""Attention candidates influenced by Signals and active Entity drives."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True, frozen=True, kw_only=True)
class AttentionProposal:
    """Request to retain something for attention without authorizing behavior."""

    subject: str
    reason: str
    salience: float
    drive_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            drive_names = tuple(self.drive_names)
        except TypeError as error:
            raise ValueError("attention drive names must be iterable") from error
        if (
            not isinstance(self.subject, str)
            or not isinstance(self.reason, str)
            or not self.subject.strip()
            or not self.reason.strip()
        ):
            raise ValueError("attention subject and reason must not be empty")
        if (
            isinstance(self.salience, bool)
            or not isinstance(self.salience, (int, float))
            or not 0.0 <= self.salience <= 1.0
        ):
            raise ValueError("attention salience must be between 0 and 1")
        if any(
            not isinstance(name, str) or not name.strip()
            for name in drive_names
        ):
            raise ValueError("attention drive names must not be empty")
        object.__setattr__(self, "salience", float(self.salience))
        object.__setattr__(self, "drive_names", tuple(dict.fromkeys(drive_names)))


@dataclass(slots=True, frozen=True, kw_only=True)
class AttentionCandidate:
    """A retained observation candidate, not an intention or Action."""

    entity_id: str
    signal_id: str
    subject: str
    reason: str
    salience: float
    score: float
    drive_contributions: Mapping[str, float] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (self.id, self.entity_id, self.signal_id)
        ):
            raise ValueError("attention candidate IDs must not be empty")
        if (
            not isinstance(self.subject, str)
            or not isinstance(self.reason, str)
            or not self.subject.strip()
            or not self.reason.strip()
        ):
            raise ValueError("attention subject and reason must not be empty")
        for label, value in (("salience", self.salience), ("score", self.score)):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0.0 <= value <= 1.0
            ):
                raise ValueError(f"attention {label} must be between 0 and 1")
        if (
            not isinstance(self.created_at, datetime)
            or self.created_at.tzinfo is None
        ):
            raise ValueError("attention created_at must be timezone-aware")
        contributions = dict(self.drive_contributions)
        for name, value in contributions.items():
            if not isinstance(name, str) or not name:
                raise ValueError("attention drive names must not be empty")
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0.0 <= value <= 1.0
            ):
                raise ValueError(
                    "attention drive contributions must be between 0 and 1"
                )
            contributions[name] = float(value)
        object.__setattr__(self, "salience", float(self.salience))
        object.__setattr__(self, "score", float(self.score))
        object.__setattr__(
            self,
            "drive_contributions",
            MappingProxyType(contributions),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "signal_id": self.signal_id,
            "subject": self.subject,
            "reason": self.reason,
            "salience": self.salience,
            "score": self.score,
            "drive_contributions": dict(self.drive_contributions),
            "created_at": self.created_at.isoformat(),
        }
