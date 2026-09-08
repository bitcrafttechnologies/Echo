"""Provider-independent entity character and continuity primitives.

These types establish ownership boundaries for persistent identity, traits,
internal control state, drives, relationships, and embodiment. Phase 2 composes
identity, traits, and self-model into the public Entity; later slices remain
separate until their roadmap phases.
"""

from echo.entity.audit import (
    CharacterMutationAuditRecord,
    CharacterMutationDecision,
    CharacterMutationTarget,
)
from echo.entity.drives import DriveProfile
from echo.entity.identity import EntityIdentity
from echo.entity.relationships import RelationshipState, RelationshipStore
from echo.entity.self_model import SelfModel
from echo.entity.state import InternalState
from echo.entity.traits import TraitEvidence, TraitProfile

__all__ = [
    "CharacterMutationAuditRecord",
    "CharacterMutationDecision",
    "CharacterMutationTarget",
    "DriveProfile",
    "EntityIdentity",
    "InternalState",
    "RelationshipState",
    "RelationshipStore",
    "SelfModel",
    "TraitEvidence",
    "TraitProfile",
]
