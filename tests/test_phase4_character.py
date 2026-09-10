from __future__ import annotations

import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    Entity,
    RelationshipState,
    RelationshipStore,
    ResourceNotFoundError,
    Runtime,
    RuntimeService,
)


class Phase4CharacterTests(unittest.TestCase):
    def setUp(self) -> None:
        relationship = RelationshipState(
            "nathan",
            familiarity=0.75,
            trust=0.8,
            interaction_count=12,
            communication_preferences={"detail": "concise"},
            known_interests={"robotics", "runtime design"},
            boundaries={"confirm destructive actions"},
            important_memory_ids=["memory-1"],
            current_context={"project": "Echo", "phase": "4E"},
        )
        self.bit = Entity(
            "bit", relationships=RelationshipStore({"nathan": relationship})
        )
        self.service = RuntimeService(Runtime([self.bit]))

    def test_entity_owns_detached_per_person_social_state(self) -> None:
        inspected = self.service.inspect_relationship("bit", "nathan")
        inspected.current_context["phase"] = "changed"
        inspected.known_interests.add("mutation")

        retained = self.service.inspect_relationship("bit", "nathan")
        self.assertEqual(retained.current_context["phase"], "4E")
        self.assertNotIn("mutation", retained.known_interests)
        self.assertEqual(self.bit.inspect_character()["relationships"]["nathan"]["trust"], 0.8)

        source = RelationshipStore(
            {"person": RelationshipState("person", current_context={"topic": "before"})}
        )
        entity = Entity("copy-test", relationships=source)
        source.get_or_create("person").current_context["topic"] = "after"
        self.assertEqual(
            entity.inspect_relationship("person").current_context["topic"],
            "before",
        )

    def test_relationships_remain_scoped_and_inspectable(self) -> None:
        self.assertEqual(
            [item.subject_id for item in self.service.get_relationships("bit")],
            ["nathan"],
        )
        with self.assertRaises(ResourceNotFoundError):
            self.service.inspect_relationship("bit", "missing")

    def test_invalid_relationship_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RelationshipState("person", trust=1.1)
        with self.assertRaises(ValueError):
            RelationshipState("person", interaction_count=-1)


if __name__ == "__main__":
    unittest.main()
