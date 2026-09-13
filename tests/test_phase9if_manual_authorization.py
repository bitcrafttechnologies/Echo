from __future__ import annotations

import asyncio
import unittest

from echo import (
    Action,
    AuthorizedRequirements,
    ConnectionNegotiationState,
    ConnectionRequirements,
    CredentialRequirement,
    EchoNodeWebSocketHost,
    EntityIdentityBasic,
    EntityIdentityRequirement,
    EntityIdentityScope,
    NodeAuthorizationError,
    NodeLifecycleEventType,
)
from medulla_node import (
    DevelopmentAdapter,
    EchoConnectionConfig,
    MedullaNodeConfig,
    MedullaNodeRuntime,
)


class ManualAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.host = EchoNodeWebSocketHost("ws://127.0.0.1:0/medulla")
        await self.host.start()

    async def asyncTearDown(self) -> None:
        await self.host.stop()

    def runtime(self, node_id: str, requirements: ConnectionRequirements) -> MedullaNodeRuntime:
        config = MedullaNodeConfig(
            node_id=node_id,
            display_name="Workshop Pi",
            echo=EchoConnectionConfig(
                endpoint=self.host.endpoint,
                heartbeat_interval=0.03,
                heartbeat_timeout=0.2,
                reconnect_initial_delay=0.02,
                reconnect_max_delay=0.05,
            ),
            connection_requirements=requirements,
        )
        runtime = MedullaNodeRuntime(config)
        runtime.register_adapter(DevelopmentAdapter(config.provider))
        return runtime

    async def test_manual_approval_scope_authorization_and_active_gate(self) -> None:
        requirements = ConnectionRequirements(
            identity=EntityIdentityRequirement(
                required=True,
                scopes=(EntityIdentityScope.BASIC,),
            ),
        )
        runtime = self.runtime("workshop-pi", requirements)
        try:
            await runtime.start()
            self.assertTrue(await self.host.wait_for_node("workshop-pi", 2))
            request = self.host.approval_request("workshop-pi")
            self.assertIn("Workshop Pi", request.render())
            self.assertIn("identity.basic", request.render())
            self.assertIsNone(self.host.directory.status("workshop-pi"))
            self.assertIsNone(runtime.authorized_requirements())

            with self.assertRaises(NodeAuthorizationError):
                await self.host.execute("workshop-pi", Action(type="counter.increment"))

            await self.host.approve("workshop-pi", reason="the user selected this node")
            self.assertEqual(
                self.host.status("workshop-pi").negotiation_state,
                ConnectionNegotiationState.APPROVED,
            )
            with self.assertRaises(NodeAuthorizationError):
                await self.host.execute("workshop-pi", Action(type="counter.increment"))

            identity = EntityIdentityBasic(
                entity_id="echo-main",
                display_name="Echo",
                entity_type="assistant",
            )
            await self.host.authorize(
                "workshop-pi",
                AuthorizedRequirements(identity_basic=identity),
            )
            self.assertEqual(
                self.host.status("workshop-pi").negotiation_state,
                ConnectionNegotiationState.ACTIVE,
            )
            self.assertEqual(runtime.authorized_requirements().identity_basic, identity)
            self.assertIsNotNone(self.host.directory.status("workshop-pi"))
            self.assertEqual(
                await asyncio.wait_for(self.host.receive_announcement(), 1),
                "Workshop Pi connected.",
            )

            result = await self.host.execute(
                "workshop-pi",
                Action(
                    type="counter.increment",
                    parameters={"resource_id": "counter.main"},
                ),
            )
            signal = await asyncio.wait_for(self.host.receive_signal(), 1)
            self.assertEqual(result.result, {"value": 1})
            self.assertEqual(signal.type, "counter.changed")
            event_types = {event.type for event in self.host.lifecycle_events}
            self.assertTrue(
                {
                    NodeLifecycleEventType.NODE_APPROVAL_REQUESTED,
                    NodeLifecycleEventType.NODE_APPROVED,
                    NodeLifecycleEventType.NODE_REQUIREMENTS_AUTHORIZED,
                    NodeLifecycleEventType.NODE_CONNECTED,
                } <= event_types
            )
        finally:
            await runtime.stop()

    async def test_decline_is_reconsiderable_and_block_is_persistent(self) -> None:
        runtime = self.runtime("candidate-node", ConnectionRequirements())
        try:
            await runtime.start()
            self.assertTrue(await self.host.wait_for_node("candidate-node", 2))
            await self.host.decline("candidate-node", reason="not relevant now")
            self.assertEqual(
                self.host.status("candidate-node").negotiation_state,
                ConnectionNegotiationState.DECLINED,
            )
            await self.host.reconsider("candidate-node")
            self.assertEqual(
                self.host.status("candidate-node").negotiation_state,
                ConnectionNegotiationState.AWAITING_APPROVAL,
            )
            await self.host.block("candidate-node", reason="operator blocked device")
            self.assertTrue(self.host.is_blocked("candidate-node"))
            await asyncio.sleep(0.12)
            requested = [
                event for event in self.host.lifecycle_events
                if event.type is NodeLifecycleEventType.NODE_APPROVAL_REQUESTED
            ]
            self.assertEqual(len(requested), 2)
            with self.assertRaises(NodeAuthorizationError):
                await self.host.approve("candidate-node")
            self.assertTrue(self.host.unblock("candidate-node"))
        finally:
            await runtime.stop()

    async def test_requirement_authorization_is_not_implicit(self) -> None:
        requirements = ConnectionRequirements(
            credentials=(
                CredentialRequirement(
                    credential_id="weather_api_key",
                    type="secret",
                    required=True,
                ),
            ),
        )
        runtime = self.runtime("weather-node", requirements)
        try:
            await runtime.start()
            self.assertTrue(await self.host.wait_for_node("weather-node", 2))
            await self.host.approve("weather-node")
            with self.assertRaises(NodeAuthorizationError) as raised:
                await self.host.authorize("weather-node", AuthorizedRequirements())
            self.assertEqual(raised.exception.state, ConnectionNegotiationState.UNSATISFIED)
            self.assertIsNone(self.host.directory.status("weather-node"))
        finally:
            await runtime.stop()


if __name__ == "__main__":
    unittest.main()
