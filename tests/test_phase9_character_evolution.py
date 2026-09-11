from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from echo import (
    CharacterMutationDecision,
    Entity,
    TraitEvidence,
    TraitEvolutionPolicy,
    TraitEvolutionService,
    TraitProfile,
    TraitReflection,
)


def evidence(
    evidence_id: str,
    *,
    trait: str = "curiosity",
    direction: str = "increase",
    strength: float = 1.0,
    confidence: float = 1.0,
    observed_at: datetime | None = None,
) -> TraitEvidence:
    return TraitEvidence(
        trait=trait,
        evidence_id=evidence_id,
        direction=direction,  # type: ignore[arg-type]
        strength=strength,
        source="provider-reflection",
        confidence=confidence,
        observed_at=observed_at or datetime.now(timezone.utc),
    )


class Phase9CharacterEvolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entity = Entity(
            "bit",
            traits=TraitProfile({"curiosity": 0.70, "patience": 0.60}),
        )

    def test_repeated_evidence_applies_only_a_bounded_adjustment(self) -> None:
        decision = self.entity.reflect_on_traits(
            TraitReflection(
                trait="curiosity",
                evidence=(evidence("signal:one"), evidence("signal:two")),
                source="model-reflection",
            )
        )

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.decision, CharacterMutationDecision.ACCEPTED)
        self.assertAlmostEqual(decision.adjustment, 0.05)
        self.assertAlmostEqual(self.entity.traits.values["curiosity"], 0.75)
        audit = self.entity.trait_evolution.audit_records[-1]
        self.assertEqual(audit.evidence_ids, ("signal:one", "signal:two"))
        self.assertTrue(audit.reason)
        self.assertEqual(audit.before, {"curiosity": 0.70})
        self.assertEqual(audit.after, {"curiosity": 0.75})

    def test_inference_cannot_assign_or_request_an_oversized_trait_change(self) -> None:
        with self.assertRaises(TypeError):
            self.entity.traits.values["curiosity"] = 1.0  # type: ignore[index]
        with self.assertRaises(AttributeError):
            self.entity.traits = TraitProfile({"curiosity": 1.0})  # type: ignore[misc]
        with self.assertRaises(TypeError):
            TraitReflection(  # type: ignore[call-arg]
                trait="curiosity",
                evidence=(evidence("signal:one"), evidence("signal:two")),
                requested_adjustment=0.30,
            )

    def test_rejections_are_safe_and_retain_evidence_and_reason(self) -> None:
        insufficient = self.entity.reflect_on_traits(
            TraitReflection(
                trait="curiosity",
                evidence=(evidence("signal:single"),),
            )
        )
        reused = self.entity.reflect_on_traits(
            TraitReflection(
                trait="curiosity",
                evidence=(evidence("signal:single"), evidence("signal:new")),
            )
        )

        self.assertFalse(insufficient.accepted)
        self.assertIn("repeated evidence", insufficient.reason)
        self.assertFalse(reused.accepted)
        self.assertIn("already been evaluated", reused.reason)
        self.assertEqual(self.entity.traits.values["curiosity"], 0.70)
        for record in self.entity.trait_evolution.audit_records:
            self.assertEqual(record.decision, CharacterMutationDecision.REJECTED)
            self.assertTrue(record.reason)
            self.assertTrue(record.evidence_ids)

    def test_stale_contradictory_unknown_and_protected_evidence_is_rejected(self) -> None:
        now = datetime.now(timezone.utc)
        service = TraitEvolutionService(
            "bit",
            self.entity.traits,
            policy=TraitEvolutionPolicy(mutable_traits=frozenset({"curiosity"})),
        )
        protected = service.consider(
            TraitReflection(
                trait="patience",
                evidence=(
                    evidence("p1", trait="patience"),
                    evidence("p2", trait="patience"),
                ),
            ),
            now=now,
        )
        stale = service.consider(
            TraitReflection(
                trait="curiosity",
                evidence=(
                    evidence("s1", observed_at=now - timedelta(days=31)),
                    evidence("s2", observed_at=now - timedelta(days=31)),
                ),
            ),
            now=now,
        )
        conflicting = service.consider(
            TraitReflection(
                trait="curiosity",
                evidence=(
                    evidence("c1", direction="increase"),
                    evidence("c2", direction="increase"),
                    evidence("c3", direction="decrease"),
                    evidence("c4", direction="decrease"),
                ),
            ),
            now=now,
        )
        unknown = service.consider(
            TraitReflection(
                trait="invented",
                evidence=(
                    evidence("u1", trait="invented"),
                    evidence("u2", trait="invented"),
                ),
            ),
            now=now,
        )

        self.assertIn("protected", protected.reason)
        self.assertIn("stale", stale.reason)
        self.assertIn("dominant direction", conflicting.reason)
        self.assertIn("authored profile", unknown.reason)
        self.assertEqual(service.profile, self.entity.traits)

    def test_evolution_and_audit_survive_entity_reconstruction(self) -> None:
        accepted = self.entity.reflect_on_traits(
            TraitReflection(
                trait="curiosity",
                evidence=(evidence("r1"), evidence("r2")),
            )
        )
        copied = self.entity.copy_for_restart()

        self.assertEqual(copied.id, "bit")
        self.assertEqual(copied.traits, self.entity.traits)
        self.assertEqual(
            copied.trait_evolution.audit_records,
            self.entity.trait_evolution.audit_records,
        )
        duplicate = copied.reflect_on_traits(
            TraitReflection(
                trait="curiosity",
                evidence=(evidence("r1"), evidence("r3")),
            )
        )
        self.assertTrue(accepted.accepted)
        self.assertFalse(duplicate.accepted)

    def test_inspection_is_detached_and_histories_are_bounded(self) -> None:
        service = TraitEvolutionService(
            "bit",
            TraitProfile({"curiosity": 0.5}),
            policy=TraitEvolutionPolicy(
                evidence_history_size=2,
                audit_history_size=1,
            ),
        )
        service.consider(
            TraitReflection(
                trait="curiosity",
                evidence=(evidence("b1"), evidence("b2")),
            )
        )
        service.consider(
            TraitReflection(
                trait="curiosity",
                evidence=(evidence("b3"),),
            )
        )

        self.assertEqual(len(service.evidence), 2)
        self.assertEqual(len(service.audit_records), 1)
        inspected = self.entity.inspect_character()
        inspected["traits"]["curiosity"] = 0.0
        self.assertEqual(self.entity.traits.values["curiosity"], 0.70)
        self.assertIn("trait_evolution", inspected)


if __name__ == "__main__":
    unittest.main()
