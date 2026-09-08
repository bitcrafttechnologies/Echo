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

from echo import Entity, Runtime, RuntimeService, Signal


@unittest.skipIf(TestClient is None, "install the test extra to test the HTTP adapter")
class FastAPIAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        from echo.adapters.fastapi import create_app

        self.bit = Entity("bit", state={"mode": "idle", "private": "fixed"})
        self.runtime = Runtime([self.bit])
        self.service = RuntimeService(
            self.runtime,
            allowed_state_keys={"bit": {"mode"}},
        )

        @self.bit.on("observe")
        async def observe(signal: Signal):
            self.runtime.update_entity_state("bit", {"mode": "observed"})
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
