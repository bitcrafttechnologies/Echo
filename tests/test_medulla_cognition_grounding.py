from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echo import Entity, MockProvider, ProviderRouter, Runtime, Signal
from echo.host import register_user_message_handler
from medulla_protocol import (
    Capability,
    CapabilityAvailability,
    CapabilityAvailabilityState,
    CapabilityEffect,
    CapabilityPermissions,
    CapabilityProvider,
    CapabilityProviderHealth,
    CapabilityProviderHealthState,
    CapabilityProviderKind,
    CapabilityProviderLocation,
    CapabilityRegistry,
    CapabilityRisk,
    CapabilityTimeout,
    NodeActionError,
    NodeActionErrorCode,
    NodeActionResult,
    NodeActionResultState,
)


def capability(provider: CapabilityProvider, name: str) -> Capability:
    return Capability(
        capability_id=f"{provider.provider_id}.{name}.v1",
        name=name,
        description=f"Test {name}",
        provider=provider,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        availability=CapabilityAvailability(state=CapabilityAvailabilityState.AVAILABLE),
        permissions=CapabilityPermissions(risk=CapabilityRisk.READ, required=(name,)),
        effect=CapabilityEffect.READ_ONLY,
        timeout=CapabilityTimeout(expected_seconds=0.1, maximum_seconds=2),
    )


class FakeMedullaHost:
    def __init__(self) -> None:
        self.provider = CapabilityProvider(
            provider_id="macbook",
            kind=CapabilityProviderKind.WEBSOCKET,
            location=CapabilityProviderLocation.REMOTE,
            display_name="MacBook",
        )
        self.registry = CapabilityRegistry(
            (
                capability(self.provider, "battery.status"),
                capability(self.provider, "system.datetime"),
                capability(self.provider, "web.search"),
                capability(self.provider, "filesystem.list"),
            )
        )
        self.registry.update_provider_health(
            CapabilityProviderHealth(
                provider_id="macbook", state=CapabilityProviderHealthState.HEALTHY
            )
        )
        self.connected = True
        self.fail_battery = False
        self.executed = []

    def status(self, node_id: str):
        assert node_id == "macbook"
        return SimpleNamespace(
            negotiation_state=SimpleNamespace(value="active" if self.connected else "disconnected"),
            reachability=SimpleNamespace(value="reachable" if self.connected else "unreachable"),
        )

    def inspect_resources(self):
        return (
            {
                "id": "filesystem.root.0",
                "description": "Allowed test root",
                "schema": {"type": "string"},
                "metadata": {"root_label": "Echo"},
                "node_id": "macbook",
                "provider_id": "macbook",
                "provider_connected": self.connected,
            },
        )

    async def execute_capability(self, action):
        self.executed.append(action)
        selected = self.registry.find(action.type)[0]
        if action.type == "battery.status" and self.fail_battery:
            return NodeActionResult(
                action_id=action.id,
                state=NodeActionResultState.FAILED,
                node_id="macbook",
                provider_id="macbook",
                capability_id=selected.capability_id,
                error=NodeActionError(
                    code=NodeActionErrorCode.EXECUTION_FAILED,
                    message="battery probe failed",
                ),
            )
        if action.type == "battery.status":
            payload = {"percent": 80, "power_source": "ac"}
        elif action.type == "system.datetime":
            payload = {"iso8601": "2026-09-13T20:00:00-07:00"}
        elif action.type == "filesystem.list":
            payload = {
                "path": "/allowed",
                "entries": [{"name": "notes.txt", "type": "file"}],
                "truncated": False,
            }
        else:
            payload = {
                "query": action.parameters["query"],
                "results": [{"title": "AI news"}],
            }
        return NodeActionResult(
            action_id=action.id,
            state=NodeActionResultState.COMPLETED,
            node_id="macbook",
            provider_id="macbook",
            capability_id=selected.capability_id,
            result=payload,
            completed_at=datetime.now(timezone.utc),
        )

    def disconnect(self) -> None:
        self.connected = False
        for item in self.registry.capabilities_for_provider("macbook"):
            self.registry.update_availability(
                item.capability_id,
                CapabilityAvailability(state=CapabilityAvailabilityState.UNAVAILABLE),
            )
        self.registry.update_provider_health(
            CapabilityProviderHealth(
                provider_id="macbook", state=CapabilityProviderHealthState.UNAVAILABLE
            )
        )

    def reconnect(self) -> None:
        self.connected = True
        for item in self.registry.capabilities_for_provider("macbook"):
            self.registry.update_availability(
                item.capability_id,
                CapabilityAvailability(state=CapabilityAvailabilityState.AVAILABLE),
            )
        self.registry.update_provider_health(
            CapabilityProviderHealth(
                provider_id="macbook", state=CapabilityProviderHealthState.HEALTHY
            )
        )


class MedullaCognitionGroundingTests(unittest.TestCase):
    def run_sequence(self, prompts, *, responses=None, host=None):
        async def exercise():
            selected_host = host or FakeMedullaHost()
            provider = MockProvider(responses=responses or tuple("grounded" for _ in prompts))
            entity = Entity("bit")
            runtime = Runtime([entity])
            register_user_message_handler(
                entity, ProviderRouter(remote=provider), node_host=selected_host
            )
            for prompt in prompts:
                await runtime.emit(Signal(type="UserMessage", payload={"text": prompt}))
            return selected_host, provider, runtime

        return asyncio.run(exercise())

    def test_inventory_followup_uses_current_focus_not_historical_search(self) -> None:
        host, provider, _runtime = self.run_sequence(
            (
                "Search Wikinews for AI news.",
                "What capabilities do you currently have?",
                "Check again.",
            )
        )
        self.assertEqual([item.type for item in host.executed], ["web.search"])
        second = provider.requests[1].context["medulla"]
        third = provider.requests[2].context["medulla"]
        self.assertTrue(second["inventory_requested"])
        self.assertTrue(third["inventory_requested"])
        self.assertEqual(third["conversation_focus"], "medulla.capability_inventory")
        self.assertEqual(
            {item["name"] for item in third["inventory"]["capabilities"]},
            {"battery.status", "system.datetime", "web.search", "filesystem.list"},
        )

    def test_file_visibility_invokes_declared_root_and_hides_action_envelope(self) -> None:
        host, provider, runtime = self.run_sequence(
            ("What files can you see right now?",),
            responses=(
                '{"request_actions":[{"name":"filesystem.list","arguments":{"path":""}}],'
                '"reply":"Let me take a look."}',
            ),
        )
        self.assertEqual([item.type for item in host.executed], ["filesystem.list"])
        self.assertEqual(
            host.executed[0].parameters, {"resource_id": "filesystem.root.0"}
        )
        invocation = provider.requests[0].context["medulla"]["invocations"][0]
        self.assertEqual(invocation["result"]["entries"][0]["name"], "notes.txt")
        response = runtime.action_history.latest()[0].parameters["text"]
        self.assertIn('"name": "notes.txt"', response)
        self.assertNotIn("request_actions", response)

    def test_successful_result_is_reinjected_with_complete_evidence(self) -> None:
        host, provider, runtime = self.run_sequence(("What's your battery percentage?",))
        evidence = provider.requests[0].context["medulla"]["invocations"][0]
        self.assertEqual(evidence["status"], "completed")
        self.assertEqual(evidence["result"]["percent"], 80)
        for key in (
            "invocation_id", "action_id", "capability_id", "provider_id",
            "node_id", "status", "result", "error", "completed_at",
        ):
            self.assertIn(key, evidence)
        action_types = [item.type for item in reversed(runtime.action_history.latest())]
        self.assertEqual(action_types, ["battery.status", "EchoResponse"])
        self.assertEqual(runtime.action_history.latest()[0].parameters["medulla_invocations"], [evidence])

    def test_failure_is_distinct_from_registration_and_connection(self) -> None:
        host = FakeMedullaHost()
        host.fail_battery = True
        _host, provider, _runtime = self.run_sequence(
            ("What's your battery percentage?",), host=host
        )
        medulla = provider.requests[0].context["medulla"]
        battery = next(
            item for item in medulla["inventory"]["capabilities"]
            if item["name"] == "battery.status"
        )
        self.assertTrue(battery["registered"])
        self.assertTrue(battery["node_connected"])
        self.assertTrue(battery["available"])
        self.assertEqual(medulla["invocations"][0]["status"], "failed")
        self.assertIsNone(medulla["invocations"][0]["result"])

    def test_raw_results_reuse_prior_evidence_without_another_invocation(self) -> None:
        host, provider, _runtime = self.run_sequence(
            ("Search Wikinews for AI news.", "Dump the raw results."),
            responses=("Here are the results.", "I searched again just now."),
        )
        self.assertEqual(len(host.executed), 1)
        first = provider.requests[0].context["medulla"]["invocations"]
        second = provider.requests[1].context["medulla"]
        self.assertTrue(second["results_reused"])
        self.assertEqual(second["invocations"], first)
        response = _runtime.action_history.latest()[0].parameters["text"]
        self.assertIn('"title": "AI news"', response)
        self.assertIn(f'"invocation_id": "{first[0]["invocation_id"]}"', response)
        self.assertNotIn("searched again", response)

    def test_inventory_tracks_disconnect_and_reconnect(self) -> None:
        async def exercise():
            host = FakeMedullaHost()
            provider = MockProvider(responses=("connected", "disconnected", "connected again"))
            entity = Entity("bit")
            runtime = Runtime([entity])
            register_user_message_handler(entity, ProviderRouter(remote=provider), node_host=host)
            await runtime.emit(Signal(type="UserMessage", payload={"text": "What capabilities do you have?"}))
            host.disconnect()
            await runtime.emit(Signal(type="UserMessage", payload={"text": "Check again"}))
            host.reconnect()
            await runtime.emit(Signal(type="UserMessage", payload={"text": "Check again"}))
            return provider

        provider = asyncio.run(exercise())
        states = [
            request.context["medulla"]["inventory"]["capabilities"][0]["available"]
            for request in provider.requests
        ]
        self.assertEqual(states, [True, False, True])

    def test_inventory_answer_is_rendered_from_authoritative_state(self) -> None:
        _host, _provider, runtime = self.run_sequence(
            ("What capabilities do you currently have?",),
            responses=("I only have a toaster capability.",),
        )
        response = runtime.action_history.latest()[0].parameters["text"]
        self.assertIn("battery.status via MacBook: available", response)
        self.assertIn("system.datetime via MacBook: available", response)
        self.assertNotIn("toaster", response)

    def test_absent_capability_is_reported_from_registry_without_invocation(self) -> None:
        host = FakeMedullaHost()
        time_capability = host.registry.find("system.datetime")[0]
        host.registry.remove(time_capability.capability_id)
        _host, _provider, runtime = self.run_sequence(
            ("What time is it?",),
            responses=("It is definitely noon.",),
            host=host,
        )
        self.assertEqual(host.executed, [])
        response = runtime.action_history.latest()[0].parameters["text"]
        self.assertIn("system.datetime is not registered", response)
        self.assertNotIn("noon", response)

    def test_unsupported_external_action_claim_is_blocked_at_runtime(self) -> None:
        _host, _provider, runtime = self.run_sequence(
            ("Hello",), responses=("I searched the web just now.",)
        )
        response = runtime.action_history.latest()[0].parameters["text"]
        self.assertIn("don't have runtime evidence", response)
        self.assertNotEqual(response, "I searched the web just now.")


if __name__ == "__main__":
    unittest.main()
