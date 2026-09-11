from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echo import Action, Signal
from echo.medulla import (
    ActionDispatchResult,
    ActionDispatchState,
    Capability,
    CapabilityAvailability,
    CapabilityAvailabilityState,
    CapabilityEffect,
    CapabilityPermissionDecision,
    CapabilityPermissions,
    CapabilityProvider,
    CapabilityProviderHealth,
    CapabilityProviderHealthState,
    CapabilityProviderKind,
    CapabilityProviderLocation,
    CapabilityRegistry,
    CapabilityRegistryError,
    CapabilityRisk,
    CapabilityRouteBinding,
    CapabilityRouter,
    CapabilityTimeout,
    LocalQueueTransport,
    MedullaSupervisor,
    TransportErrorCode,
)


def provider(
    provider_id: str,
    *,
    kind: CapabilityProviderKind = CapabilityProviderKind.NATIVE,
) -> CapabilityProvider:
    return CapabilityProvider(
        provider_id=provider_id,
        kind=kind,
        location=CapabilityProviderLocation.LOCAL,
    )


def capability(
    provider_value: CapabilityProvider,
    *,
    name: str = "filesystem.read",
    availability: CapabilityAvailabilityState = CapabilityAvailabilityState.AVAILABLE,
    timeout: float = 1.0,
) -> Capability:
    return Capability(
        capability_id=f"{provider_value.provider_id}.{name}.v1",
        name=name,
        description=f"Execute {name}",
        provider=provider_value,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        availability=CapabilityAvailability(state=availability),
        permissions=CapabilityPermissions(risk=CapabilityRisk.READ),
        effect=CapabilityEffect.READ_ONLY,
        timeout=CapabilityTimeout(
            expected_seconds=min(0.1, timeout), maximum_seconds=timeout
        ),
    )


class AllowPermissions:
    def __init__(self, denied_provider_ids: set[str] | None = None) -> None:
        self.denied_provider_ids = denied_provider_ids or set()
        self.calls: list[str] = []

    async def __call__(
        self, action: Action, selected: Capability
    ) -> CapabilityPermissionDecision:
        self.calls.append(selected.capability_id)
        if selected.provider.provider_id in self.denied_provider_ids:
            return CapabilityPermissionDecision(allowed=False, reason="not authorized")
        return CapabilityPermissionDecision(allowed=True)


class RecordingDispatcher:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        delay: float = 0,
        failed: bool = False,
    ) -> None:
        self.error = error
        self.delay = delay
        self.failed = failed
        self.calls: list[tuple[Action, str | None]] = []

    async def dispatch(
        self, action: Action, *, transport_id: str | None = None
    ) -> ActionDispatchResult:
        self.calls.append((action, transport_id))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        if self.failed:
            from echo.medulla import TransportErrorInfo, TransportOperation

            return ActionDispatchResult(
                transport_id=transport_id or "missing",
                action_id=action.id,
                state=ActionDispatchState.FAILED,
                error=TransportErrorInfo(
                    transport_id=transport_id or "missing",
                    operation=TransportOperation.EXECUTE,
                    code=TransportErrorCode.CONNECTION_FAILED,
                    message="transport offline",
                    retryable=True,
                ),
            )
        return ActionDispatchResult(
            transport_id=transport_id or "missing",
            action_id=action.id,
            state=ActionDispatchState.COMPLETED,
            result={"ok": True},
        )


class SignalSink:
    async def emit(self, signal: Signal) -> None:
        pass


class RegistryTests(unittest.TestCase):
    def test_registration_tracks_ownership_lookup_health_and_introspection(self) -> None:
        local = provider("local-system")
        remote = provider("remote-system", kind=CapabilityProviderKind.MCP)
        local_capability = capability(local)
        remote_capability = capability(remote)
        registry = CapabilityRegistry((remote_capability, local_capability))
        health = CapabilityProviderHealth(
            provider_id="local-system",
            state=CapabilityProviderHealthState.HEALTHY,
            details={"latency_ms": 3},
        )

        registry.update_provider_health(health)

        self.assertEqual(registry.provider("local-system"), local)
        self.assertEqual(
            registry.capabilities_for_provider("local-system"), (local_capability,)
        )
        self.assertEqual(registry.find("filesystem.read"), (local_capability, remote_capability))
        self.assertEqual(registry.health("local-system"), health)
        restored = CapabilityRegistry.from_dict(json.loads(json.dumps(registry.to_dict())))
        self.assertEqual(restored.to_dict(), registry.to_dict())

    def test_capability_and_provider_removal_are_complete_and_idempotent(self) -> None:
        first_provider = provider("first")
        second_provider = provider("second")
        first = capability(first_provider)
        second = capability(second_provider)
        registry = CapabilityRegistry((first, second))

        self.assertEqual(registry.remove(first.capability_id), first)
        self.assertIsNone(registry.remove(first.capability_id))
        self.assertEqual(registry.find(first.name), (second,))
        self.assertEqual(registry.remove_provider("second"), (second,))
        self.assertEqual(registry.remove_provider("second"), ())
        self.assertIsNone(registry.provider("second"))
        self.assertIsNone(registry.health("second"))

    def test_one_provider_id_cannot_claim_conflicting_metadata(self) -> None:
        registry = CapabilityRegistry((capability(provider("shared")),))
        conflicting = provider("shared", kind=CapabilityProviderKind.MQTT)

        with self.assertRaisesRegex(CapabilityRegistryError, "conflicting metadata"):
            registry.register(capability(conflicting, name="system.execute"))


class RouterTests(unittest.IsolatedAsyncioTestCase):
    def create_router(
        self,
        capabilities: tuple[Capability, ...],
        dispatcher: RecordingDispatcher,
        permissions: AllowPermissions | None = None,
        *,
        priorities: dict[str, int] | None = None,
    ) -> tuple[CapabilityRegistry, CapabilityRouter, AllowPermissions]:
        registry = CapabilityRegistry(capabilities)
        hook = permissions or AllowPermissions()
        priorities = priorities or {}
        bindings = tuple(
            CapabilityRouteBinding(
                provider_id=item.provider_id,
                transport_id=f"transport-{item.provider_id}",
                priority=priorities.get(item.provider_id, 100),
            )
            for item in registry.providers()
        )
        return registry, CapabilityRouter(registry, dispatcher, hook, bindings), hook

    async def test_duplicate_names_route_by_stable_health_and_identity_order(self) -> None:
        alpha = capability(provider("alpha"))
        beta = capability(provider("beta"))
        dispatcher = RecordingDispatcher()
        registry, router, permissions = self.create_router((beta, alpha), dispatcher)
        registry.update_provider_health(
            CapabilityProviderHealth(
                provider_id="alpha", state=CapabilityProviderHealthState.HEALTHY
            )
        )
        registry.update_provider_health(
            CapabilityProviderHealth(
                provider_id="beta", state=CapabilityProviderHealthState.HEALTHY
            )
        )
        action = Action(type="filesystem.read", parameters={"path": "/tmp/x"})

        result = await router.dispatch(action)

        self.assertEqual(result.state, ActionDispatchState.COMPLETED)
        self.assertEqual(result.transport_id, "transport-alpha")
        self.assertEqual(len(dispatcher.calls), 1)
        self.assertEqual(
            permissions.calls, [alpha.capability_id, beta.capability_id]
        )

    async def test_availability_health_priority_and_explicit_provider_are_honored(self) -> None:
        alpha = capability(
            provider("alpha"), availability=CapabilityAvailabilityState.DEGRADED
        )
        beta = capability(provider("beta"))
        dispatcher = RecordingDispatcher()
        registry, router, _ = self.create_router(
            (alpha, beta), dispatcher, priorities={"alpha": 0, "beta": 100}
        )
        registry.update_provider_health(
            CapabilityProviderHealth(
                provider_id="alpha", state=CapabilityProviderHealthState.HEALTHY
            )
        )
        registry.update_provider_health(
            CapabilityProviderHealth(
                provider_id="beta", state=CapabilityProviderHealthState.HEALTHY
            )
        )

        automatic = await router.dispatch(Action(type="filesystem.read"))
        explicit = await router.dispatch(
            Action(type="filesystem.read"), provider_id="alpha"
        )
        registry.update_provider_health(
            CapabilityProviderHealth(
                provider_id="alpha",
                state=CapabilityProviderHealthState.UNAVAILABLE,
            )
        )
        unavailable = await router.dispatch(
            Action(type="filesystem.read"), provider_id="alpha"
        )

        self.assertEqual(automatic.transport_id, "transport-beta")
        self.assertEqual(explicit.transport_id, "transport-alpha")
        self.assertEqual(unavailable.error.code, TransportErrorCode.UNAVAILABLE)

    async def test_permission_denial_is_structured_and_never_reaches_transport(self) -> None:
        selected = capability(provider("local-system"))
        dispatcher = RecordingDispatcher()
        registry, router, _ = self.create_router(
            (selected,), dispatcher, AllowPermissions({"local-system"})
        )

        result = await router.dispatch(Action(type="filesystem.read"))

        self.assertEqual(result.state, ActionDispatchState.FAILED)
        self.assertEqual(result.error.code, TransportErrorCode.PERMISSION_DENIED)
        self.assertEqual(dispatcher.calls, [])
        self.assertEqual(registry.find("filesystem.read"), (selected,))

    async def test_provider_exception_and_transport_failure_are_contained(self) -> None:
        selected = capability(provider("remote"))
        exploding = RecordingDispatcher(error=RuntimeError("provider exploded"))
        _, router, _ = self.create_router((selected,), exploding)

        exception_result = await router.dispatch(Action(type="filesystem.read"))

        self.assertEqual(exception_result.state, ActionDispatchState.FAILED)
        self.assertEqual(
            exception_result.error.code, TransportErrorCode.EXECUTION_FAILED
        )
        self.assertIn("provider exploded", exception_result.error.message)

        failed = RecordingDispatcher(failed=True)
        _, router, _ = self.create_router((selected,), failed)
        transport_result = await router.dispatch(Action(type="filesystem.read"))
        self.assertEqual(
            transport_result.error.code, TransportErrorCode.CONNECTION_FAILED
        )
        self.assertTrue(transport_result.error.retryable)

    async def test_timeout_is_structured_and_does_not_try_another_provider(self) -> None:
        alpha = capability(provider("alpha"), timeout=0.01)
        beta = capability(provider("beta"), timeout=0.01)
        dispatcher = RecordingDispatcher(delay=0.1)
        _, router, _ = self.create_router((alpha, beta), dispatcher)

        result = await router.dispatch(Action(type="filesystem.read"))

        self.assertEqual(result.state, ActionDispatchState.FAILED)
        self.assertEqual(result.error.code, TransportErrorCode.TIMEOUT)
        self.assertTrue(result.error.retryable)
        self.assertEqual(len(dispatcher.calls), 1)

    async def test_router_introspection_and_removal_update_eligible_routes(self) -> None:
        selected = capability(provider("local-system"))
        dispatcher = RecordingDispatcher()
        registry, router, _ = self.create_router((selected,), dispatcher)

        before = router.status().to_dict()
        registry.remove(selected.capability_id)
        after = router.status().to_dict()
        result = await router.dispatch(Action(type="filesystem.read"))

        self.assertEqual(before["eligible_routes"][0]["capability_id"], selected.capability_id)
        self.assertEqual(after["eligible_routes"], [])
        self.assertEqual(result.error.code, TransportErrorCode.UNAVAILABLE)

    async def test_selected_route_dispatches_through_medulla_supervisor(self) -> None:
        selected = capability(provider("local-system"))
        registry = CapabilityRegistry((selected,))
        transport = LocalQueueTransport("local-actions")
        supervisor = MedullaSupervisor(SignalSink(), (transport,))
        router = CapabilityRouter(
            registry,
            supervisor,
            AllowPermissions(),
            (
                CapabilityRouteBinding(
                    provider_id="local-system", transport_id="local-actions"
                ),
            ),
        )
        await supervisor.start()
        try:
            action = Action(type="filesystem.read", parameters={"path": "/tmp/x"})
            result = await router.dispatch(action)
            received = await asyncio.wait_for(transport.receive_action(), timeout=1)

            self.assertEqual(result.state, ActionDispatchState.ACCEPTED)
            self.assertEqual(received.id, action.id)
            self.assertEqual(received.parameters, {"path": "/tmp/x"})
        finally:
            await supervisor.stop()


if __name__ == "__main__":
    unittest.main()
