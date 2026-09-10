from __future__ import annotations

import asyncio
import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    ApplySignalInfluenceRequest,
    AttentionProposal,
    DriveProfile,
    EmitSignalRequest,
    Entity,
    InternalState,
    InvalidCharacterInfluenceError,
    ResourceNotFoundError,
    Runtime,
    RuntimeEventCategory,
    RuntimeService,
    RuntimeSubscriptionRequest,
    Signal,
    SignalInfluence,
)


class Phase3CharacterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bit = Entity(
            "bit",
            internal_state=InternalState(curiosity=0.8, urgency=0.1),
            drives=DriveProfile(
                {
                    "curiosity": 0.8,
                    "learning": 0.7,
                    "resource_conservation": 0.4,
                }
            ),
            drive_activations={"curiosity": 0.2},
        )
        self.runtime = Runtime([self.bit])
        self.service = RuntimeService(self.runtime)

    async def test_signal_influence_updates_controls_and_attention(self) -> None:
        subscription = self.service.subscribe_events(
            RuntimeSubscriptionRequest(
                categories=(RuntimeEventCategory.STATE_CHANGED,)
            )
        )
        signal = Signal(
            type="telemetry.anomaly",
            source="sensor",
            payload={"reading": 12.5},
        )
        await self.service.emit_signal(EmitSignalRequest(signal=signal))

        result = self.service.apply_signal_influence(
            ApplySignalInfluenceRequest(
                entity_id="bit",
                signal_id=signal.id,
                influence=SignalInfluence(
                    state_deltas={"curiosity": 0.3, "urgency": 0.2},
                    drive_deltas={"curiosity": 0.6},
                    attention=AttentionProposal(
                        subject="unexpected telemetry",
                        reason="reading departed from the recent pattern",
                        salience=0.6,
                        drive_names=("curiosity", "learning"),
                    ),
                ),
            )
        )

        self.assertEqual(result.internal_state["curiosity"], 1.0)
        self.assertAlmostEqual(result.internal_state["urgency"], 0.3)
        self.assertAlmostEqual(result.drive_activations["curiosity"], 0.8)
        candidate = result.attention_candidate
        assert candidate is not None
        self.assertEqual(candidate.signal_id, signal.id)
        self.assertGreater(candidate.score, candidate.salience * 0.75)
        self.assertAlmostEqual(candidate.drive_contributions["curiosity"], 0.64)

        character = self.service.get_character_state("bit")
        self.assertEqual(character.attention_candidates[0].id, candidate.id)
        self.assertEqual(
            self.service.get_attention_candidates("bit", limit=1)[0].id,
            candidate.id,
        )
        self.assertEqual(self.runtime.actions, [])
        self.assertEqual(self.runtime.tasks, [])

        event = await subscription.get()
        self.assertEqual(event.event.signal_id, signal.id)
        self.assertEqual(event.event.metadata["operation"], "signal_influence")
        inspected_character = self.runtime.inspect()["entity_character"]["bit"]
        self.assertEqual(
            inspected_character["attention_candidates"][0]["id"],
            candidate.id,
        )
        subscription.close()

    async def test_character_views_are_detached_and_read_only(self) -> None:
        state_snapshot = self.bit.internal_state
        state_snapshot.adjust(curiosity=-0.5)
        self.assertEqual(self.bit.internal_state.curiosity, 0.8)

        with self.assertRaises(TypeError):
            self.bit.drive_activations["curiosity"] = 1.0  # type: ignore[index]

        result = self.service.get_character_state("bit")
        result.internal_state["curiosity"] = 0.0
        result.drive_activations["curiosity"] = 0.0
        self.assertEqual(self.bit.internal_state.curiosity, 0.8)
        self.assertEqual(self.bit.drive_activations["curiosity"], 0.2)

    async def test_attention_candidate_does_not_interrupt_active_work(self) -> None:
        anomaly = Signal(type="telemetry.unexpected")
        await self.service.emit_signal(EmitSignalRequest(signal=anomaly))
        started = asyncio.Event()
        release = asyncio.Event()

        @self.bit.on("conversation.active")
        async def conversation(signal: Signal) -> None:
            started.set()
            await release.wait()

        active = asyncio.create_task(
            self.service.emit_signal(
                EmitSignalRequest(signal=Signal(type="conversation.active"))
            )
        )
        await started.wait()

        result = self.service.apply_signal_influence(
            ApplySignalInfluenceRequest(
                entity_id="bit",
                signal_id=anomaly.id,
                influence=SignalInfluence(
                    attention=AttentionProposal(
                        subject="unexpected telemetry",
                        reason="retain for later investigation",
                        salience=0.7,
                        drive_names=("curiosity",),
                    )
                ),
            )
        )

        self.assertIsNotNone(result.attention_candidate)
        self.assertFalse(active.done())
        self.assertEqual(len(self.bit.active_tasks), 1)
        self.assertEqual(self.runtime.actions, [])
        release.set()
        await active

    async def test_invalid_influence_is_atomic_and_returns_domain_error(self) -> None:
        signal = Signal(type="telemetry.anomaly")
        await self.service.emit_signal(EmitSignalRequest(signal=signal))
        before = self.service.get_character_state("bit")

        with self.assertRaises(InvalidCharacterInfluenceError) as invalid:
            self.service.apply_signal_influence(
                ApplySignalInfluenceRequest(
                    entity_id="bit",
                    signal_id=signal.id,
                    influence=SignalInfluence(
                        state_deltas={"unknown": 0.2},
                        drive_deltas={"curiosity": 0.5},
                    ),
                )
            )

        self.assertEqual(invalid.exception.code, "invalid_character_influence")
        after = self.service.get_character_state("bit")
        self.assertEqual(after.internal_state, before.internal_state)
        self.assertEqual(after.drive_activations, before.drive_activations)

        with self.assertRaises(ResourceNotFoundError):
            self.service.apply_signal_influence(
                ApplySignalInfluenceRequest(
                    entity_id="bit",
                    signal_id="missing",
                    influence=SignalInfluence(state_deltas={"urgency": 0.1}),
                )
            )


if __name__ == "__main__":
    unittest.main()
