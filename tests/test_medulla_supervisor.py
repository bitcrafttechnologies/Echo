from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echo import (
    Action,
    ActionDispatchState,
    EchoApplication,
    Entity,
    LocalQueueTransport,
    MedullaLifecycle,
    MedullaSupervisor,
    Runtime,
    Signal,
    TransportErrorCode,
    TransportHealthState,
    TransportStateError,
)
from echo.medulla import BaseTransport


class FailingStartTransport(BaseTransport):
    def __init__(self) -> None:
        super().__init__("broken")

    async def _start(self) -> None:
        raise OSError("unplugged")

    async def _stop(self) -> None:
        return None

    async def _receive(self) -> Signal:
        raise AssertionError("failed transport must not receive")

    async def _execute(self, action: Action):
        raise AssertionError("failed transport must not execute")

    async def _health(self):
        raise OSError("unplugged")


class MedullaSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_inbound_signal_is_pumped_into_runtime(self) -> None:
        delivered = asyncio.Event()
        entity = Entity("echo")
        runtime = Runtime([entity])
        transport = LocalQueueTransport()
        supervisor = MedullaSupervisor(runtime, [transport])

        @entity.on("website.user_message")
        async def receive_message(signal: Signal) -> None:
            entity.state["message"] = signal.payload["text"]
            delivered.set()

        status = await supervisor.start()
        await transport.publish_signal(
            Signal(
                type="website.user_message",
                source="test-browser",
                payload={"text": "Hello Echo"},
            )
        )
        await asyncio.wait_for(delivered.wait(), timeout=1)

        self.assertEqual(status.lifecycle, MedullaLifecycle.RUNNING)
        self.assertEqual(entity.state["message"], "Hello Echo")
        await supervisor.stop()

    async def test_handler_failure_is_recorded_and_later_signals_still_flow(self) -> None:
        delivered = asyncio.Event()
        entity = Entity("echo")
        runtime = Runtime([entity])
        transport = LocalQueueTransport()
        supervisor = MedullaSupervisor(runtime, [transport])

        @entity.on("project.input")
        async def handle(signal: Signal) -> None:
            if signal.payload.get("fail"):
                raise RuntimeError("handler rejected input")
            entity.state["delivered"] = True
            delivered.set()

        await supervisor.start()
        await transport.publish_signal(
            Signal(type="project.input", payload={"fail": True})
        )
        await transport.publish_signal(
            Signal(type="project.input", payload={"fail": False})
        )
        await asyncio.wait_for(delivered.wait(), timeout=1)

        status = supervisor.status()
        self.assertEqual(status.receiving_transport_ids, ("local",))
        self.assertTrue(
            any("handler rejected input" in item.message for item in status.recent_failures)
        )
        self.assertTrue(runtime.running)
        await supervisor.stop()

    async def test_outbound_dispatch_is_explicit_and_uses_the_default(self) -> None:
        entity = Entity("echo")
        runtime = Runtime([entity])
        transport = LocalQueueTransport()
        supervisor = MedullaSupervisor(runtime, [transport])
        await supervisor.start()
        action = await entity.action("website.avatar_speak", text="Hello")

        result = await supervisor.dispatch(action)
        external_action = await transport.receive_action()

        self.assertEqual(result.state, ActionDispatchState.ACCEPTED)
        self.assertEqual(external_action.id, action.id)
        self.assertEqual(external_action.parameters, {"text": "Hello"})
        self.assertEqual(len(runtime.actions), 1)
        await supervisor.stop()

    async def test_failed_transport_is_isolated_from_a_healthy_transport(self) -> None:
        entity = Entity("echo")
        runtime = Runtime([entity])
        healthy = LocalQueueTransport("healthy")
        supervisor = MedullaSupervisor(
            runtime,
            [FailingStartTransport(), healthy],
            default_transport_id="healthy",
        )

        status = await supervisor.start()
        result = await supervisor.dispatch(Action(type="website.render"))
        health = await supervisor.health()

        self.assertEqual(status.lifecycle, MedullaLifecycle.RUNNING)
        self.assertEqual(status.receiving_transport_ids, ("healthy",))
        self.assertTrue(any(item.transport_id == "broken" for item in status.recent_failures))
        self.assertEqual(result.state, ActionDispatchState.ACCEPTED)
        self.assertEqual(health.state, TransportHealthState.DEGRADED)
        self.assertTrue(runtime.running)
        await supervisor.stop()

    async def test_dispatch_failure_is_data_and_does_not_change_action_history(self) -> None:
        entity = Entity("echo")
        runtime = Runtime([entity])
        transport = LocalQueueTransport(outbound_capacity=1)
        supervisor = MedullaSupervisor(runtime, [transport])
        await supervisor.start()
        first = await entity.action("one")
        second = await entity.action("two")

        await supervisor.dispatch(first)
        failed = await supervisor.dispatch(second)

        self.assertEqual(failed.state, ActionDispatchState.FAILED)
        self.assertEqual(failed.error.code, TransportErrorCode.QUEUE_FULL)
        self.assertEqual([item.id for item in runtime.actions], [first.id, second.id])
        self.assertTrue(runtime.running)
        await supervisor.stop()

    async def test_stop_is_clean_and_wakes_local_consumers(self) -> None:
        runtime = Runtime([Entity("echo")])
        transport = LocalQueueTransport()
        supervisor = MedullaSupervisor(runtime, [transport])
        await supervisor.start()
        consumer = asyncio.create_task(transport.receive_action())
        await asyncio.sleep(0)

        status = await supervisor.stop()

        self.assertEqual(status.lifecycle, MedullaLifecycle.STOPPED)
        with self.assertRaises(TransportStateError):
            await consumer

    async def test_stable_lifecycle_is_idempotent_and_restartable(self) -> None:
        runtime = Runtime([Entity("echo")])
        transport = LocalQueueTransport()
        supervisor = MedullaSupervisor(runtime, [transport])

        await supervisor.start()
        await supervisor.start()
        await supervisor.stop()
        await supervisor.stop()
        restarted = await supervisor.start()

        self.assertEqual(restarted.lifecycle, MedullaLifecycle.RUNNING)
        self.assertEqual(restarted.receiving_transport_ids, ("local",))
        await supervisor.stop()


class EchoApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_owned_entity_directory_builds_runnable_application(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "prompts").mkdir()
            (root / "identity.yaml").write_text(
                """schema_version: 1
entity:
  id: echo
  name: Echo
  type: website_character
  presentation: text
identity:
  worldview: Curious and grounded
  core_values:
    - honesty
""",
                encoding="utf-8",
            )
            (root / "traits.yaml").write_text(
                "schema_version: 1\ntraits:\n  curiosity: 0.8\n",
                encoding="utf-8",
            )
            (root / "drives.yaml").write_text(
                "schema_version: 1\ndrives:\n  learning: 0.7\n",
                encoding="utf-8",
            )
            (root / "prompts" / "character.md").write_text(
                "Speak as Echo.", encoding="utf-8"
            )
            transport = LocalQueueTransport()
            application = EchoApplication.from_entity_directory(root, [transport])

            await application.start()

            self.assertTrue(application.runtime.running)
            self.assertEqual(application.entity.id, "echo")
            self.assertEqual(application.character_guidance, "Speak as Echo.")
            await application.stop()
            self.assertFalse(application.runtime.running)


if __name__ == "__main__":
    unittest.main()
