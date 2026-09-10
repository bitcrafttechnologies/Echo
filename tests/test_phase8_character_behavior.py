from __future__ import annotations

import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    ApplySignalInfluenceRequest,
    AttentionProposal,
    BehaviorContext,
    BehaviorOutcome,
    CuriosityGoalStatus,
    EmitSignalRequest,
    Entity,
    Intention,
    IntentionType,
    Runtime,
    RuntimeService,
    Signal,
    SignalInfluence,
    SignalPriority,
    Task,
)


class Phase8CharacterBehaviorTests(unittest.IsolatedAsyncioTestCase):
    def test_intentions_accept_only_safe_json_parameters(self) -> None:
        with self.assertRaisesRegex(ValueError, "safe JSON"):
            Intention(
                entity_id="bit",
                type=IntentionType.REMEMBER,
                parameters={"unsafe": object()},
            )

    def test_behavior_policy_gates_external_actions_without_executing_them(self) -> None:
        bit = Entity("bit")
        runtime = Runtime([bit])
        intention = Intention(
            entity_id="bit",
            type=IntentionType.MOVE,
            parameters={"destination": "charging-pad"},
        )

        rejected = bit.arbitrate_intention(
            intention,
            BehaviorContext(capabilities=frozenset({"movement"})),
        )
        self.assertEqual(rejected.outcome, BehaviorOutcome.REJECTED)
        self.assertIsNone(rejected.action)

        approved = bit.arbitrate_intention(
            intention,
            BehaviorContext(
                capabilities=frozenset({"movement"}),
                allow_external_actions=True,
            ),
        )
        self.assertEqual(approved.outcome, BehaviorOutcome.APPROVED)
        self.assertEqual(approved.action.type, "intention.move")  # type: ignore[union-attr]
        self.assertEqual(runtime.actions, [])

    def test_active_noninterruptible_work_defers_low_urgency_intention(self) -> None:
        bit = Entity("bit")
        decision = bit.arbitrate_intention(
            Intention(
                entity_id="bit",
                type=IntentionType.REMEMBER,
                parameters={"observation": "a recurring sound"},
                urgency=0.4,
            ),
            BehaviorContext(active_task=True, active_task_interruptible=False),
        )

        self.assertEqual(decision.outcome, BehaviorOutcome.DEFERRED)
        self.assertIn("not interruptible", decision.reason)
        self.assertIsNone(decision.action)

    async def test_attention_can_create_only_low_priority_curiosity_work(self) -> None:
        bit = Entity("bit")
        runtime = Runtime([bit])
        service = RuntimeService(runtime)
        signal = Signal(type="sensor.novel", source="camera")
        await service.emit_signal(EmitSignalRequest(signal=signal))

        service.apply_signal_influence(
            ApplySignalInfluenceRequest(
                entity_id="bit",
                signal_id=signal.id,
                influence=SignalInfluence(
                    attention=AttentionProposal(
                        subject="unfamiliar object",
                        reason="retain for a quiet-time investigation",
                        salience=0.9,
                    )
                ),
            )
        )

        goal = bit.behavior.curiosity_goals[0]
        self.assertEqual(goal.status, CuriosityGoalStatus.ACTIVE)
        self.assertGreaterEqual(goal.priority, int(SignalPriority.BACKGROUND))
        self.assertIn(goal.id, bit.self_model.active_goal_ids)
        self.assertEqual(runtime.actions, [])
        self.assertEqual(runtime.tasks, [])

        copied = bit.copy_for_restart()
        self.assertEqual(copied.behavior.to_dict(), bit.behavior.to_dict())
        self.assertEqual(
            copied.inspect_character()["behavior"]["curiosity_goals"][0]["id"],
            goal.id,
        )

    async def test_curiosity_is_deferred_while_entity_has_active_work(self) -> None:
        bit = Entity("bit")
        runtime = Runtime([bit])
        service = RuntimeService(runtime)
        task = Task(name="conversation", owner=bit.id)
        task.start()
        bit.active_tasks[task.id] = task
        signal = Signal(type="sensor.novel")
        await service.emit_signal(EmitSignalRequest(signal=signal))

        service.apply_signal_influence(
            ApplySignalInfluenceRequest(
                entity_id=bit.id,
                signal_id=signal.id,
                influence=SignalInfluence(
                    attention=AttentionProposal(
                        subject="unfamiliar sound",
                        reason="investigate after the conversation",
                        salience=0.9,
                    )
                ),
            )
        )

        goal = bit.behavior.curiosity_goals[0]
        self.assertEqual(goal.status, CuriosityGoalStatus.DEFERRED)
        self.assertNotIn(goal.id, bit.self_model.active_goal_ids)
        self.assertEqual(runtime.actions, [])


if __name__ == "__main__":
    unittest.main()
