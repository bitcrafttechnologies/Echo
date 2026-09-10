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
from echo.entity.behavior import (
    BehaviorContext,
    BehaviorController,
    BehaviorDecision,
    BehaviorOutcome,
    BehaviorPolicy,
    CuriosityGoal,
    CuriosityGoalStatus,
    Intention,
    IntentionType,
)
from echo.entity.context import (
    CharacterContext,
    CharacterContextBuilder,
    CharacterContextRequest,
)
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
from echo.entity.memory_store import (
    DurableMemoryRecord,
    DurableMemoryStatus,
    DurableMemoryType,
    InMemoryMemoryRepository,
    MemoryCandidate,
    MemoryCommitAction,
    MemoryCommitDecision,
    MemoryRepository,
    MemoryService,
    MemorySourceType,
    user_statement_candidates,
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
    "CharacterContext",
    "CharacterContextBuilder",
    "CharacterContextRequest",
    "AttentionCandidate",
    "AttentionProposal",
    "BehaviorContext",
    "BehaviorController",
    "BehaviorDecision",
    "BehaviorOutcome",
    "BehaviorPolicy",
    "CuriosityGoal",
    "CuriosityGoalStatus",
    "DriveProfile",
    "DurableMemoryRecord",
    "DurableMemoryStatus",
    "DurableMemoryType",
    "EntityIdentity",
    "CharacterMemory",
    "CharacterMemoryRecord",
    "EpisodicMemory",
    "MemoryConsolidationDecision",
    "MemoryConsolidationProposal",
    "MemoryImportance",
    "InternalState",
    "Intention",
    "IntentionType",
    "InMemoryStateStore",
    "InMemoryMemoryRepository",
    "MemoryKind",
    "MemoryCandidate",
    "MemoryCommitAction",
    "MemoryCommitDecision",
    "MemoryRepository",
    "MemoryService",
    "MemorySourceType",
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
    "user_statement_candidates",
]
