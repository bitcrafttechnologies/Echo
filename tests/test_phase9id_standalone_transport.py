from __future__ import annotations

import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import textwrap
import sys
import unittest

from echo import Action, AuthorizedRequirements, EchoNodeWebSocketHost, RemoteNodeReachability, Runtime
from medulla_node import (
    DevelopmentAdapter,
    EchoConnectionConfig,
    MedullaNodeConfig,
    MedullaNodeRuntime,
    NodeReachability,
    NodeTransportState,
)
from medulla_protocol import NodeActionResultState


class StandaloneNodeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_websocket_manifest_action_signal_and_heartbeat(self) -> None:
        echo_runtime = Runtime()
        host = EchoNodeWebSocketHost(
            "ws://127.0.0.1:0/medulla",
            signal_target=echo_runtime,
        )
        await host.start()
        config = MedullaNodeConfig(
            node_id="development-node",
            display_name="Development Node",
            echo=EchoConnectionConfig(
                endpoint=host.endpoint,
                heartbeat_interval=0.03,
                heartbeat_timeout=0.15,
                reconnect_initial_delay=0.02,
                reconnect_max_delay=0.05,
            ),
        )
        runtime = MedullaNodeRuntime(config)
        runtime.register_adapter(DevelopmentAdapter(config.provider))
        try:
            await runtime.start()
            self.assertTrue(await host.wait_for_node("development-node", 2))
            manifest = host.manifest("development-node")
            self.assertIsNotNone(manifest)
            self.assertEqual(manifest.node.display_name, "Development Node")
            self.assertEqual(manifest.capabilities[1].name, "counter.increment")

            await host.approve("development-node")
            await host.authorize("development-node", AuthorizedRequirements())

            result = await host.execute(
                "development-node",
                Action(
                    type="counter.increment",
                    parameters={"amount": 2, "resource_id": "counter.main"},
                ),
            )
            signal = await asyncio.wait_for(host.receive_signal(), 2)
            self.assertEqual(result.state, NodeActionResultState.COMPLETED)
            self.assertEqual(result.result, {"value": 2})
            self.assertEqual(signal.type, "counter.changed")
            self.assertEqual(signal.payload, {"value": 2})
            self.assertEqual(signal.metadata["adapter_id"], "development")
            self.assertEqual(echo_runtime.signals[-1].id, signal.id)

            async with asyncio.timeout(2):
                while runtime.status().transport["reachability"] != NodeReachability.HEALTHY.value:
                    await asyncio.sleep(0.01)
            self.assertEqual(
                host.status("development-node").reachability,
                RemoteNodeReachability.HEALTHY,
            )
        finally:
            await runtime.stop()
            await host.stop()
            echo_runtime.stop()
        self.assertEqual(runtime.status().transport["state"], NodeTransportState.STOPPED.value)

    async def test_node_reconnects_and_reannounces_manifest(self) -> None:
        first_host = EchoNodeWebSocketHost("ws://127.0.0.1:0/medulla")
        await first_host.start()
        endpoint = first_host.endpoint
        config = MedullaNodeConfig(
            node_id="reconnecting-node",
            echo=EchoConnectionConfig(
                endpoint=endpoint,
                heartbeat_interval=0.03,
                heartbeat_timeout=0.15,
                reconnect_initial_delay=0.02,
                reconnect_max_delay=0.05,
            ),
        )
        runtime = MedullaNodeRuntime(config)
        runtime.register_adapter(DevelopmentAdapter(config.provider))
        second_host = None
        try:
            await runtime.start()
            self.assertTrue(await first_host.wait_for_node("reconnecting-node", 2))
            await first_host.stop()
            async with asyncio.timeout(2):
                while runtime.status().transport["state"] == NodeTransportState.CONNECTED.value:
                    await asyncio.sleep(0.01)

            second_host = EchoNodeWebSocketHost(endpoint)
            await second_host.start()
            self.assertTrue(await second_host.wait_for_node("reconnecting-node", 3))
            self.assertIsNotNone(second_host.manifest("reconnecting-node"))
            self.assertGreaterEqual(runtime.status().transport["connections"], 2)
        finally:
            await runtime.stop()
            await first_host.stop()
            if second_host is not None:
                await second_host.stop()

    async def test_echo_and_cli_node_run_as_separate_processes(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        host = EchoNodeWebSocketHost("ws://127.0.0.1:0/medulla")
        await host.start()
        process = None
        with TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "node.yaml"
            config_path.write_text(
                textwrap.dedent(
                    f"""
                    node:
                      id: process-node
                      name: Process Node
                      type: edge
                    provider:
                      id: process-node
                    transport:
                      type: websocket
                    metadata:
                      implementation: medulla-node
                      version: 0.4
                    echo:
                      transport: websocket
                      endpoint: {host.endpoint}
                      heartbeat_interval: 0.05
                      heartbeat_timeout: 0.25
                      reconnect_initial_delay: 0.02
                      reconnect_max_delay: 0.05
                    capabilities: []
                    signals: []
                    resources: []
                    """
                ).strip() + "\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(repository / "src")
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m", "medulla_node", "run", os.fspath(config_path), "--development",
                    cwd=temporary,
                    env=environment,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                self.assertTrue(await host.wait_for_node("process-node", 3))
                self.assertEqual(host.manifest("process-node").node.display_name, "Process Node")
                await host.approve("process-node")
                await host.authorize("process-node", AuthorizedRequirements())
                result = await host.execute(
                    "process-node",
                    Action(type="counter.increment", parameters={"resource_id": "counter.main"}),
                    timeout=3,
                )
                signal = await asyncio.wait_for(host.receive_signal(), 3)
                self.assertEqual(result.result, {"value": 1})
                self.assertEqual(signal.type, "counter.changed")
            finally:
                if process is not None and process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 2)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
                await host.stop()


if __name__ == "__main__":
    unittest.main()
