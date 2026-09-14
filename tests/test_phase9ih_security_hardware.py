from __future__ import annotations

import asyncio
import json
import unittest

from echo import (
    Action,
    AuthorizedRequirements,
    ConnectionNegotiationState,
    ConnectionRequirements,
    EchoNodeWebSocketHost,
    EntityIdentityBasic,
    EntityIdentityRequirement,
    EntityIdentityScope,
    NodeApprovalMode,
    SecretReference,
    parse_config,
)
from medulla_node import EchoConnectionConfig, GpioAdapter, MedullaNodeConfig, MedullaNodeRuntime
from medulla_node import MedullaAdapter
from medulla_protocol import (
    Capability,
    CapabilityAvailability,
    CapabilityAvailabilityState,
    CapabilityEffect,
    CapabilityPermissions,
    CapabilityRisk,
    CapabilityTimeout,
    NodeAction,
)


class FakeGpioBackend:
    def __init__(self) -> None:
        self.outputs: dict[int, bool] = {}
        self.inputs = {}
        self.closed = False

    def setup_output(self, pin: int) -> None:
        self.outputs[pin] = False

    def setup_input(self, pin: int, changed) -> None:
        self.inputs[pin] = changed

    def write(self, pin: int, enabled: bool) -> None:
        self.outputs[pin] = enabled

    def trigger(self, pin: int, active: bool) -> None:
        self.inputs[pin](active)

    def close(self) -> None:
        self.closed = True


class SecurityContractTests(unittest.TestCase):
    def test_secret_references_round_trip_without_secret_values(self) -> None:
        authorization = AuthorizedRequirements(
            credentials={
                "weather_api_key": SecretReference("secret://weather_api_key")
            }
        )
        encoded = json.dumps(authorization.to_dict(), allow_nan=False)
        self.assertIn("secret://weather_api_key", encoded)
        self.assertNotIn("actual-secret-value", encoded)
        self.assertEqual(
            authorization.public_summary(),
            {
                "identity_scopes": [],
                "metadata_keys": [],
                "credential_ids": ["weather_api_key"],
                "permissions": [],
                "protocol_features": [],
                "entity_capabilities": [],
                "session_features": [],
            },
        )
        self.assertEqual(
            AuthorizedRequirements.from_dict(json.loads(encoded)), authorization
        )
        with self.assertRaisesRegex(ValueError, "secret reference"):
            AuthorizedRequirements(credentials={"weather_api_key": "plaintext"})

    def test_invalid_credential_reference_fails_authentication(self) -> None:
        requirements = ConnectionRequirements.from_dict(
            {
                "credentials": [
                    {"id": "weather_api_key", "type": "secret", "required": True}
                ]
            }
        )
        authorization = AuthorizedRequirements(
            credentials={"weather_api_key": "secret://different_secret"}
        )
        failure = authorization.validate_for(requirements)
        self.assertEqual(failure[0], ConnectionNegotiationState.AUTH_FAILED)

    def test_manual_and_autonomous_approval_configuration(self) -> None:
        self.assertEqual(
            parse_config({"discovery": {"approval_mode": "manual"}}).discovery.approval_mode,
            NodeApprovalMode.MANUAL,
        )
        self.assertEqual(
            parse_config({"discovery": {"approval_mode": "autonomous"}}).discovery.approval_mode,
            NodeApprovalMode.AUTONOMOUS,
        )


class GpioEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def test_approved_action_and_physical_input_signal_path(self) -> None:
        host = EchoNodeWebSocketHost("ws://127.0.0.1:0/medulla")
        await host.start()
        backend = FakeGpioBackend()
        requirements = ConnectionRequirements(
            identity=EntityIdentityRequirement(
                required=True,
                scopes=(EntityIdentityScope.BASIC,),
            )
        )
        config = MedullaNodeConfig(
            node_id="workshop-pi",
            display_name="Workshop Pi",
            echo=EchoConnectionConfig(
                endpoint=host.endpoint,
                heartbeat_interval=0.03,
                heartbeat_timeout=0.2,
            ),
            connection_requirements=requirements,
        )
        runtime = MedullaNodeRuntime(config)
        runtime.register_adapter(
            GpioAdapter(
                config.provider,
                output_pins=(17,),
                input_pins=(27,),
                backend=backend,
            )
        )
        try:
            await runtime.start()
            self.assertTrue(await host.wait_for_node("workshop-pi", 2))
            self.assertEqual(backend.outputs[17], False)
            with self.assertRaises(PermissionError):
                await host.execute(
                    "workshop-pi",
                    Action(
                        type="gpio.output.set",
                        parameters={"enabled": True, "resource_id": "gpio.pin.17"},
                    ),
                )
            self.assertEqual(backend.outputs[17], False)
            self.assertIsNone(host.directory.status("workshop-pi"))

            await host.approve("workshop-pi", reason="user requested GPIO control")
            await host.authorize(
                "workshop-pi",
                AuthorizedRequirements(
                    identity_basic=EntityIdentityBasic(
                        entity_id="echo-main",
                        display_name="Echo",
                        entity_type="assistant",
                    )
                ),
            )
            self.assertEqual(
                await asyncio.wait_for(host.receive_announcement(), 1),
                "Workshop Pi connected.",
            )
            result = await host.execute(
                "workshop-pi",
                Action(
                    type="gpio.output.set",
                    parameters={"enabled": True, "resource_id": "gpio.pin.17"},
                ),
            )
            output_signal = await asyncio.wait_for(host.receive_signal(), 1)
            self.assertEqual(result.result, {"pin": 17, "enabled": True})
            self.assertTrue(backend.outputs[17])
            self.assertEqual(output_signal.type, "gpio.output.changed")

            backend.trigger(27, True)
            input_signal = await asyncio.wait_for(host.receive_signal(), 1)
            self.assertEqual(input_signal.type, "gpio.input.changed")
            self.assertEqual(input_signal.payload, {"pin": 27, "active": True})
            audit = host.inspect_node("workshop-pi")
            self.assertEqual(audit["approval_mode"], "manual")
            self.assertEqual(audit["decision_reason"], "user requested GPIO control")
            self.assertEqual(audit["negotiation_state"], "active")
            self.assertIsNotNone(audit["active_at"])
        finally:
            await runtime.stop()
            await host.stop()
        self.assertTrue(backend.closed)

    async def test_disconnect_during_action_fails_the_echo_waiter(self) -> None:
        host = EchoNodeWebSocketHost("ws://127.0.0.1:0/medulla")
        await host.start()
        config = MedullaNodeConfig(
            node_id="slow-node",
            echo=EchoConnectionConfig(
                endpoint=host.endpoint,
                heartbeat_interval=0.03,
                heartbeat_timeout=0.2,
            ),
        )
        started = asyncio.Event()

        class SlowAdapter(MedullaAdapter):
            async def execute(self, action: NodeAction):
                started.set()
                await asyncio.Event().wait()

        capability = Capability(
            capability_id="slow-node.slow.action.v1",
            name="slow.action",
            description="Never completes",
            provider=config.provider,
            input_schema={},
            output_schema={},
            availability=CapabilityAvailability(state=CapabilityAvailabilityState.AVAILABLE),
            permissions=CapabilityPermissions(risk=CapabilityRisk.NONE),
            effect=CapabilityEffect.READ_ONLY,
            timeout=CapabilityTimeout(expected_seconds=1, maximum_seconds=30),
        )
        runtime = MedullaNodeRuntime(config)
        runtime.register_adapter(SlowAdapter(adapter_id="slow", capabilities=(capability,)))
        try:
            await runtime.start()
            self.assertTrue(await host.wait_for_node("slow-node", 2))
            await host.approve("slow-node")
            await host.authorize("slow-node", AuthorizedRequirements())
            pending = asyncio.create_task(
                host.execute("slow-node", Action(type="slow.action"), timeout=5)
            )
            await asyncio.wait_for(started.wait(), 1)
            await runtime.stop()
            with self.assertRaises(ConnectionError):
                await pending
        finally:
            await runtime.stop()
            await host.stop()

    async def test_reconnect_with_changed_identity_is_not_reoffered(self) -> None:
        host = EchoNodeWebSocketHost("ws://127.0.0.1:0/medulla")
        await host.start()

        def make_runtime(display_name: str) -> MedullaNodeRuntime:
            return MedullaNodeRuntime(
                MedullaNodeConfig(
                    node_id="stable-node",
                    display_name=display_name,
                    echo=EchoConnectionConfig(
                        endpoint=host.endpoint,
                        heartbeat_interval=0.03,
                        heartbeat_timeout=0.2,
                        reconnect_initial_delay=0.02,
                        reconnect_max_delay=0.04,
                    ),
                )
            )

        original = make_runtime("Original Identity")
        changed = make_runtime("Changed Identity")
        try:
            await original.start()
            self.assertTrue(await host.wait_for_node("stable-node", 2))
            await original.stop()
            async with asyncio.timeout(1):
                while host.status("stable-node").reachability.value != "unreachable":
                    await asyncio.sleep(0.01)
            await changed.start()
            await asyncio.sleep(0.12)
            self.assertEqual(
                host.manifest("stable-node").node.display_name,
                "Original Identity",
            )
            requests = [
                event for event in host.lifecycle_events
                if event.type.value == "NodeApprovalRequested"
            ]
            self.assertEqual(len(requests), 1)
            self.assertIsNone(host.directory.status("stable-node"))
        finally:
            await original.stop()
            await changed.stop()
            await host.stop()


if __name__ == "__main__":
    unittest.main()
