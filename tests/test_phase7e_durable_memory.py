from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (  # noqa: E402
    CharacterContextBuilder,
    CharacterContextRequest,
    DurableMemoryStatus,
    DurableMemoryType,
    Entity,
    MemoryCandidate,
    MemoryCommitAction,
    MemoryConsolidationProposal,
    MemoryImportance,
    MemoryKind,
    MemoryService,
    MemorySourceType,
    MockProvider,
    ProviderRouter,
    Runtime,
    RuntimeService,
    EpisodicMemory,
    Signal,
    SQLiteMemoryRepository,
)
from echo.host import register_user_message_handler  # noqa: E402


def candidate(
    content: str,
    *,
    key: str,
    source_type: MemorySourceType = MemorySourceType.USER_STATEMENT,
    memory_type: DurableMemoryType = DurableMemoryType.SEMANTIC,
    refs: tuple[str, ...] = ("signal:test",),
    confidence: float = 0.9,
    importance: float = 0.8,
) -> MemoryCandidate:
    return MemoryCandidate(
        content=content,
        canonical_key=key,
        memory_type=memory_type,
        source_type=source_type,
        source_refs=refs,
        confidence=confidence,
        importance=importance,
    )


class DurableMemoryTests(unittest.TestCase):
    def test_restart_persistence_entity_isolation_and_no_transcript_dependency(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "echo.sqlite3"
            first_repository = SQLiteMemoryRepository(database)
            first = MemoryService("bit", first_repository)
            decision = first.commit(
                candidate(
                    "Tucker's neighborhood has a dog park",
                    key="neighborhood.dog_park",
                )
            )
            self.assertEqual(decision.action, MemoryCommitAction.COMMITTED)
            first_repository.close()

            second_repository = SQLiteMemoryRepository(database)
            restored = MemoryService("bit", second_repository)
            other = MemoryService("other", second_repository)

            self.assertEqual(
                restored.query("What is in Tucker's neighborhood?")[0].content,
                "Tucker's neighborhood has a dog park",
            )
            self.assertEqual(other.list(), ())
            second_repository.close()

    def test_provenance_deduplication_supersession_and_capability_update(self) -> None:
        service = MemoryService("bit")
        first = service.commit(
            candidate("Bit has no body connected", key="capability.body")
        )
        duplicate = service.commit(
            candidate(
                "Bit has no body connected",
                key="capability.body",
                refs=("signal:repeat",),
            )
        )
        updated = service.commit(
            candidate(
                "Bit has a body connected",
                key="capability.body",
                refs=("signal:update",),
            )
        )

        self.assertEqual(first.action, MemoryCommitAction.COMMITTED)
        self.assertEqual(duplicate.action, MemoryCommitAction.MERGED)
        self.assertEqual(updated.action, MemoryCommitAction.COMMITTED)
        active = service.list(status="active")
        historical = service.list(status="superseded")
        self.assertEqual([record.content for record in active], ["Bit has a body connected"])
        self.assertEqual([record.content for record in historical], ["Bit has no body connected"])
        self.assertEqual(active[0].source_type, MemorySourceType.USER_STATEMENT)
        self.assertEqual(active[0].source_refs, ("signal:update",))
        self.assertEqual(active[0].supersedes_id, historical[0].id)

    def test_character_promotion_is_conservative_and_model_knowledge_is_rejected(self) -> None:
        service = MemoryService("bit")
        casual = service.commit(
            candidate(
                "Bit is permanently grumpy",
                key="character.mood",
                memory_type=DurableMemoryType.CHARACTER,
            )
        )
        reflected = service.commit(
            candidate(
                "Bit values firsthand experimentation",
                key="character.experimentation",
                memory_type=DurableMemoryType.CHARACTER,
                source_type=MemorySourceType.REFLECTION,
                refs=("memory:one", "memory:two"),
            )
        )
        background = service.commit(
            candidate(
                "Dogs often use play bows as a social signal",
                key="dogs.play_bow",
                source_type=MemorySourceType.MODEL_KNOWLEDGE,
            )
        )

        self.assertEqual(casual.action, MemoryCommitAction.DEFERRED)
        self.assertEqual(reflected.action, MemoryCommitAction.COMMITTED)
        self.assertEqual(background.action, MemoryCommitAction.REJECTED)

    def test_direct_experience_requires_trusted_core_authority(self) -> None:
        service = MemoryService("bit")
        experience = candidate(
            "Bit interacted with a golden retriever",
            key="experience.golden_retriever",
            source_type=MemorySourceType.DIRECT_EXPERIENCE,
            refs=("signal:sensor-observation",),
        )
        rejected = service.commit(experience)
        accepted = service.commit(experience, trusted_provenance=True)

        self.assertEqual(rejected.action, MemoryCommitAction.REJECTED)
        self.assertEqual(accepted.action, MemoryCommitAction.COMMITTED)

    def test_archive_delete_and_missing_memory_are_safe(self) -> None:
        service = MemoryService("bit")
        self.assertEqual(service.query("nothing"), ())
        record = service.commit(candidate("The lab is quiet", key="lab.noise")).record
        assert record is not None
        self.assertTrue(service.archive(record.id))
        self.assertEqual(service.query("lab"), ())
        self.assertTrue(service.delete(record.id))
        self.assertIsNone(service.get(record.id))

    def test_phase6_consolidation_commits_through_durable_service(self) -> None:
        service = MemoryService("bit")
        entity = Entity("bit", memory_service=service)
        evidence = []
        for label in ("quiet workspace", "quiet workspace again"):
            episode = EpisodicMemory(content={"event": label}, source="test")
            entity.consider_memory(
                episode,
                MemoryImportance(
                    novelty=1,
                    surprise=1,
                    state_significance=1,
                    relationship_significance=1,
                    goal_relevance=1,
                    future_usefulness=1,
                    repetition=1,
                    unresolved_importance=1,
                ),
            )
            evidence.append(episode.id)

        decision = entity.consolidate_memory(
            MemoryConsolidationProposal(
                key="workspace.noise",
                target_kind=MemoryKind.SEMANTIC,
                content={"workspace": "quiet"},
                evidence_ids=tuple(evidence),
                confidence=0.9,
            )
        )

        self.assertTrue(decision.accepted)
        durable = service.list(memory_type="semantic")
        self.assertEqual(len(durable), 1)
        self.assertEqual(durable[0].canonical_key, "semantic:workspace.noise")
        self.assertEqual(durable[0].source_type, MemorySourceType.REFLECTION)

    def test_runtime_service_inspects_archives_and_deletes_memory(self) -> None:
        memory_service = MemoryService("bit")
        entity = Entity("bit", memory_service=memory_service)
        record = memory_service.commit(
            candidate("The workshop has a charger", key="workshop.charger")
        ).record
        assert record is not None
        service = RuntimeService(Runtime([entity]))

        listed = service.get_durable_memories("bit", status="active")
        self.assertEqual(listed.records, (record,))
        self.assertEqual(
            service.inspect_durable_memory("bit", record.id).source_refs,
            ("signal:test",),
        )
        archived = service.archive_durable_memory("bit", record.id)
        self.assertEqual(archived.status, DurableMemoryStatus.ARCHIVED)
        service.delete_durable_memory("bit", record.id)
        self.assertEqual(service.get_durable_memories("bit").records, ())


class DurableMemoryChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_signal_path_survives_restart_and_provider_swap(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "echo.sqlite3"
            first_repository = SQLiteMemoryRepository(database)
            remote = MockProvider(provider_id="openrouter", response="That sounds lovely.")
            first_entity = Entity(
                "bit",
                memory_service=MemoryService("bit", first_repository),
            )
            register_user_message_handler(first_entity, ProviderRouter(remote=remote))
            first_runtime = Runtime([first_entity])
            await first_runtime.emit(
                Signal(
                    type="UserMessage",
                    source="console",
                    payload={
                        "text": (
                            "My neighborhood is dog friendly. It has lots of "
                            "trees, walking paths, a pool, and a dog park."
                        )
                    },
                )
            )
            await first_runtime.emit(
                Signal(
                    type="UserMessage",
                    payload={
                        "text": (
                            "When you have a body, I want our first day to "
                            "include walking around the neighborhood, riding "
                            "in the car, and probably going to a store."
                        )
                    },
                )
            )
            await first_runtime.emit(
                Signal(
                    type="UserMessage",
                    payload={
                        "text": (
                            "Right now you're still pure chat. You don't have "
                            "internet tools, a body, battery monitoring, or "
                            "physical sensors connected yet."
                        )
                    },
                )
            )
            self.assertGreaterEqual(len(first_entity.durable_memories), 9)
            first_runtime.stop()
            first_repository.close()

            second_repository = SQLiteMemoryRepository(database)
            local = MockProvider(provider_id="offline", response="It has a dog park.")
            fresh_entity = Entity(
                "bit",
                memory_service=MemoryService("bit", second_repository),
            )
            register_user_message_handler(
                fresh_entity,
                ProviderRouter(offline=local, mode="offline"),
            )
            fresh_runtime = Runtime([fresh_entity])
            await fresh_runtime.emit(
                Signal(
                    type="UserMessage",
                    source="fresh-console",
                    payload={"text": "What do you remember about my neighborhood?"},
                )
            )

            context = local.requests[0].context
            contents = [memory["content"] for memory in context["memories"]]
            self.assertTrue(any("dog park" in value for value in contents))
            self.assertEqual(fresh_runtime.signals[0].source, "fresh-console")
            self.assertIn("personal history", local.requests[0].instructions)

            await fresh_runtime.emit(
                Signal(
                    type="UserMessage",
                    payload={
                        "text": (
                            "What did I say I wanted us to do on your first "
                            "embodied day?"
                        )
                    },
                )
            )
            first_day_memories = [
                memory["content"]
                for memory in local.requests[1].context["memories"]
            ]
            self.assertTrue(
                any("first day" in value for value in first_day_memories)
            )

            await fresh_runtime.emit(
                Signal(
                    type="UserMessage",
                    payload={"text": "What are you currently capable of doing?"},
                )
            )
            capability_memories = [
                memory["content"]
                for memory in local.requests[2].context["memories"]
            ]
            self.assertTrue(
                any(
                    "does not have" in value or "pure chat" in value
                    for value in capability_memories
                )
            )

            await fresh_runtime.emit(
                Signal(
                    type="UserMessage",
                    payload={"text": "The pool is gone now."},
                )
            )
            active_contents = [
                record.content
                for record in fresh_entity.memory_service.list(status="active")
            ]
            self.assertIn("The pool is gone now", active_contents)
            self.assertNotIn("The user's neighborhood has a pool", active_contents)

            await fresh_runtime.emit(
                Signal(
                    type="UserMessage",
                    payload={"text": "What is my neighborhood like?"},
                )
            )
            corrected_context = [
                memory["content"]
                for memory in local.requests[4].context["memories"]
            ]
            self.assertIn("The pool is gone now", corrected_context)
            self.assertNotIn(
                "The user's neighborhood has a pool",
                corrected_context,
            )
            second_repository.close()

    async def test_persistence_failure_does_not_destroy_response_path(self) -> None:
        class FailingRepository:
            def get(self, entity_id, memory_id): return None
            def list_for_entity(self, entity_id, **filters): return ()
            def commit(self, record, *, supersede_ids=()): raise OSError("disk unavailable")
            def archive(self, entity_id, memory_id): return False
            def delete(self, entity_id, memory_id): return False
            def record_access(self, entity_id, memory_ids, accessed_at): return None

        provider = MockProvider(response="Normal response")
        entity = Entity(
            "bit",
            memory_service=MemoryService("bit", FailingRepository()),
        )
        register_user_message_handler(entity, ProviderRouter(remote=provider))
        runtime = Runtime([entity])
        signal = Signal(
            type="UserMessage",
            payload={"text": "My neighborhood has a dog park."},
        )

        with self.assertLogs("echo.host", level="ERROR"):
            await runtime.emit(signal)

        self.assertEqual(
            runtime.filter_actions(signal_id=signal.id)[0].parameters["text"],
            "Normal response",
        )
        errors = runtime.latest_logs(event_type="error")
        self.assertEqual(errors[0].metadata["operation"], "memory.commit")
        self.assertTrue(errors[0].metadata["recoverable"])

    async def test_context_access_is_bounded_and_recorded(self) -> None:
        service = MemoryService("bit", max_results=2)
        for index in range(4):
            service.commit(candidate(f"Neighborhood detail {index}", key=f"neighborhood.{index}"))
        entity = Entity("bit", memory_service=service)

        context = CharacterContextBuilder().build(
            entity,
            CharacterContextRequest(
                situation="neighborhood",
                max_memories=2,
            ),
        )

        self.assertEqual(len(context.memories), 2)
        self.assertTrue(
            all(
                service.get(memory["id"]).access_count == 1
                for memory in context.memories
            )
        )


if __name__ == "__main__":
    unittest.main()
