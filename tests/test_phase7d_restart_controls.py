from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
except ImportError:
    TestClient = None
    WebSocketDisconnect = Exception

from echo import (
    CharacterContextBuilder,
    CharacterContextRequest,
    CharacterMemory,
    Entity,
    EntityIdentity,
    GracefulRestartCoordinator,
    InMemoryStateStore,
    InternalState,
    MemoryKind,
    RelationshipState,
    RelationshipStore,
    RestartRequest,
    Runtime,
    RuntimeService,
    RuntimeSubscriptionRequest,
    SemanticMemory,
    Signal,
    StateCategory,
    TraitProfile,
    WorkingMemory,
)


class RestartControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_control_tracks_restart_and_reconnects_to_fresh_events(self) -> None:
        store = InMemoryStateStore()
        old_entity = Entity("bit", state_store=store)
        old_entity.set_state("identity", "continuous", category="persistent")
        old_entity.set_state("scratch", "expires", category="session")
        old_runtime = Runtime([old_entity])

        def build_fresh() -> Runtime:
            return Runtime([Entity("bit", state_store=store)])

        coordinator = GracefulRestartCoordinator(old_runtime, build_fresh)
        service = RuntimeService(
            old_runtime,
            restart_coordinator=coordinator,
        )
        old_subscription = service.subscribe_events(
            RuntimeSubscriptionRequest(categories=("logs",))
        )

        operation = await service.request_restart(
            RestartRequest(
                reason="console requested",
                confirmation="RESTART",
                preserve_state=True,
            )
        )
        while operation.status not in {"completed", "failed"}:
            await asyncio.sleep(0)
            operation = service.get_restart_operation(operation.operation_id)

        self.assertEqual(operation.status, "completed")
        self.assertTrue(operation.state_preservation_enabled)
        self.assertNotEqual(operation.previous_runtime_id, operation.new_runtime_id)
        self.assertTrue(old_subscription.closed)
        self.assertIs(service.runtime, coordinator.runtime)
        entity = service.runtime.get_entity("bit")
        self.assertEqual(
            entity.get_state("identity", category=StateCategory.PERSISTENT),
            "continuous",
        )
        self.assertIsNone(entity.get_state("scratch"))

        reconnected = service.subscribe_events(
            RuntimeSubscriptionRequest(categories=("signal.received",))
        )
        signal = Signal(type="console.reconnected")
        await service.runtime.emit(signal)
        event = await asyncio.wait_for(reconnected.get(), timeout=1)
        self.assertEqual(event.event.signal_id, signal.id)
        reconnected.close()

    async def test_control_requires_confirmation_and_preservation(self) -> None:
        runtime = Runtime([Entity("bit")])
        coordinator = GracefulRestartCoordinator(
            runtime, lambda: Runtime([Entity("bit")])
        )
        service = RuntimeService(runtime, restart_coordinator=coordinator)

        for confirmation, preserve_state in (("restart", True), ("RESTART", False)):
            with self.assertRaisesRegex(Exception, "confirmation|preservation"):
                await service.request_restart(
                    RestartRequest(
                        reason="test",
                        confirmation=confirmation,
                        preserve_state=preserve_state,
                    )
                )

    async def test_control_exposes_progress_and_rejects_overlap(self) -> None:
        runtime = Runtime([Entity("bit")])
        started = asyncio.Event()
        release = asyncio.Event()

        @runtime.get_entity("bit").on("long.work")
        async def long_work(signal: Signal) -> None:
            started.set()
            await release.wait()

        emission = asyncio.create_task(runtime.emit(Signal(type="long.work")))
        await started.wait()
        coordinator = GracefulRestartCoordinator(
            runtime,
            lambda: Runtime([Entity("bit")]),
            task_grace_seconds=1,
        )
        service = RuntimeService(runtime, restart_coordinator=coordinator)
        request = RestartRequest(
            reason="progress test",
            confirmation="RESTART",
            preserve_state=True,
        )

        operation = await service.request_restart(request)
        self.assertEqual(operation.status, "settling_tasks")
        with self.assertRaisesRegex(Exception, "already in progress"):
            await service.request_restart(request)

        release.set()
        await emission
        while operation.status != "completed":
            await asyncio.sleep(0)
            operation = service.get_restart_operation(operation.operation_id)
        self.assertEqual(operation.status, "completed")


@unittest.skipIf(TestClient is None, "install the test extra to test HTTP")
class RestartHttpTests(unittest.TestCase):
    def test_restart_endpoint_requires_deliberate_body(self) -> None:
        from echo.adapters.fastapi import create_app

        client = TestClient(create_app(RuntimeService(Runtime([Entity("bit")]))))
        accidental = client.post(
            "/runtime/restart",
            json={
                "reason": "clicked",
                "confirmation": "restart",
                "preserve_state": True,
            },
        )
        self.assertEqual(accidental.status_code, 422)

        deliberate = client.post(
            "/runtime/restart",
            json={
                "reason": "developer request",
                "confirmation": "RESTART",
                "preserve_state": True,
            },
        )
        self.assertEqual(deliberate.status_code, 503)
        self.assertEqual(
            deliberate.json()["error"]["code"], "restart_unavailable"
        )

    def test_restart_endpoint_returns_trackable_completed_operation(self) -> None:
        from echo.adapters.fastapi import create_app

        store = InMemoryStateStore()
        runtime = Runtime([Entity("bit", state_store=store)])
        runtime.get_entity("bit").set_state(
            "continuity", "kept", category="persistent"
        )
        coordinator = GracefulRestartCoordinator(
            runtime,
            lambda: Runtime([Entity("bit", state_store=store)]),
        )
        service = RuntimeService(runtime, restart_coordinator=coordinator)
        with TestClient(create_app(service)) as client:
            with client.websocket_connect("/events") as socket:
                response = client.post(
                    "/runtime/restart",
                    json={
                        "reason": "API verification",
                        "confirmation": "RESTART",
                        "preserve_state": True,
                    },
                )
                self.assertEqual(
                    socket.receive_json()["event"]["event_type"],
                    "runtime.quiescing",
                )
                self.assertEqual(
                    socket.receive_json()["event"]["event_type"],
                    "runtime.stopped",
                )
                with self.assertRaises(WebSocketDisconnect) as closed:
                    socket.receive_json()
                self.assertEqual(closed.exception.code, 1012)
            self.assertEqual(response.status_code, 202)
            operation = response.json()
            inspected = client.get(
                f"/runtime/restart/{operation['operation_id']}"
            )
            self.assertEqual(inspected.status_code, 200)
            self.assertEqual(inspected.json()["status"], "completed")
            self.assertTrue(inspected.json()["state_preservation_enabled"])
            self.assertEqual(
                client.get("/runtime/status").json()["runtime_id"],
                inspected.json()["new_runtime_id"],
            )
            with client.websocket_connect(
                "/events?category=signal.received"
            ) as reconnected:
                signal = client.post(
                    "/signals", json={"type": "console.reconnected"}
                )
                self.assertEqual(signal.status_code, 201)
                self.assertEqual(
                    reconnected.receive_json()["event"]["event_type"],
                    "signal.received",
                )


class CharacterContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_builder_selects_compact_relevant_provider_neutral_context(self) -> None:
        memory = CharacterMemory(
            (
                SemanticMemory(
                    content={"fact": "Nathan likes telescopes"},
                    source="test",
                    metadata={"subject_id": "nathan"},
                ),
                WorkingMemory(
                    content={"topic": "unrelated groceries"},
                    source="test",
                ),
            )
        )
        entity = Entity(
            "bit",
            identity=EntityIdentity(
                entity_id="bit",
                name="Bit",
                entity_type="companion",
                presentation="curious",
                worldview="evidence matters",
                core_values=("care", "curiosity"),
            ),
            traits=TraitProfile({"curiosity": 0.9}),
            internal_state=InternalState(curiosity=0.8),
            relationships=RelationshipStore(
                {
                    "nathan": RelationshipState(
                        "nathan",
                        familiarity=0.8,
                        known_interests={"telescopes"},
                    ),
                    "alex": RelationshipState("alex", familiarity=0.2),
                }
            ),
            memory=memory,
        )
        entity.set_state("telescope_project", "mirror", category="persistent")
        entity.set_state("unrelated", "shopping", category="session")

        context = CharacterContextBuilder().build(
            entity,
            CharacterContextRequest(
                situation="Help Nathan with the telescope",
                subject_id="nathan",
                environment={"location": "observatory", "weather": "clear"},
                max_memories=1,
                max_state_items=1,
            ),
        ).to_dict()

        self.assertEqual(context["identity"]["name"], "Bit")
        self.assertEqual(context["traits"], {"curiosity": 0.9})
        self.assertIn("internal_state", context)
        self.assertIn("drives", context)
        self.assertIn("self_model", context)
        self.assertIn("goals", context)
        self.assertEqual(context["relationships"][0]["subject_id"], "nathan")
        self.assertEqual(context["memories"][0]["kind"], MemoryKind.SEMANTIC.value)
        self.assertEqual(
            context["state"]["persistent"], {"telescope_project": "mirror"}
        )
        self.assertEqual(sum(len(values) for values in context["state"].values()), 1)
        self.assertEqual(context["environment"]["location"], "observatory")

        from echo.host import _clone_entity_generation

        restarted = _clone_entity_generation(entity)
        self.assertEqual(restarted.identity, entity.identity)
        self.assertEqual(restarted.traits, entity.traits)
        self.assertEqual(restarted.internal_state, entity.internal_state)
        self.assertEqual(restarted.relationships, entity.relationships)
        self.assertEqual(restarted.memories, entity.memories)


if __name__ == "__main__":
    unittest.main()
