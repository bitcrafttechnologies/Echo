from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None

from echo import Entity, Runtime, RuntimeService
from echo.tui import HttpEchoClient, LocalEchoClient, render_snapshot


class FakeNodeHost:
    def __init__(self) -> None:
        self.state = "awaiting_approval"
        self.node_ids = ("macbook-home",)

    def inspect_node(self, node_id):
        return {
            "node_id": node_id,
            "first_seen_at": "2026-09-13T08:00:00+00:00",
            "last_seen_at": "2026-09-13T08:00:00+00:00",
            "active_at": None,
            "disconnected_at": None,
            "reachability": "healthy",
            "negotiation_state": self.state,
            "approval_mode": "manual",
            "decision_reason": None,
            "granted_scopes": None,
            "advertised_manifest": {"node": {"id": node_id, "display_name": "Home MacBook"}, "capabilities": [{"name": "battery.status"}]},
            "approval_request": {"provides": ["battery.status"], "requires": []},
            "events": [],
        }

    async def decide(self, node_id, decision, reason=None):
        del node_id, reason
        self.state = {"approve": "approved", "decline": "declined", "block": "blocked"}[decision.value]

    async def reconsider(self, node_id):
        del node_id
        self.state = "awaiting_approval"

    def unblock(self, node_id):
        del node_id
        return self.state == "blocked"

    async def authorize(self, node_id, authorization):
        del node_id, authorization
        self.state = "active"


class MedullaManagementTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_tui_lists_and_controls_nodes(self) -> None:
        host = FakeNodeHost()
        service = RuntimeService(Runtime([Entity("echo")]), node_host=host)
        client = LocalEchoClient(service)
        snapshot = await client.snapshot("medulla")
        rendered = render_snapshot("medulla", snapshot, width=180)
        self.assertIn("Home MacBook", rendered)
        self.assertIn("battery.status", rendered)
        await client.decide_medulla_node("macbook-home", "approve", reason="operator chose it")
        await client.authorize_medulla_node("macbook-home", {})
        self.assertEqual(host.state, "active")

    async def test_http_tui_client_uses_public_medulla_endpoints(self) -> None:
        client = HttpEchoClient("http://echo.test")
        client._request = AsyncMock(return_value={"node_id": "macbook-home"})
        await client.decide_medulla_node("macbook-home", "approve", reason="needed")
        client._request.assert_awaited_with("/medulla/nodes/macbook-home/decision", method="POST", body={"decision": "approve", "reason": "needed"})
        await client.authorize_medulla_node("macbook-home", {})
        client._request.assert_awaited_with("/medulla/nodes/macbook-home/authorization", method="POST", body={"authorization": {}})


@unittest.skipIf(TestClient is None, "install the API extra")
class MedullaHttpApiTests(unittest.TestCase):
    def test_web_api_lists_approves_and_authorizes_node(self) -> None:
        from echo.adapters.fastapi import create_app

        host = FakeNodeHost()
        client = TestClient(create_app(RuntimeService(Runtime([Entity("echo")]), node_host=host)))
        listed = client.get("/medulla/nodes")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["negotiation_state"], "awaiting_approval")
        approved = client.post("/medulla/nodes/macbook-home/decision", json={"decision": "approve", "reason": "operator chose it"})
        self.assertEqual(approved.json()["negotiation_state"], "approved")
        active = client.post("/medulla/nodes/macbook-home/authorization", json={"authorization": {}})
        self.assertEqual(active.json()["negotiation_state"], "active")


if __name__ == "__main__":
    unittest.main()
