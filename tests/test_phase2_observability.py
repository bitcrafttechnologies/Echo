from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    ActionStatus,
    CharacterMutationAuditRecord,
    CharacterMutationDecision,
    CharacterMutationTarget,
    Entity,
    EntityIdentity,
    InMemoryLogSink,
    Runtime,
    RuntimeEventType,
    SelfModel,
    Signal,
    TaskStatus,
    TraitProfile,
)


class Phase2CharacterTests(unittest.TestCase):
    def test_entity_composes_protected_phase2_character_state(self) -> None:
        identity = EntityIdentity(
            entity_id="bit",
            name="Bit",
            entity_type="embodied_companion",
            presentation="boyish_neutral",
            worldview="Understanding creates opportunities to improve.",
            core_values=("curiosity", "human_agency"),
        )
        traits = TraitProfile({"curiosity": 0.82, "optimism": 0.75})
        self_model = SelfModel(entity_id="bit", embodiment_id="bit_v1")
        bit = Entity(
            "bit",
            identity=identity,
            traits=traits,
            self_model=self_model,
        )

        with self.assertRaises(FrozenInstanceError):
            bit.identity.name = "Other"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            bit.traits.values["curiosity"] = 0.1  # type: ignore[index]
        with self.assertRaises(AttributeError):
            bit.identity = identity  # type: ignore[misc]
        with self.assertRaises(AttributeError):
            bit.id = "other"  # type: ignore[misc]
        with self.assertRaisesRegex(AttributeError, "entity_id is immutable"):
            bit.self_model.entity_id = "other"

        bit.self_model.embodiment_id = "bit_v2"
        self.assertEqual(bit.id, "bit")
        self.assertEqual(bit.identity.entity_id, "bit")
        self.assertEqual(bit.self_model.entity_id, "bit")
        self.assertEqual(bit.self_model.embodiment_id, "bit_v2")

    def test_entity_rejects_character_identity_mismatches(self) -> None:
        other_identity = EntityIdentity(
            entity_id="other",
            name="Other",
            entity_type="entity",
            presentation="unspecified",
            worldview="",
            core_values=(),
        )
        with self.assertRaisesRegex(ValueError, "identity entity_id"):
            Entity("bit", identity=other_identity)
        with self.assertRaisesRegex(ValueError, "self-model entity_id"):
            Entity("bit", self_model=SelfModel(entity_id="other"))

    def test_character_mutation_audit_vocabulary_is_structured(self) -> None:
        audit = CharacterMutationAuditRecord(
            entity_id="bit",
            target=CharacterMutationTarget.TRAITS,
            decision=CharacterMutationDecision.REJECTED,
            source="reflection-provider",
            reason="insufficient repeated evidence",
            evidence_ids=("signal-1",),
            before={"curiosity": 0.82},
            after={"curiosity": 0.82},
            metadata={"requested_delta": 0.4},
        )

        data = audit.to_dict()
        self.assertEqual(data["target"], "traits")
        self.assertEqual(data["decision"], "rejected")
        self.assertEqual(data["evidence_ids"], ["signal-1"])
        json.dumps(data)


class Phase2ObservabilityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_causal_chain_is_coherent_across_all_observability(self) -> None:
        identity = EntityIdentity(
            entity_id="bit",
            name="Bit",
            entity_type="embodied_companion",
            presentation="boyish_neutral",
            worldview="Understanding creates opportunities to improve.",
            core_values=("curiosity", "human_agency"),
        )
        bit = Entity(
            "bit",
            state={"battery": 1.0},
            identity=identity,
            traits=TraitProfile({"curiosity": 0.82}),
            self_model=SelfModel(
                entity_id="bit",
                embodiment_id="bit_v1",
                capabilities={"speech": True},
            ),
        )
        sink = InMemoryLogSink()
        runtime = Runtime([bit], log_sink=sink)

        @bit.on("battery.low")
        async def battery_low(signal: Signal):
            bit.state["battery"] = signal.payload["percent"]
            return await bit.action("speak", text="I should charge soon.")

        signal = Signal(
            type="battery.low",
            source="battery",
            payload={"percent": 0.08},
        )
        await runtime.emit(signal)

        task = runtime.tasks[0]
        action = runtime.actions[0]
        signal_entry = runtime.get_signal(signal.id)
        task_entry = runtime.get_task(task.id)
        action_entry = runtime.get_action(action.id)
        assert signal_entry is not None
        assert task_entry is not None
        assert action_entry is not None

        self.assertEqual(signal_entry.routing_result.entity_ids, (bit.id,))
        self.assertEqual(signal_entry.routing_result.task_ids, (task.id,))
        self.assertEqual(task_entry.status, TaskStatus.COMPLETED)
        self.assertEqual(task_entry.signal_id, signal.id)
        self.assertEqual(action_entry.status, ActionStatus.EXECUTED)
        self.assertEqual(action_entry.task_id, task.id)
        self.assertEqual(action_entry.signal_id, signal.id)
        self.assertEqual(action_entry.entity_id, bit.id)

        expected_counts = {
            RuntimeEventType.SIGNAL_RECEIVED: 1,
            RuntimeEventType.SIGNAL_ROUTED: 1,
            RuntimeEventType.TASK_CREATED: 1,
            RuntimeEventType.TASK_STATUS_CHANGED: 2,
            RuntimeEventType.ACTION_CREATED: 1,
            RuntimeEventType.ACTION_EXECUTED: 1,
            RuntimeEventType.STATE_CHANGED: 1,
        }
        for event_type, expected_count in expected_counts.items():
            self.assertEqual(len(sink.query(event_type=event_type)), expected_count)

        routed = sink.query(event_type=RuntimeEventType.SIGNAL_ROUTED)[0]
        created_task = sink.query(event_type=RuntimeEventType.TASK_CREATED)[0]
        task_status_logs = sink.query(
            event_type=RuntimeEventType.TASK_STATUS_CHANGED,
            task_id=task.id,
        )
        state_changed = sink.query(event_type=RuntimeEventType.STATE_CHANGED)[0]
        action_logs = sink.query(action_id=action.id)
        self.assertEqual(routed.signal_id, signal.id)
        self.assertEqual(routed.entity_id, bit.id)
        self.assertEqual(created_task.signal_id, signal.id)
        self.assertEqual(created_task.task_id, task.id)
        self.assertTrue(
            all(
                event.signal_id == signal.id
                and event.task_id == task.id
                and event.entity_id == bit.id
                for event in task_status_logs
            )
        )
        self.assertEqual(state_changed.signal_id, signal.id)
        self.assertEqual(state_changed.task_id, task.id)
        self.assertTrue(
            all(
                event.signal_id == signal.id
                and event.task_id == task.id
                and event.entity_id == bit.id
                for event in action_logs
            )
        )

        snapshot = runtime.inspect(recent_limit=10)
        self.assertEqual(snapshot["runtime_status"], "idle")
        self.assertEqual(snapshot["entity_state"]["bit"]["battery"], 0.08)
        self.assertEqual(snapshot["active_tasks"], [])
        self.assertEqual(snapshot["recent_signals"][0]["id"], signal.id)
        self.assertEqual(
            snapshot["recent_signals"][0]["routing_result"]["task_ids"],
            [task.id],
        )
        self.assertEqual(snapshot["recent_actions"][0]["id"], action.id)
        self.assertEqual(snapshot["recent_actions"][0]["task_id"], task.id)
        self.assertEqual(snapshot["recent_actions"][0]["signal_id"], signal.id)
        self.assertEqual(
            snapshot["entity_character"]["bit"]["identity"]["name"],
            "Bit",
        )
        self.assertEqual(
            snapshot["entity_character"]["bit"]["traits"]["curiosity"],
            0.82,
        )
        self.assertEqual(
            snapshot["entity_character"]["bit"]["self_model"]["embodiment_id"],
            "bit_v1",
        )
        json.dumps(snapshot, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
