from __future__ import annotations

import asyncio
import unittest

from echo import (
    AuthorizedRequirements,
    AutonomousApproval,
    ConnectionNegotiationState,
    EchoNodeWebSocketHost,
    NodeApprovalDecision,
    NodeApprovalMode,
    NodeCandidateContext,
    StaticRequirementAuthorizationPolicy,
)
from medulla_node import EchoConnectionConfig, MedullaNodeAdapter, MedullaNodeConfig, MedullaNodeRuntime
from medulla_protocol import (
    Capability,
    CapabilityAvailability,
    CapabilityAvailabilityState,
    CapabilityEffect,
    CapabilityPermissions,
    CapabilityRisk,
    CapabilityTimeout,
)


class RelevanceEvaluator:
    async def evaluate(self, request, context: NodeCandidateContext) -> AutonomousApproval:
        relevant = set(request.provides) & set(context.missing_capabilities)
        if relevant:
            capability = sorted(relevant)[0]
            return AutonomousApproval(
                decision=NodeApprovalDecision.APPROVE,
                reason=f"the current task requires {capability}",
            )
        return AutonomousApproval(
            decision=NodeApprovalDecision.DECLINE,
            reason="its capabilities are not relevant to the current task",
        )


def runtime_for(host, node_id: str, capability_name: str) -> MedullaNodeRuntime:
    config = MedullaNodeConfig(
        node_id=node_id,
        display_name=node_id.replace("-", " ").title(),
        echo=EchoConnectionConfig(
            endpoint=host.endpoint,
            heartbeat_interval=0.03,
            heartbeat_timeout=0.2,
        ),
    )
    provider = config.provider
    capability = Capability(
        capability_id=f"{node_id}.{capability_name}.v1",
        name=capability_name,
        description=capability_name,
        provider=provider,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        availability=CapabilityAvailability(state=CapabilityAvailabilityState.AVAILABLE),
        permissions=CapabilityPermissions(risk=CapabilityRisk.NONE),
        effect=CapabilityEffect.READ_ONLY,
        timeout=CapabilityTimeout(expected_seconds=0.1, maximum_seconds=1),
    )
    runtime = MedullaNodeRuntime(config)
    runtime.register_adapter(
        MedullaNodeAdapter(adapter_id=f"{node_id}-adapter", capabilities=(capability,))
    )
    return runtime


async def eventually(predicate, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


class AutonomousApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_nodes_are_evaluated_independently_and_decline_can_change(self) -> None:
        context = NodeCandidateContext(
            current_tasks=("inspect loading dock",),
            missing_capabilities=("camera.capture",),
        )
        host = EchoNodeWebSocketHost(
            "ws://127.0.0.1:0/medulla",
            approval_mode=NodeApprovalMode.AUTONOMOUS,
            autonomous_evaluator=RelevanceEvaluator(),
            authorization_policy=StaticRequirementAuthorizationPolicy(
                {
                    "camera-node": AuthorizedRequirements(),
                    "printer-node": AuthorizedRequirements(),
                }
            ),
            candidate_context=lambda: context,
        )
        await host.start()
        camera = runtime_for(host, "camera-node", "camera.capture")
        printer = runtime_for(host, "printer-node", "document.print")
        try:
            await asyncio.gather(camera.start(), printer.start())
            self.assertTrue(await host.wait_for_node("camera-node", 2))
            self.assertTrue(await host.wait_for_node("printer-node", 2))
            await eventually(
                lambda: host.status("camera-node").negotiation_state
                is ConnectionNegotiationState.ACTIVE
            )
            await eventually(
                lambda: host.status("printer-node").negotiation_state
                is ConnectionNegotiationState.DECLINED
            )
            self.assertIsNotNone(host.directory.status("camera-node"))
            self.assertIsNone(host.directory.status("printer-node"))
            camera_announcement = await asyncio.wait_for(host.receive_announcement(), 1)
            self.assertIn("current task requires camera.capture", camera_announcement)
            self.assertEqual(
                host.inspect_node("printer-node")["decision_reason"],
                "its capabilities are not relevant to the current task",
            )

            await host.reevaluate(
                "printer-node",
                context=NodeCandidateContext(
                    current_tasks=("print document",),
                    missing_capabilities=("document.print",),
                ),
            )
            self.assertEqual(
                host.status("printer-node").negotiation_state,
                ConnectionNegotiationState.ACTIVE,
            )
            printer_announcement = await asyncio.wait_for(host.receive_announcement(), 1)
            self.assertIn("current task requires document.print", printer_announcement)
        finally:
            await asyncio.gather(camera.stop(), printer.stop())
            await host.stop()

    async def test_autonomous_mode_without_evaluator_never_auto_connects(self) -> None:
        host = EchoNodeWebSocketHost(
            "ws://127.0.0.1:0/medulla",
            approval_mode=NodeApprovalMode.AUTONOMOUS,
        )
        await host.start()
        runtime = runtime_for(host, "unreviewed-node", "camera.capture")
        try:
            await runtime.start()
            self.assertTrue(await host.wait_for_node("unreviewed-node", 2))
            await asyncio.sleep(0.05)
            self.assertEqual(
                host.status("unreviewed-node").negotiation_state,
                ConnectionNegotiationState.AWAITING_APPROVAL,
            )
            self.assertIsNone(host.directory.status("unreviewed-node"))
        finally:
            await runtime.stop()
            await host.stop()


if __name__ == "__main__":
    unittest.main()
