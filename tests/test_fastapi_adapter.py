from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None

from echo import (
    EmitSignalRequest,
    Entity,
    RelationshipState,
    RelationshipStore,
    Runtime,
    RuntimeService,
    RuntimeSubscriptionRequest,
    Signal,
)


class TrackingRuntimeService(RuntimeService):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.created_subscriptions = []

    def subscribe_events(self, request=None):
        subscription = super().subscribe_events(request)
        self.created_subscriptions.append(subscription)
        return subscription


@unittest.skipIf(TestClient is None, "install the test extra to test the HTTP adapter")
class FastAPIAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        from echo.adapters.fastapi import create_app

        self.bit = Entity(
            "bit",
            state={"mode": "idle", "private": "fixed"},
            relationships=RelationshipStore(
                {
                    "nathan": RelationshipState(
                        "nathan",
                        familiarity=0.7,
                        trust=0.8,
                        interaction_count=4,
                        current_context={"topic": "Echo"},
                    )
                }
            ),
        )
        self.runtime = Runtime([self.bit])
        self.service = TrackingRuntimeService(
            self.runtime,
            allowed_state_keys={"bit": {"mode"}},
        )

        @self.bit.on("observe")
        async def observe(signal: Signal):
            self.bit.state["mode"] = "observed"
            return await self.bit.action("remember", value=signal.payload["value"])

        self.client = TestClient(create_app(self.service))

    def test_health_runtime_and_entity_responses(self) -> None:
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

        runtime = self.client.get("/runtime/status")
        self.assertEqual(runtime.status_code, 200)
        self.assertEqual(runtime.json()["runtime_id"], self.runtime.id)
        self.assertEqual(runtime.json()["status"], "idle")

        entities = self.client.get("/entities")
        self.assertEqual(entities.status_code, 200)
        self.assertEqual([item["id"] for item in entities.json()], ["bit"])

        entity = self.client.get("/entities/bit")
        self.assertEqual(entity.status_code, 200)
        self.assertEqual(entity.json()["handlers"]["count"], 1)

        relationships = self.client.get("/entities/bit/relationships")
        self.assertEqual(relationships.status_code, 200)
        self.assertEqual(relationships.json()[0]["subject_id"], "nathan")
        relationship = self.client.get("/entities/bit/relationships/nathan")
        self.assertEqual(relationship.json()["current_context"], {"topic": "Echo"})
        self.assertEqual(
            self.client.get("/entities/bit/relationships/missing").status_code,
            404,
        )

    def test_signal_task_action_state_and_log_responses(self) -> None:
        emitted = self.client.post(
            "/signals",
            json={
                "type": "observe",
                "source": "http-test",
                "payload": {"value": 7},
                "priority": "high",
            },
        )
        self.assertEqual(emitted.status_code, 201)
        signal_id = emitted.json()["id"]
        self.assertEqual(emitted.json()["routing_result"]["status"], "completed")

        signals = self.client.get(
            "/signals",
            params={"limit": 1, "signal_type": "observe", "source": "http-test"},
        )
        self.assertEqual([item["id"] for item in signals.json()], [signal_id])
        self.assertEqual(
            self.client.get(f"/signals/{signal_id}").json()["payload"],
            {"value": 7},
        )

        tasks = self.client.get("/tasks", params={"status": "completed"})
        self.assertEqual(tasks.status_code, 200)
        task_id = tasks.json()[0]["id"]
        self.assertEqual(
            self.client.get(f"/tasks/{task_id}").json()["signal_id"], signal_id
        )

        actions = self.client.get(
            "/actions",
            params={"type": "remember", "task_id": task_id, "signal_id": signal_id},
        )
        self.assertEqual(actions.status_code, 200)
        action_id = actions.json()[0]["id"]
        self.assertEqual(
            self.client.get(f"/actions/{action_id}").json()["parameters"],
            {"value": 7},
        )

        state = self.client.get("/entities/bit/state")
        self.assertEqual(state.json()["values"]["mode"], "observed")
        updated = self.client.patch(
            "/entities/bit/state",
            json={"values": {"mode": "ready"}},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["values"]["mode"], "ready")

        logs = self.client.get(
            "/logs",
            params={"event_type": "state.changed", "entity_id": "bit"},
        )
        self.assertEqual(logs.status_code, 200)
        self.assertGreaterEqual(len(logs.json()["events"]), 2)

        errors = self.client.get("/logs", params={"severity": "error"})
        self.assertEqual(errors.status_code, 200)
        self.assertEqual(errors.json()["events"], [])
        invalid_severity = self.client.get("/logs", params={"severity": "critical"})
        self.assertEqual(invalid_severity.status_code, 400)

    def test_user_message_uses_normal_signal_task_and_action_routing(self) -> None:
        @self.bit.on("UserMessage")
        async def user_message(signal: Signal):
            return await self.bit.action(
                "respond", text=f"Echo received: {signal.payload['text']}"
            )

        emitted = self.client.post(
            "/signals",
            json={
                "type": "UserMessage",
                "source": "console",
                "payload": {"text": "Hello"},
                "metadata": {"channel": "console"},
            },
        )

        self.assertEqual(emitted.status_code, 201)
        body = emitted.json()
        self.assertEqual(body["routing_result"]["status"], "completed")
        task_id = body["routing_result"]["task_ids"][0]
        task = self.client.get(f"/tasks/{task_id}").json()
        actions = self.client.get("/actions", params={"signal_id": body["id"]}).json()
        self.assertEqual(task["signal_id"], body["id"])
        self.assertEqual(actions[0]["task_id"], task_id)
        self.assertEqual(actions[0]["parameters"]["text"], "Echo received: Hello")
    def test_service_errors_have_stable_http_responses(self) -> None:
        @self.bit.on("broken")
        async def broken(signal: Signal) -> None:
            raise ValueError("handler broke")

        missing = self.client.get("/entities/missing")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "not_found")

        denied = self.client.patch(
            "/entities/bit/state",
            json={"values": {"private": "changed"}},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(
            denied.json()["error"]["code"], "state_update_not_allowed"
        )

        invalid_filter = self.client.get("/tasks", params={"status": "unknown"})
        self.assertEqual(invalid_filter.status_code, 400)
        self.assertEqual(invalid_filter.json()["error"]["code"], "invalid_request")

        invalid_signal = self.client.post(
            "/signals",
            json={"type": "observe", "timestamp": "2026-09-08T12:00:00"},
        )
        self.assertEqual(invalid_signal.status_code, 422)

        failed_signal = self.client.post("/signals", json={"type": "broken"})
        self.assertEqual(failed_signal.status_code, 422)
        self.assertEqual(
            failed_signal.json()["error"]["code"], "signal_emission_failed"
        )

        completed = self.client.post(
            "/signals",
            json={"type": "observe", "payload": {"value": 1}},
        ).json()["routing_result"]["task_ids"][0]
        conflict = self.client.post(f"/tasks/{completed}/cancel")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "task_not_cancellable")

    def test_websocket_streams_signal_task_and_action_events(self) -> None:
        with self.client.websocket_connect("/events") as websocket:
            emitted = self.client.post(
                "/signals",
                json={"type": "observe", "payload": {"value": 9}},
            )
            self.assertEqual(emitted.status_code, 201)
            signal_id = emitted.json()["id"]
            events = [websocket.receive_json() for _ in range(8)]

        self.assertEqual(
            [event["sequence"] for event in events],
            sorted(event["sequence"] for event in events),
        )
        self.assertTrue(
            all(event["event"]["signal_id"] == signal_id for event in events)
        )
        self.assertEqual(
            {event["category"] for event in events},
            {
                "signal.received",
                "signal.routed",
                "task.lifecycle",
                "action.lifecycle",
                "state.changed",
            },
        )
        self.assertEqual(
            [event["event"]["event_type"] for event in events],
            [
                "signal.received",
                "signal.routed",
                "task.created",
                "task.status_changed",
                "action.created",
                "action.executed",
                "task.status_changed",
                "state.changed",
            ],
        )
        self.assertTrue(self.service.created_subscriptions[-1].closed)

    def test_websocket_filter_disconnect_and_reconnect(self) -> None:
        path = "/events?category=signal.received&max_queue_size=2"
        with self.client.websocket_connect(path) as first:
            first_signal = self.client.post(
                "/signals", json={"type": "observe", "payload": {"value": 1}}
            ).json()
            first_event = first.receive_json()

        first_subscription = self.service.created_subscriptions[-1]
        self.assertTrue(first_subscription.closed)
        self.assertEqual(self.runtime._event_broker.subscriber_count, 0)
        self.assertEqual(first_event["category"], "signal.received")
        self.assertEqual(first_event["event"]["signal_id"], first_signal["id"])

        while_disconnected = self.client.post(
            "/signals", json={"type": "observe", "payload": {"value": 2}}
        )
        self.assertEqual(while_disconnected.status_code, 201)
        self.assertTrue(self.runtime.running)

        with self.client.websocket_connect(path) as second:
            second_signal = self.client.post(
                "/signals", json={"type": "observe", "payload": {"value": 3}}
            ).json()
            second_event = second.receive_json()

        second_subscription = self.service.created_subscriptions[-1]
        self.assertIsNot(first_subscription, second_subscription)
        self.assertTrue(second_subscription.closed)
        self.assertEqual(self.runtime._event_broker.subscriber_count, 0)
        self.assertEqual(second_event["category"], "signal.received")
        self.assertEqual(second_event["event"]["signal_id"], second_signal["id"])
        self.assertNotEqual(second_event["event"]["signal_id"], first_signal["id"])

        after_disconnect = self.client.post(
            "/signals", json={"type": "observe", "payload": {"value": 4}}
        )
        self.assertEqual(after_disconnect.status_code, 201)
        self.assertTrue(self.runtime.running)

    def test_slow_websocket_does_not_block_runtime_publication(self) -> None:
        from echo.adapters.fastapi import _stream_events

        async def exercise() -> None:
            send_started = asyncio.Event()
            release_send = asyncio.Event()
            disconnect = asyncio.Event()

            class SlowWebSocket:
                async def send_json(self, event) -> None:
                    send_started.set()
                    await release_send.wait()

                async def receive(self):
                    await disconnect.wait()
                    return {"type": "websocket.disconnect"}

            subscription = self.service.subscribe_events(
                RuntimeSubscriptionRequest(
                    categories=("signal.received",),
                    max_queue_size=2,
                )
            )
            streaming = asyncio.create_task(
                _stream_events(SlowWebSocket(), subscription)
            )

            await self.service.emit_signal(
                EmitSignalRequest(signal=Signal(type="slow.first"))
            )
            await send_started.wait()
            for index in range(5):
                await self.service.emit_signal(
                    EmitSignalRequest(signal=Signal(type=f"slow.{index}"))
                )

            self.assertTrue(self.runtime.running)
            self.assertEqual(subscription.pending_count, 2)
            self.assertEqual(subscription.dropped_count, 3)

            disconnect.set()
            release_send.set()
            await streaming
            self.assertTrue(subscription.closed)
            self.assertEqual(self.runtime._event_broker.subscriber_count, 0)

        asyncio.run(exercise())


class FastAPIIsolationTests(unittest.TestCase):
    def test_echo_core_runs_when_fastapi_cannot_be_imported(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository / "src")
        script = """
import asyncio
import importlib.abc
import sys

class BlockFastAPI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'fastapi' or fullname.startswith('fastapi.'):
            raise ModuleNotFoundError('FastAPI intentionally unavailable')
        return None

sys.meta_path.insert(0, BlockFastAPI())
from echo import Entity, Runtime, Signal

entity = Entity('core-only')
runtime = Runtime([entity])
signal = Signal(type='core.check')
asyncio.run(runtime.emit(signal))
assert runtime.get_signal(signal.id) is not None
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repository,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
