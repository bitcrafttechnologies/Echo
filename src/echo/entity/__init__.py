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
from echo.entity.memory import (
    CharacterMemory,
    CharacterMemoryRecord,
    EpisodicMemory,
    MemoryConsolidationDecision,
    MemoryConsolidationProposal,
    MemoryImportance,
    MemoryKind,
    MemoryRetentionDecision,
    PreferenceMemory,
    RelationshipMemory,
    SemanticMemory,
    WorkingMemory,
)
from echo.entity.relationships import RelationshipState, RelationshipStore
from echo.entity.self_model import SelfModel
from echo.entity.state import InternalState
from echo.entity.state_store import InMemoryStateStore, StateCategory, StateStore
from echo.entity.traits import TraitEvidence, TraitProfile

__all__ = [
    "CharacterMutationAuditRecord",
    "CharacterMutationDecision",
    "CharacterMutationTarget",
    "AttentionCandidate",
    "AttentionProposal",
    "DriveProfile",
    "EntityIdentity",
    "CharacterMemory",
    "CharacterMemoryRecord",
    "EpisodicMemory",
    "MemoryConsolidationDecision",
    "MemoryConsolidationProposal",
    "MemoryImportance",
    "InternalState",
    "InMemoryStateStore",
    "MemoryKind",
    "MemoryRetentionDecision",
    "PreferenceMemory",
    "RelationshipMemory",
    "RelationshipState",
    "RelationshipStore",
    "SelfModel",
    "StateCategory",
    "StateStore",
    "SemanticMemory",
    "SignalInfluence",
    "TraitEvidence",
    "TraitProfile",
    "WorkingMemory",
]
