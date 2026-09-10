from __future__ import annotations

import os
import sys
import unittest
from dataclasses import FrozenInstanceError


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo.entity import (
    DriveProfile,
    EntityIdentity,
    InternalState,
    RelationshipStore,
    SelfModel,
    TraitEvidence,
    TraitProfile,
)


class CharacterPrimitiveTests(unittest.TestCase):
    def test_identity_snapshot_is_immutable(self) -> None:
        identity = EntityIdentity(
            entity_id="bit",
            name="Bit",
            entity_type="embodied_companion",
            presentation="boyish_neutral",
            worldview="Understanding creates opportunities to improve.",
            core_values=("curiosity", "human_agency"),
        )

        with self.assertRaises(FrozenInstanceError):
            identity.name = "Other"  # type: ignore[misc]

    def test_trait_and_drive_values_are_bounded_and_read_only(self) -> None:
        traits = TraitProfile({"curiosity": 0.82})
        drives = DriveProfile({"learning": 0.75})

        with self.assertRaises(TypeError):
            traits.values["curiosity"] = 1.0  # type: ignore[index]
        with self.assertRaises(TypeError):
            drives.values["learning"] = 1.0  # type: ignore[index]
        with self.assertRaises(ValueError):
            TraitProfile({"curiosity": 1.1})

    def test_trait_evidence_is_a_proposal_not_a_mutation(self) -> None:
        evidence = TraitEvidence(
            trait="confidence",
            evidence_id="event-17",
            direction="increase",
            strength=0.08,
            source="reflection-provider",
        )

        self.assertEqual(evidence.evidence_id, "event-17")

    def test_internal_state_adjustment_clamps_values(self) -> None:
        state = InternalState(curiosity=0.9, resource_pressure=0.1)

        state.adjust(curiosity=0.3, resource_pressure=-0.5)

        self.assertEqual(state.curiosity, 1.0)
        self.assertEqual(state.resource_pressure, 0.0)
        with self.assertRaises(ValueError):
            InternalState(curiosity=1.1)

    def test_relationships_are_scoped_per_subject(self) -> None:
        store = RelationshipStore()

        nathan = store.get_or_create("nathan")
        stranger = store.get_or_create("stranger-1")
        nathan.interaction_count += 1

        self.assertEqual(store.get_or_create("nathan").interaction_count, 1)
        self.assertEqual(stranger.interaction_count, 0)

    def test_embodiment_can_change_without_changing_identity(self) -> None:
        model = SelfModel(entity_id="bit", embodiment_id="bit_v1")

        model.embodiment_id = "bit_v2"

        self.assertEqual(model.entity_id, "bit")
        self.assertEqual(model.embodiment_id, "bit_v2")


if __name__ == "__main__":
    unittest.main()
