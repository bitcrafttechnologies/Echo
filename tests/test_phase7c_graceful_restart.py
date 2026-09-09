from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    Entity,
    GracefulRestartCoordinator,
    InferenceRequest,
    InMemoryStateStore,
    MockProvider,
    ProviderNotAcceptingRequestsError,
    ProviderRouter,
    RestartStatus,
    RestartTarget,
    Runtime,
    RuntimeEventType,
    RuntimeNotAcceptingWorkError,
    RuntimeService,
    SQLiteStateStore,
    Signal,
    StateCategory,
    TaskStatus,
)


class ClosableResource:
    def __init__(self) -> None:
        self.quiesced = False
        self.closed = False

    def quiesce(self) -> None:
        self.quiesced = True

    async def aclose(self) -> None:
        self.closed = True


class ClosableMockProvider(MockProvider):
    def __init__(self) -> None:
        super().__init__(provider_id="closable")
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class GracefulRestartTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_router_quiesces_and_closes_providers(self) -> None:
        provider = ClosableMockProvider()
        router = ProviderRouter(remote=provider)

        router.quiesce()
        with self.assertRaises(ProviderNotAcceptingRequestsError):
            await router.infer(InferenceRequest(prompt="new work"))

        await router.aclose()
        self.assertFalse(router.accepting_requests)
        self.assertTrue(provider.closed)

    async def test_wait_policy_preserves_state_and_starts_fresh_runtime(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite3"
            old_store = SQLiteStateStore(database)
            old_entity = Entity("bit", state_store=old_store)
            old_entity.set_state(
                "continuity", ("curious", 7), category="persistent"
            )
            old_entity.set_state("conversation", "old", category="session")
            old_entity.set_state("scratch", True, category="ephemeral")
            old_runtime = Runtime([old_entity])
            started = asyncio.Event()
            release = asyncio.Event()
            resource = ClosableResource()
            fresh_stores: list[SQLiteStateStore] = []

            @old_entity.on("long.work")
            async def long_work(signal: Signal) -> None:
                started.set()
                await release.wait()

            emission = asyncio.create_task(
                old_runtime.emit(Signal(type="long.work"))
            )
            await started.wait()

            def build_fresh() -> RestartTarget:
                store = SQLiteStateStore(database)
                fresh_stores.append(store)
                entity = Entity("bit", state_store=store)
                return RestartTarget(
                    runtime=Runtime([entity]),
                    resources=(store,),
                )

            coordinator = GracefulRestartCoordinator(
                old_runtime,
                build_fresh,
                resources=(resource, old_store),
                task_policy="wait",
                task_grace_seconds=1,
            )
            restarting = asyncio.create_task(coordinator.restart("code changed"))
            await asyncio.sleep(0)

            self.assertFalse(old_runtime.accepting_work)
            self.assertTrue(resource.quiesced)
            with self.assertRaises(RuntimeNotAcceptingWorkError):
                await old_runtime.emit(Signal(type="new.work"))

            release.set()
            result = await restarting
            await emission

            fresh_runtime = coordinator.runtime
            fresh_entity = fresh_runtime.get_entity("bit")
            self.assertIsNotNone(fresh_entity)
            assert fresh_entity is not None
            self.assertEqual(result.status, RestartStatus.COMPLETED)
            self.assertEqual(result.settled_task_ids, result.initial_task_ids)
            self.assertEqual(result.cancelled_task_ids, ())
            self.assertFalse(result.task_wait_timed_out)
            self.assertEqual(result.persisted_entity_ids, ("bit",))
            self.assertTrue(resource.closed)
            self.assertFalse(old_runtime.running)
            self.assertIsNot(fresh_runtime, old_runtime)
            self.assertEqual(fresh_runtime.tasks, [])
            self.assertEqual(fresh_runtime.signals, [])
            self.assertEqual(
                fresh_entity.get_state("continuity", category="persistent"),
                ("curious", 7),
            )
            self.assertIsNone(fresh_entity.get_state("conversation"))
            self.assertIsNone(
                fresh_entity.get_state("scratch", category="ephemeral")
            )

            status = RuntimeService(fresh_runtime).get_runtime_status()
            self.assertTrue(status.accepting_work)
            self.assertIsNotNone(status.restart)
            assert status.restart is not None
            self.assertEqual(status.restart["reason"], "code changed")
            self.assertEqual(status.restart["status"], "completed")
            self.assertEqual(
                old_runtime.latest_logs(1)[0].event_type,
                RuntimeEventType.RUNTIME_STOPPED,
            )
            self.assertEqual(
                fresh_runtime.latest_logs(1)[0].event_type,
                RuntimeEventType.RUNTIME_RESTARTED,
            )

            fresh_runtime.stop()
            fresh_stores[0].close()

    async def test_cancel_policy_cancels_active_tasks_before_restart(self) -> None:
        store = InMemoryStateStore()
        old_entity = Entity("bit", state_store=store)
        old_entity.set_state("identity", "continuous", category="persistent")
        old_entity.set_state("temporary", "discard", category="session")
        old_runtime = Runtime([old_entity])
        started = asyncio.Event()

        @old_entity.on("never.finishes")
        async def never_finishes(signal: Signal) -> None:
            started.set()
            await asyncio.Event().wait()

        emission = asyncio.create_task(
            old_runtime.emit(Signal(type="never.finishes"))
        )
        await started.wait()

        def build_fresh() -> Runtime:
            return Runtime([Entity("bit", state_store=store)])

        coordinator = GracefulRestartCoordinator(
            old_runtime,
            build_fresh,
            task_policy="cancel",
        )
        result = await coordinator.restart("manual restart")

        with self.assertRaises(asyncio.CancelledError):
            await emission
        self.assertEqual(len(result.cancelled_task_ids), 1)
        cancelled = old_runtime.get_task(result.cancelled_task_ids[0])
        self.assertIsNotNone(cancelled)
        self.assertEqual(cancelled.status, TaskStatus.CANCELLED)
        self.assertEqual(
            coordinator.runtime.get_entity("bit").get_state(
                "identity", category="persistent"
            ),
            "continuous",
        )
        self.assertIsNone(
            coordinator.runtime.get_entity("bit").get_state("temporary")
        )
        coordinator.runtime.stop()


if __name__ == "__main__":
    unittest.main()
