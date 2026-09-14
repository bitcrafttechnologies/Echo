from __future__ import annotations

import asyncio
import json
import unittest

from medulla_node import (
    DevelopmentAdapter,
    MedullaAdapter,
    MedullaNodeConfig,
    MedullaNodeLifecycle,
    MedullaNodeRuntime,
)
from medulla_protocol import (
    AdapterEvent,
    Capability,
    CapabilityAvailability,
    CapabilityAvailabilityState,
    CapabilityEffect,
    CapabilityPermissions,
    CapabilityRisk,
    CapabilityTimeout,
    MedullaNodeSignal,
    MedullaNodeType,
    NodeAction,
    NodeActionErrorCode,
    NodeActionResult,
    NodeActionResultState,
    NodeSignalEnvelope,
)


def make_runtime() -> tuple[MedullaNodeRuntime, DevelopmentAdapter]:
    config = MedullaNodeConfig(
        node_id="development-node",
        node_type=MedullaNodeType.EDGE,
        display_name="Development Node",
    )
    adapter = DevelopmentAdapter(config.provider)
    runtime = MedullaNodeRuntime(config)
    runtime.register_adapter(adapter)
    return runtime, adapter


class AdapterRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_development_manifest_is_aggregated_before_start(self) -> None:
        runtime, _adapter = make_runtime()
        manifest = runtime.manifest()
        self.assertEqual(
            tuple(item.name for item in manifest.capabilities),
            ("dev.echo", "counter.increment", "counter.reset"),
        )
        self.assertEqual(tuple(item.name for item in manifest.signals), ("counter.changed",))
        self.assertEqual(tuple(item.resource_id for item in manifest.resources), ("counter.main",))
        self.assertEqual(runtime.status().lifecycle, MedullaNodeLifecycle.CREATED)

    async def test_counter_increment_produces_normalized_correlated_signal(self) -> None:
        runtime, adapter = make_runtime()
        await runtime.start()
        action = NodeAction(
            type="counter.increment",
            parameters={"amount": 2},
            resource_id="counter.main",
            correlation_id="request-7",
        )
        result = await runtime.execute(action)
        signal = await asyncio.wait_for(runtime.receive_signal(), 1)

        self.assertEqual(result.state, NodeActionResultState.COMPLETED)
        self.assertEqual(result.adapter_id, "development")
        self.assertEqual(result.result, {"value": 2})
        self.assertEqual(NodeActionResult.from_dict(result.to_dict()), result)
        self.assertEqual(adapter.counter, 2)
        self.assertEqual(signal.signal_type, "counter.changed")
        self.assertEqual(signal.payload, {"value": 2})
        self.assertEqual(signal.node_id, "development-node")
        self.assertEqual(signal.provider_id, "development-node")
        self.assertEqual(signal.adapter_id, "development")
        self.assertEqual(signal.resource_id, "counter.main")
        self.assertEqual(signal.correlation_id, "request-7")
        self.assertEqual(signal.sequence, 1)
        self.assertEqual(NodeSignalEnvelope.from_dict(signal.to_dict()), signal)
        json.dumps(signal.to_dict(), allow_nan=False)
        await runtime.stop()

    async def test_echo_reset_and_sequence_are_deterministic(self) -> None:
        runtime, adapter = make_runtime()
        await runtime.start()
        echo = await runtime.execute(NodeAction(type="dev.echo", parameters={"value": 4}))
        increment = await runtime.execute(
            NodeAction(type="development-node.counter.increment.v1")
        )
        reset = await runtime.execute(NodeAction(type="counter.reset"))
        first = await runtime.receive_signal()
        second = await runtime.receive_signal()
        self.assertEqual(echo.result, {"echo": {"value": 4}})
        self.assertEqual(increment.result, {"value": 1})
        self.assertEqual(reset.result, {"value": 0})
        self.assertEqual(adapter.counter, 0)
        self.assertEqual((first.sequence, second.sequence), (1, 2))
        await runtime.stop()

    async def test_unknown_action_invalid_resource_and_stopped_node_fail_closed(self) -> None:
        runtime, _adapter = make_runtime()
        stopped = await runtime.execute(NodeAction(type="counter.increment"))
        self.assertEqual(stopped.state, NodeActionResultState.REJECTED)
        self.assertEqual(stopped.error.code, NodeActionErrorCode.NOT_RUNNING)

        await runtime.start()
        unknown = await runtime.execute(NodeAction(type="system.shell"))
        invalid_resource = await runtime.execute(
            NodeAction(type="counter.increment", resource_id="counter.secret")
        )
        self.assertEqual(unknown.state, NodeActionResultState.REJECTED)
        self.assertEqual(unknown.error.code, NodeActionErrorCode.UNKNOWN_ACTION)
        self.assertEqual(invalid_resource.state, NodeActionResultState.REJECTED)
        self.assertEqual(invalid_resource.error.code, NodeActionErrorCode.INVALID_RESOURCE)
        self.assertEqual(runtime.status().queued_signals, 0)
        await runtime.stop()

    async def test_adapter_failures_and_undeclared_events_are_contained(self) -> None:
        config = MedullaNodeConfig(node_id="failing-node")
        capability = Capability(
            capability_id="failing-node.fail.v1",
            name="fail",
            description="Fail deterministically",
            provider=config.provider,
            input_schema={},
            output_schema={},
            availability=CapabilityAvailability(state=CapabilityAvailabilityState.AVAILABLE),
            permissions=CapabilityPermissions(risk=CapabilityRisk.NONE),
            effect=CapabilityEffect.READ_ONLY,
            timeout=CapabilityTimeout(expected_seconds=0.1, maximum_seconds=1),
        )

        class FailingAdapter(MedullaAdapter):
            async def execute(self, action: NodeAction):
                await self.emit(AdapterEvent(signal_type="undeclared", correlation_id=action.id))
                return {}

        runtime = MedullaNodeRuntime(config)
        runtime.register_adapter(
            FailingAdapter(
                adapter_id="failing",
                capabilities=(capability,),
                signals=(MedullaNodeSignal(name="declared", schema="declared/v1"),),
            )
        )
        await runtime.start()
        result = await runtime.execute(NodeAction(type="fail"))
        self.assertEqual(result.state, NodeActionResultState.FAILED)
        self.assertEqual(result.error.code, NodeActionErrorCode.EXECUTION_FAILED)
        self.assertEqual(runtime.status().queued_signals, 0)
        await runtime.stop()

    async def test_action_serialization_is_closed_and_round_trips(self) -> None:
        action = NodeAction(
            type="counter.increment",
            parameters={"amount": 3},
            resource_id="counter.main",
            correlation_id="job-1",
        )
        self.assertEqual(NodeAction.from_dict(json.loads(json.dumps(action.to_dict()))), action)
        unsafe = action.to_dict()
        unsafe["callback"] = "module:function"
        with self.assertRaisesRegex(ValueError, "closed"):
            NodeAction.from_dict(unsafe)


if __name__ == "__main__":
    unittest.main()
