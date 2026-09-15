from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echo import Entity, MemoryService, MockProvider, ProviderRouter, Runtime, Signal
from echo.entity.memory_store import user_statement_candidates
from echo.host import register_user_message_handler
from echo.state import SQLiteMemoryRepository
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


class GateMedullaHost:
    def __init__(self) -> None:
        provider = CapabilityProvider(
            provider_id="gate-node",
            kind=CapabilityProviderKind.WEBSOCKET,
            location=CapabilityProviderLocation.REMOTE,
            display_name="Gate node",
        )
        self.registry = CapabilityRegistry(
            tuple(
                Capability(
                    capability_id=f"gate-node.{name}.v1",
                    name=name,
                    description=f"Read {name}",
                    provider=provider,
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    availability=CapabilityAvailability(
                        state=CapabilityAvailabilityState.AVAILABLE
                    ),
                    permissions=CapabilityPermissions(
                        risk=CapabilityRisk.READ, required=(name,)
                    ),
                    effect=CapabilityEffect.READ_ONLY,
                    timeout=CapabilityTimeout(expected_seconds=0.1, maximum_seconds=2),
                )
                for name in (
                    "weather.current",
                    "web.search",
                    "battery.status",
                    "system.health",
                )
            )
        )
        self.registry.update_provider_health(
            CapabilityProviderHealth(
                provider_id="gate-node", state=CapabilityProviderHealthState.HEALTHY
            )
        )
        self.executed = []
        self.fail_search = False

    def status(self, _node_id):
        return SimpleNamespace(
            negotiation_state=SimpleNamespace(value="active"),
            reachability=SimpleNamespace(value="reachable"),
        )

    def inspect_resources(self):
        return ()

    async def execute_capability(self, action):
        self.executed.append(action)
        selected = self.registry.find(action.type)[0]
        error = None
        state = NodeActionResultState.COMPLETED
        if action.type == "web.search" and self.fail_search:
            state = NodeActionResultState.FAILED
            error = NodeActionError(
                code=NodeActionErrorCode.EXECUTION_FAILED,
                message="forced search failure",
            )
        payloads = {
            "weather.current": {"temperature_c": 31},
            "web.search": {"results": [{"title": "Fusion milestone"}]},
            "battery.status": {"percent": 18},
            "system.health": {"status": "healthy"},
        }
        return NodeActionResult(
            action_id=action.id,
            state=state,
            node_id="gate-node",
            provider_id="gate-node",
            capability_id=selected.capability_id,
            result=payloads[action.type] if error is None else {},
            error=error,
            completed_at=datetime.now(timezone.utc),
        )


async def send(runtime: Runtime, text: str) -> None:
    await runtime.emit(Signal(type="UserMessage", payload={"text": text}))


class ContinuityGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_immediate_pronoun_reference_is_always_in_context(self) -> None:
        provider = MockProvider(responses=("The Echo repository is thoughtfully layered.", "It is promising."))
        entity = Entity("bit")
        runtime = Runtime([entity])
        register_user_message_handler(entity, ProviderRouter(remote=provider))
        await send(runtime, "Describe the Echo repository.")
        await send(runtime, "What do you think of it?")
        context = provider.requests[1].context
        self.assertEqual(
            [item["role"] for item in context["immediate_conversation"]],
            ["user", "assistant"],
        )
        self.assertEqual(
            context["dialogue_state"]["interpretation"]["kind"],
            "immediate_dialogue_reference",
        )

    async def test_confirmation_executes_pending_weather_intent(self) -> None:
        provider = MockProvider(responses=("Want me to check the weather?", "It is 31 C."))
        host = GateMedullaHost()
        entity = Entity("bit")
        runtime = Runtime([entity])
        register_user_message_handler(entity, ProviderRouter(remote=provider), node_host=host)
        await send(runtime, "I'm heading outside.")
        await send(runtime, "Go for it.")
        self.assertEqual([action.type for action in host.executed], ["weather.current"])
        self.assertEqual(
            provider.requests[1].context["dialogue_state"]["interpretation"]["kind"],
            "pending_intent_confirmation",
        )

    async def test_authorized_search_executes_without_second_prompt(self) -> None:
        provider = MockProvider(response="Fusion research is moving quickly.")
        host = GateMedullaHost()
        entity = Entity("bit")
        runtime = Runtime([entity])
        register_user_message_handler(entity, ProviderRouter(remote=provider), node_host=host)
        await send(runtime, "Search the web for something you're interested in.")
        self.assertEqual([action.type for action in host.executed], ["web.search"])
        trace = runtime.action_history.latest()[0].parameters["cognition_trace"]
        self.assertEqual(trace["capabilities_selected"], ["web.search"])
        self.assertEqual(trace["action_results_returned"][0]["status"], "completed")

    async def test_compound_medulla_and_failure_are_grounded(self) -> None:
        provider = MockProvider(responses=("Battery 18%; healthy.", "I searched and found results."))
        host = GateMedullaHost()
        entity = Entity("bit")
        runtime = Runtime([entity])
        register_user_message_handler(entity, ProviderRouter(remote=provider), node_host=host)
        await send(runtime, "How are your battery and system health?")
        self.assertEqual(
            [action.type for action in host.executed],
            ["battery.status", "system.health"],
        )
        host.fail_search = True
        await send(runtime, "Search the web for fusion news.")
        response = runtime.action_history.latest()[0].parameters["text"]
        self.assertIn("don't have runtime evidence", response)
        self.assertNotIn("found results", response)

    async def test_unknown_is_not_rewritten_as_never_happened(self) -> None:
        provider = MockProvider(response="You haven't told me your favorite movie!")
        entity = Entity("bit")
        runtime = Runtime([entity])
        register_user_message_handler(entity, ProviderRouter(remote=provider))
        await send(runtime, "What's my favorite movie?")
        output = runtime.action_history.latest()[0].parameters["text"]
        self.assertIn("don't currently have", output)
        self.assertNotIn("haven't told", output)

    def test_transient_telemetry_does_not_become_a_semantic_candidate(self) -> None:
        candidates = user_statement_candidates(
            "Battery is 18%", signal_id="signal-1"
        )
        self.assertEqual(candidates, ())


class RestartAndReinstallGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_database_owns_name_and_episodic_continuity(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "echo.sqlite3"
            first_repo = SQLiteMemoryRepository(database)
            first_provider = MockProvider(responses=("Nice to meet you.", "Great topic."))
            first = Entity("bit", memory_service=MemoryService("bit", first_repo))
            first_runtime = Runtime([first])
            register_user_message_handler(first, ProviderRouter(remote=first_provider))
            await send(first_runtime, "My name is Tucker.")
            await send(first_runtime, "Let's discuss orbital gardening.")
            first_repo.close()

            # A new application generation (including a reinstall) gets no process state.
            # Reusing only SQLite must preserve Entity continuity.
            second_repo = SQLiteMemoryRepository(database)
            second_provider = MockProvider(responses=("Tucker.", "Orbital gardening."))
            second = Entity("bit", memory_service=MemoryService("bit", second_repo))
            second_runtime = Runtime([second])
            register_user_message_handler(second, ProviderRouter(remote=second_provider))
            await send(second_runtime, "What is my name?")
            name_context = second_provider.requests[0].context
            self.assertEqual(name_context["immediate_conversation"], [])
            preferred = next(
                memory for memory in name_context["memories"]
                if memory["canonical_key"] == "user.preferred_name"
            )
            self.assertIn("Tucker", preferred["content"])
            self.assertEqual(preferred["source_type"], "user_explicit")
            await send(second_runtime, "What were we talking about recently?")
            episodic = [
                item for item in second_provider.requests[1].context["memories"]
                if item.get("memory_type") == "episodic"
            ]
            self.assertTrue(any("orbital gardening" in item["content"] for item in episodic))
            second_repo.close()

    async def test_fresh_database_has_fresh_entity_memory(self) -> None:
        with TemporaryDirectory() as directory:
            repository = SQLiteMemoryRepository(Path(directory) / "fresh.sqlite3")
            provider = MockProvider(response="I don't know.")
            entity = Entity("bit", memory_service=MemoryService("bit", repository))
            runtime = Runtime([entity])
            register_user_message_handler(entity, ProviderRouter(remote=provider))
            await send(runtime, "What is my name?")
            self.assertFalse(
                any(item["canonical_key"] == "user.preferred_name" for item in provider.requests[0].context["memories"])
            )
            repository.close()


if __name__ == "__main__":
    unittest.main()
