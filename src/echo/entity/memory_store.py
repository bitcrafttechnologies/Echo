"""Durable, Entity-scoped semantic and character-development memory."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4


class DurableMemoryType(StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    CHARACTER = "character"


class MemorySourceType(StrEnum):
    USER_EXPLICIT = "user_explicit"
    OBSERVED = "observed"
    INFERRED = "inferred"
    SYSTEM_CONFIGURED = "system_configured"
    # Legacy provenance values remain readable for existing databases.
    USER_STATEMENT = "user_statement"
    DIRECT_EXPERIENCE = "direct_experience"
    INFERENCE = "inference"
    REFLECTION = "reflection"
    EXTERNAL_RETRIEVAL = "external_retrieval"
    SYSTEM_OBSERVATION = "system_observation"
    MODEL_KNOWLEDGE = "model_knowledge"


class DurableMemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class MemoryCommitAction(StrEnum):
    COMMITTED = "committed"
    MERGED = "merged"
    REJECTED = "rejected"
    DEFERRED = "deferred"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def normalize_memory_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("memory content must be text")
    return " ".join(value.strip().split())


@dataclass(slots=True, kw_only=True, frozen=True)
class MemoryCandidate:
    content: str
    memory_type: DurableMemoryType
    source_type: MemorySourceType
    source_refs: tuple[str, ...]
    confidence: float
    importance: float
    canonical_key: str
    subject_id: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        content = normalize_memory_text(self.content)
        key = normalize_memory_text(self.canonical_key).lower()
        if not content or len(content) > 800:
            raise ValueError("candidate content must contain 1 to 800 characters")
        if not key or len(key) > 200:
            raise ValueError("candidate canonical_key must contain 1 to 200 characters")
        for name in ("confidence", "importance"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= value <= 1
            ):
                raise ValueError(f"candidate {name} must be between 0 and 1")
        if not self.source_refs or not all(
            isinstance(ref, str) and ref for ref in self.source_refs
        ):
            raise ValueError("candidate source_refs must contain non-empty references")
        if self.subject_id is not None and not self.subject_id.strip():
            raise ValueError("candidate subject_id must not be empty")
        _aware(self.created_at, "candidate created_at")
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "canonical_key", key)
        object.__setattr__(self, "memory_type", DurableMemoryType(self.memory_type))
        object.__setattr__(self, "source_type", MemorySourceType(self.source_type))
        object.__setattr__(self, "source_refs", tuple(dict.fromkeys(self.source_refs)))


@dataclass(slots=True, kw_only=True, frozen=True)
class DurableMemoryRecord:
    id: str
    entity_id: str
    memory_type: DurableMemoryType
    content: str
    canonical_key: str
    source_type: MemorySourceType
    source_refs: tuple[str, ...]
    confidence: float
    importance: float
    status: DurableMemoryStatus = DurableMemoryStatus.ACTIVE
    subject_id: str | None = None
    supersedes_id: str | None = None
    version: int = 1
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    last_accessed_at: datetime | None = None
    access_count: int = 0

    def __post_init__(self) -> None:
        if not self.id or not self.entity_id:
            raise ValueError("memory id and entity_id must not be empty")
        content = normalize_memory_text(self.content)
        if not content or len(content) > 800:
            raise ValueError("memory content must contain 1 to 800 characters")
        if not self.canonical_key or len(self.canonical_key) > 200:
            raise ValueError("memory canonical_key must not be empty")
        for name in ("confidence", "importance"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= value <= 1
            ):
                raise ValueError(f"memory {name} must be between 0 and 1")
        if not self.source_refs or not all(
            isinstance(ref, str) and ref for ref in self.source_refs
        ):
            raise ValueError("memory source_refs must contain non-empty references")
        if self.version < 1 or self.access_count < 0:
            raise ValueError("memory version and access_count are invalid")
        _aware(self.created_at, "memory created_at")
        _aware(self.updated_at, "memory updated_at")
        if self.last_accessed_at is not None:
            _aware(self.last_accessed_at, "memory last_accessed_at")
        object.__setattr__(self, "memory_type", DurableMemoryType(self.memory_type))
        object.__setattr__(self, "source_type", MemorySourceType(self.source_type))
        object.__setattr__(self, "status", DurableMemoryStatus(self.status))
        object.__setattr__(self, "source_refs", tuple(self.source_refs))
        object.__setattr__(self, "content", content)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "memory_type": self.memory_type.value,
            "content": self.content,
            "canonical_key": self.canonical_key,
            "source_type": self.source_type.value,
            "source_refs": list(self.source_refs),
            "confidence": self.confidence,
            "importance": self.importance,
            "status": self.status.value,
            "subject_id": self.subject_id,
            "supersedes_id": self.supersedes_id,
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_accessed_at": (
                self.last_accessed_at.isoformat()
                if self.last_accessed_at
                else None
            ),
            "access_count": self.access_count,
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class MemoryCommitDecision:
    candidate_id: str
    action: MemoryCommitAction
    reason: str
    record: DurableMemoryRecord | None = None
    superseded_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "action": self.action.value,
            "reason": self.reason,
            "record": self.record.to_dict() if self.record else None,
            "superseded_ids": list(self.superseded_ids),
        }


@runtime_checkable
class MemoryRepository(Protocol):
    def get(self, entity_id: str, memory_id: str) -> DurableMemoryRecord | None: ...

    def list_for_entity(
        self,
        entity_id: str,
        *,
        status: DurableMemoryStatus | str | None = None,
        memory_type: DurableMemoryType | str | None = None,
    ) -> tuple[DurableMemoryRecord, ...]: ...

    def commit(
        self,
        record: DurableMemoryRecord,
        *,
        supersede_ids: tuple[str, ...] = (),
    ) -> None: ...

    def archive(self, entity_id: str, memory_id: str) -> bool: ...
    def delete(self, entity_id: str, memory_id: str) -> bool: ...

    def record_access(
        self,
        entity_id: str,
        memory_ids: tuple[str, ...],
        accessed_at: datetime,
    ) -> None: ...


class InMemoryMemoryRepository:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], DurableMemoryRecord] = {}

    def get(self, entity_id: str, memory_id: str) -> DurableMemoryRecord | None:
        return self._records.get((entity_id, memory_id))

    def list_for_entity(
        self,
        entity_id: str,
        *,
        status: DurableMemoryStatus | str | None = None,
        memory_type: DurableMemoryType | str | None = None,
    ) -> tuple[DurableMemoryRecord, ...]:
        normalized_status = (
            DurableMemoryStatus(status) if status is not None else None
        )
        normalized_type = (
            DurableMemoryType(memory_type) if memory_type is not None else None
        )
        records = [
            record
            for (owner, _), record in self._records.items()
            if owner == entity_id
            and (normalized_status is None or record.status is normalized_status)
            and (normalized_type is None or record.memory_type is normalized_type)
        ]
        return tuple(
            sorted(
                records,
                key=lambda record: (record.created_at, record.id),
                reverse=True,
            )
        )

    def commit(self, record: DurableMemoryRecord, *, supersede_ids: tuple[str, ...] = ()) -> None:
        now = _now()
        for memory_id in supersede_ids:
            old = self._records.get((record.entity_id, memory_id))
            if old is not None:
                self._records[(record.entity_id, memory_id)] = replace(
                    old,
                    status=DurableMemoryStatus.SUPERSEDED,
                    updated_at=now,
                )
        self._records[(record.entity_id, record.id)] = record

    def archive(self, entity_id: str, memory_id: str) -> bool:
        record = self.get(entity_id, memory_id)
        if record is None:
            return False
        self._records[(entity_id, memory_id)] = replace(
            record,
            status=DurableMemoryStatus.ARCHIVED,
            updated_at=_now(),
        )
        return True

    def delete(self, entity_id: str, memory_id: str) -> bool:
        return self._records.pop((entity_id, memory_id), None) is not None

    def record_access(
        self,
        entity_id: str,
        memory_ids: tuple[str, ...],
        accessed_at: datetime,
    ) -> None:
        for memory_id in memory_ids:
            record = self.get(entity_id, memory_id)
            if record is not None:
                self._records[(entity_id, memory_id)] = replace(
                    record,
                    last_accessed_at=accessed_at,
                    access_count=record.access_count + 1,
                )


_TOKEN = re.compile(r"[a-z0-9]+")
_CORRECTION = re.compile(
    r"\b(?:actually|anymore|gone|no longer)\b",
    re.I,
)
_FILLER = re.compile(
    r"^(?:haha|lol|yes|yeah|yep|sure|ok(?:ay)?|go ahead|go for it|do it|"
    r"sounds good|thanks|thank you|that was helpful)\b",
    re.I,
)
_STOP = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "but", "does", "doesn",
        "don", "entity", "for", "has", "have", "i", "in", "is", "it",
        "lots", "my", "not", "now", "of", "on", "our", "probably", "right",
        "still", "t", "the", "to", "we", "when", "with", "you", "your",
    }
)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(token for token in _TOKEN.findall(text.lower()) if token not in _STOP)


def _list_items(text: str) -> tuple[str, ...]:
    return tuple(
        cleaned
        for item in re.split(r"\s*,\s*|\s+(?:and|or)\s+", text)
        if (cleaned := re.sub(r"^(?:and|or)\s+", "", item.strip(), flags=re.I))
    )


class MemoryService:
    """Echo-owned policy and retrieval boundary for one Entity."""

    def __init__(
        self,
        entity_id: str,
        repository: MemoryRepository | None = None,
        *,
        max_results: int = 8,
        max_active_records: int = 1000,
    ) -> None:
        if not entity_id:
            raise ValueError("entity_id must not be empty")
        for name, value in {
            "max_results": max_results,
            "max_active_records": max_active_records,
        }.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.entity_id = entity_id
        self.repository = repository or InMemoryMemoryRepository()
        self.max_results = max_results
        self.max_active_records = max_active_records

    @property
    def durable(self) -> bool:
        return not isinstance(self.repository, InMemoryMemoryRepository)

    def commit(
        self,
        candidate: MemoryCandidate,
        *,
        trusted_provenance: bool = False,
    ) -> MemoryCommitDecision:
        if candidate.source_type is MemorySourceType.MODEL_KNOWLEDGE:
            return MemoryCommitDecision(
                candidate_id=candidate.id,
                action=MemoryCommitAction.REJECTED,
                reason="model knowledge is not personal durable memory",
            )
        if candidate.source_type is MemorySourceType.DIRECT_EXPERIENCE and not trusted_provenance:
            return MemoryCommitDecision(
                candidate_id=candidate.id,
                action=MemoryCommitAction.REJECTED,
                reason="direct experience requires a trusted Core source",
            )
        if candidate.memory_type is DurableMemoryType.CHARACTER and not (
            candidate.source_type is MemorySourceType.REFLECTION
            and len(candidate.source_refs) >= 2
            and candidate.confidence >= 0.8
            and candidate.importance >= 0.75
        ):
            return MemoryCommitDecision(
                candidate_id=candidate.id,
                action=MemoryCommitAction.DEFERRED,
                reason="character development requires repeated reflected evidence",
            )
        if candidate.confidence < 0.6 or candidate.importance < 0.35:
            return MemoryCommitDecision(
                candidate_id=candidate.id,
                action=MemoryCommitAction.DEFERRED,
                reason="candidate did not meet confidence and importance policy",
            )

        active = self.repository.list_for_entity(
            self.entity_id,
            status=DurableMemoryStatus.ACTIVE,
            memory_type=candidate.memory_type,
        )
        exact = next(
            (
                record
                for record in active
                if record.canonical_key == candidate.canonical_key
                and normalize_memory_text(record.content).casefold()
                == candidate.content.casefold()
            ),
            None,
        )
        if exact is not None:
            merged = replace(
                exact,
                source_refs=tuple(
                    dict.fromkeys((*exact.source_refs, *candidate.source_refs))
                ),
                confidence=max(exact.confidence, candidate.confidence),
                importance=max(exact.importance, candidate.importance),
                updated_at=_now(),
            )
            self.repository.commit(merged)
            return MemoryCommitDecision(
                candidate_id=candidate.id,
                action=MemoryCommitAction.MERGED,
                reason="equivalent active memory already exists",
                record=merged,
            )

        superseded = [
            record
            for record in active
            if record.canonical_key == candidate.canonical_key
        ]
        if not superseded and _CORRECTION.search(candidate.content):
            candidate_terms = set(_tokens(candidate.content))
            superseded = [
                record
                for record in active
                if candidate_terms
                and len(candidate_terms & set(_tokens(record.content))) >= 1
            ]
        if len(active) >= self.max_active_records and not superseded:
            return MemoryCommitDecision(
                candidate_id=candidate.id,
                action=MemoryCommitAction.DEFERRED,
                reason="Entity active-memory limit has been reached",
            )
        record = DurableMemoryRecord(
            id=str(uuid4()), entity_id=self.entity_id, memory_type=candidate.memory_type,
            content=candidate.content,
            canonical_key=(
                superseded[0].canonical_key
                if superseded and _CORRECTION.search(candidate.content)
                else candidate.canonical_key
            ),
            source_type=candidate.source_type, source_refs=candidate.source_refs,
            confidence=float(candidate.confidence), importance=float(candidate.importance),
            subject_id=candidate.subject_id,
            supersedes_id=superseded[0].id if superseded else None,
            version=max((item.version for item in superseded), default=0) + 1,
        )
        superseded_ids = tuple(item.id for item in superseded)
        self.repository.commit(record, supersede_ids=superseded_ids)
        return MemoryCommitDecision(
            candidate_id=candidate.id,
            action=MemoryCommitAction.COMMITTED,
            reason="candidate passed Echo memory policy",
            record=record,
            superseded_ids=superseded_ids,
        )

    def query(
        self,
        situation: str,
        *,
        limit: int | None = None,
        memory_type: DurableMemoryType | str | None = None,
        record_access: bool = True,
    ) -> tuple[DurableMemoryRecord, ...]:
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
        ):
            raise ValueError("memory query limit must be a non-negative integer")
        requested = self.max_results if limit is None else min(limit, self.max_results)
        terms = set(_tokens(situation))
        records = self.repository.list_for_entity(
            self.entity_id,
            status=DurableMemoryStatus.ACTIVE,
            memory_type=memory_type,
        )
        ranked = sorted(
            (
                record for record in records
                if self._relevance(record, situation, terms) > 0
            ),
            key=lambda record: (
                -self._relevance(record, situation, terms),
                -record.importance,
                -record.updated_at.timestamp(),
                record.id,
            ),
        )[:requested]
        if record_access and ranked:
            self.repository.record_access(
                self.entity_id,
                tuple(record.id for record in ranked),
                _now(),
            )
        return tuple(ranked)

    def query_trace(
        self,
        situation: str,
        *,
        limit: int | None = None,
        memory_type: DurableMemoryType | str | None = None,
    ) -> dict[str, Any]:
        """Explain retrieval data and scores without exposing model reasoning."""

        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
        ):
            raise ValueError("memory query limit must be a non-negative integer")
        requested = self.max_results if limit is None else min(limit, self.max_results)
        terms = set(_tokens(situation))
        records = self.repository.list_for_entity(
            self.entity_id,
            status=DurableMemoryStatus.ACTIVE,
            memory_type=memory_type,
        )
        scored = [
            (record, self._relevance(record, situation, terms)) for record in records
        ]
        scored.sort(
            key=lambda item: (
                -item[1], -item[0].importance, -item[0].updated_at.timestamp(), item[0].id
            )
        )
        selected = [item for item in scored if item[1] > 0][:requested]
        return {
            "query": situation,
            "candidates": [
                {
                    "id": record.id,
                    "memory_type": record.memory_type.value,
                    "canonical_key": record.canonical_key,
                    "content": record.content,
                    "score": score,
                    "confidence": record.confidence,
                    "importance": record.importance,
                    "source_type": record.source_type.value,
                    "selected": any(record.id == chosen.id for chosen, _ in selected),
                }
                for record, score in scored
            ],
            "selected": [record.id for record, _score in selected],
            "context_injection": [record.to_dict() for record, _score in selected],
        }

    @staticmethod
    def _relevance(
        record: DurableMemoryRecord, situation: str, terms: set[str]
    ) -> int:
        record_terms = set(_tokens(f"{record.canonical_key} {record.content}"))
        score = len(terms & record_terms) * 10
        lowered = situation.casefold()
        if re.search(r"\b(?:my|me|i)\b.*\bname\b|\bname\b.*\b(?:my|me)\b", lowered):
            if record.canonical_key == "user.preferred_name":
                score += 1000
        if re.search(r"\b(?:recently|yesterday|last time|talking about|discussed)\b", lowered):
            if record.memory_type is DurableMemoryType.EPISODIC:
                score += 200
        if re.search(r"\bwho are you\b|\byour identity\b", lowered):
            if record.memory_type is DurableMemoryType.CHARACTER:
                score += 200
        if re.search(r"\b(?:capable|capability|capabilities|can you do)\b", lowered):
            if record.canonical_key.startswith("semantic:capability."):
                score += 200
        if score > 0:
            if record.source_type is MemorySourceType.USER_EXPLICIT:
                score += 30
            elif record.source_type in {
                MemorySourceType.OBSERVED,
                MemorySourceType.DIRECT_EXPERIENCE,
            }:
                score += 15
        return score

    def list(
        self,
        *,
        status: DurableMemoryStatus | str | None = None,
        memory_type: DurableMemoryType | str | None = None,
    ) -> tuple[DurableMemoryRecord, ...]:
        return self.repository.list_for_entity(
            self.entity_id,
            status=status,
            memory_type=memory_type,
        )

    def get(self, memory_id: str) -> DurableMemoryRecord | None:
        return self.repository.get(self.entity_id, memory_id)

    def archive(self, memory_id: str) -> bool:
        return self.repository.archive(self.entity_id, memory_id)

    def delete(self, memory_id: str) -> bool:
        return self.repository.delete(self.entity_id, memory_id)


def user_statement_candidates(
    text: str,
    *,
    signal_id: str,
    action_id: str | None = None,
    subject_id: str | None = None,
) -> tuple[MemoryCandidate, ...]:
    """Conservatively extract concise declarative evidence without an LLM."""

    normalized = normalize_memory_text(text)
    if len(normalized) < 4 or normalized.endswith("?") or _FILLER.match(normalized):
        return ()
    sentences = [
        part.strip(" .")
        for part in re.split(r"(?<=[.!])\s+", normalized)
        if part.strip(" .")
    ]
    candidates: list[MemoryCandidate] = []
    prior_subject: str | None = None
    for sentence in sentences:
        lowered = sentence.lower().replace("’", "'")
        if lowered.startswith("my neighborhood"):
            prior_subject = "neighborhood"
        if re.search(
            r"\b(?:battery|cpu|memory|temperature|signal|bluetooth)\b\s*(?:=|is|at)\s*\d+(?:\.\d+)?%?",
            lowered,
        ):
            continue
        attribute_match = re.fullmatch(
            r"my\s+([a-z][a-z _-]{0,40})\s+is\s+(.+)", sentence, re.I
        )
        name_match = re.fullmatch(
            r"(?:call me|i prefer to be called)\s+(.+)", sentence, re.I
        )
        preference_match = re.fullmatch(r"i prefer\s+(.+)", sentence, re.I)
        if attribute_match or name_match or preference_match:
            if attribute_match:
                attribute = attribute_match.group(1).strip().lower().replace(" ", "_")
                value = attribute_match.group(2).strip(" .")
                if attribute == "name":
                    attribute = "preferred_name"
                content = f"The user's {attribute.replace('_', ' ')} is {value}"
                key = f"user.{attribute}"
            elif name_match:
                value = name_match.group(1).strip(" .")
                content = f"The user's preferred name is {value}"
                key = "user.preferred_name"
            else:
                assert preference_match is not None
                value = preference_match.group(1).strip(" .")
                value_terms = _tokens(value)
                if len(value_terms) == 1 and value[:1].isupper():
                    content = f"The user's preferred name is {value}"
                    key = "user.preferred_name"
                else:
                    content = f"The user prefers {value}"
                    key = "user.preference." + ".".join(value_terms[:3] or ("general",))
            if value:
                refs = (f"signal:{signal_id}",) + (
                    (f"action:{action_id}",) if action_id is not None else ()
                )
                candidates.append(
                    MemoryCandidate(
                        content=content,
                        memory_type=DurableMemoryType.SEMANTIC,
                        source_type=MemorySourceType.USER_EXPLICIT,
                        source_refs=refs,
                        confidence=0.99,
                        importance=0.9,
                        canonical_key=key,
                        subject_id=subject_id,
                    )
                )
                continue
        statements = [sentence]
        list_match = re.match(r"(?:it|my neighborhood) (?:has|contains) (.+)", sentence, re.I)
        if list_match and (
            prior_subject == "neighborhood"
            or lowered.startswith("my neighborhood")
        ):
            items = _list_items(list_match.group(1))
            statements = [
                f"The user's neighborhood has {item.strip()}"
                for item in items
                if item.strip()
            ]
        missing_match = re.match(
            r"you (?:do not|don't) have (.+)", lowered, re.I
        )
        if missing_match:
            items = _list_items(missing_match.group(1))
            statements = [
                f"The Entity does not have {item.strip()}"
                for item in items
                if item.strip()
            ]
        for statement in statements:
            terms = _tokens(statement)
            if not terms:
                continue
            topic_terms = terms[:4]
            if "neighborhood" in terms:
                detail = tuple(term for term in terms if term not in {"tucker", "neighborhood"})
                topic_terms = ("neighborhood", *(detail[-2:] or ("general",)))
            elif statement.lower().startswith("the entity does not have"):
                capability = tuple(
                    term
                    for term in terms
                    if term not in {"entity", "doesn", "don", "t"}
                )
                topic_terms = ("capability", *(capability[-2:] or ("unknown",)))
            key = "semantic:" + ".".join(topic_terms)
            refs = (f"signal:{signal_id}",) + (
                (f"action:{action_id}",) if action_id is not None else ()
            )
            candidates.append(
                MemoryCandidate(
                    content=statement,
                    memory_type=DurableMemoryType.SEMANTIC,
                    source_type=MemorySourceType.USER_EXPLICIT,
                    source_refs=refs,
                    confidence=0.85,
                    importance=0.65,
                    canonical_key=key,
                    subject_id=subject_id,
                )
            )
    return tuple(candidates)
