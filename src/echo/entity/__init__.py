"""Provider-independent entity character and continuity primitives.

These types establish ownership boundaries for persistent identity, traits,
internal control state, drives, attention, relationships, and embodiment.
"""

from echo.entity.audit import (
    CharacterMutationAuditRecord,
    CharacterMutationDecision,
    CharacterMutationTarget,
)
from echo.entity.attention import AttentionCandidate, AttentionProposal
from echo.entity.drives import DriveProfile
from echo.entity.influence import SignalInfluence
from echo.entity.identity import EntityIdentity
from echo.entity.relationships import RelationshipState, RelationshipStore
from echo.entity.self_model import SelfModel
from echo.entity.state import InternalState
from echo.entity.traits import TraitEvidence, TraitProfile

__all__ = [
    "CharacterMutationAuditRecord",
    "CharacterMutationDecision",
    "CharacterMutationTarget",
    "AttentionCandidate",
    "AttentionProposal",
    "DriveProfile",
    "EntityIdentity",
    "InternalState",
    "RelationshipState",
    "RelationshipStore",
    "SelfModel",
    "SignalInfluence",
    "TraitEvidence",
    "TraitProfile",
]
