from __future__ import annotations

import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (  # noqa: E402
    Entity,
    EpisodicMemory,
    MemoryConsolidationProposal,
    MemoryImportance,
    MemoryKind,
)


class Phase6CharacterMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bit = Entity("bit")

    def retain_episode(self, label: str) -> EpisodicMemory:
        episode = EpisodicMemory(content={"event": label}, source="test")
        decision = self.bit.consider_memory(
            episode,
            MemoryImportance(
                novelty=0.9,
                surprise=0.8,
                state_significance=0.7,
                relationship_significance=0.7,
                goal_relevance=0.8,
                future_usefulness=0.9,
                repetition=0.6,
                unresolved_importance=0.8,
            ),
        )
        self.assertTrue(decision.retained)
        return episode

    def test_insignificant_experience_expires_and_significant_one_stays(self) -> None:
        insignificant = EpisodicMemory(
            content={"event": "routine clock tick"}, source="test"
        )
        rejected = self.bit.consider_memory(
            insignificant, MemoryImportance(novelty=0.05)
        )
        significant = self.retain_episode("unexpected person returned")

        self.assertFalse(rejected.retained)
        self.assertNotIn(insignificant.id, {item.id for item in self.bit.memories})
        self.assertIn(significant.id, {item.id for item in self.bit.memories})
        audit = self.bit.inspect_character()["memory_audit"]["retention"]
        self.assertEqual([item["retained"] for item in audit], [True, False])

    def test_repeated_evidence_can_form_a_preference(self) -> None:
        first = self.retain_episode("selected quiet workspace")
        second = self.retain_episode("selected quiet workspace again")
        decision = self.bit.consolidate_memory(
            MemoryConsolidationProposal(
                key="workspace.noise",
                target_kind=MemoryKind.PREFERENCE,
                content={"prefers": "quiet workspace"},
                evidence_ids=(first.id, second.id),
                confidence=0.9,
            )
        )

        self.assertTrue(decision.accepted)
        preferences = self.bit.get_memories(MemoryKind.PREFERENCE)
        self.assertEqual(dict(preferences[0].content), {"prefers": "quiet workspace"})
        self.assertEqual(
            preferences[0].metadata["evidence_ids"], (first.id, second.id)
        )

    def test_consolidation_rejects_weak_scope_and_contradictions_with_audit(self) -> None:
        first = self.retain_episode("nathan prefers concise status")
        second = self.retain_episode("nathan repeats concise preference")
        missing_scope = self.bit.consolidate_memory(
            MemoryConsolidationProposal(
                key="communication.style",
                target_kind=MemoryKind.RELATIONSHIP,
                content={"style": "concise"},
                evidence_ids=(first.id, second.id),
                confidence=0.9,
            )
        )
        accepted = self.bit.consolidate_memory(
            MemoryConsolidationProposal(
                key="communication.style",
                target_kind=MemoryKind.RELATIONSHIP,
                content={"style": "concise"},
                evidence_ids=(first.id, second.id),
                confidence=0.9,
                subject_id="nathan",
            )
        )
        contradicted = self.bit.consolidate_memory(
            MemoryConsolidationProposal(
                key="communication.style",
                target_kind=MemoryKind.RELATIONSHIP,
                content={"style": "verbose"},
                evidence_ids=(first.id, second.id),
                confidence=0.95,
                subject_id="nathan",
            )
        )

        self.assertFalse(missing_scope.accepted)
        self.assertTrue(accepted.accepted)
        self.assertFalse(contradicted.accepted)
        self.assertIn("contradicts", contradicted.reason)
        audit = self.bit.inspect_character()["memory_audit"]["consolidation"]
        self.assertEqual([item["accepted"] for item in audit], [False, True, False])


if __name__ == "__main__":
    unittest.main()
