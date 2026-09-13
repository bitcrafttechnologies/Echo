from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from medulla_node import (
    MedullaNodeAdapter,
    MedullaNodeRuntime,
    NodeConfigurationError,
    build_node_manifest,
    load_node_config,
)
from medulla_protocol import (
    Capability,
    CapabilityAvailability,
    CapabilityAvailabilityState,
    CapabilityEffect,
    CapabilityPermissions,
    CapabilityRisk,
    CapabilityTimeout,
    MedullaNodeManifest,
    MedullaNodeResource,
    MedullaNodeSignal,
    NodeProtocolError,
)


VALID_CONFIG = """protocol: medulla/1
node:
  id: workshop-pi
  name: Workshop Pi
  type: edge
provider:
  id: workshop-pi
transport:
  id: control
  type: websocket
  address: ws://workshop-pi.local:8765/medulla
  metadata:
    wire_version: 1
metadata:
  implementation: medulla-node
  version: 0.2
capabilities:
  - id: workshop-pi.temperature.read.v1
    name: temperature.read
    description: Read workshop temperature
    input_schema: {}
    output_schema: {"type": "object"}
    availability:
      state: available
    permissions:
      risk: read
      required: []
    effect: read_only
    timeout:
      expected_seconds: 0.1
      maximum_seconds: 1.0
signals:
  - name: workshop.motion
    schema: motion/v1
resources:
  - id: sensor.temperature
    description: Workshop temperature sensor
    schema: temperature/v1
health:
  supported: true
  state: healthy
"""


class NodeManifestConfigurationTests(unittest.TestCase):
    def _write(self, root: Path, text: str = VALID_CONFIG) -> Path:
        path = root / "medulla-node.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_valid_config_builds_complete_phase_9h_manifest(self) -> None:
        with TemporaryDirectory() as temporary:
            config = load_node_config(self._write(Path(temporary)))
        manifest = build_node_manifest(config)
        self.assertEqual(manifest.node.node_id, "workshop-pi")
        self.assertEqual(manifest.node.display_name, "Workshop Pi")
        self.assertEqual(manifest.node.type.value, "edge")
        self.assertEqual(manifest.provider.provider_id, "workshop-pi")
        self.assertEqual(manifest.protocol, "medulla/1")
        self.assertEqual(manifest.endpoints[0].metadata, {"wire_version": 1})
        self.assertEqual(manifest.capabilities[0].name, "temperature.read")
        self.assertEqual(manifest.signals[0].name, "workshop.motion")
        self.assertEqual(manifest.resources[0].resource_id, "sensor.temperature")
        self.assertTrue(manifest.health.details["supported"])
        self.assertEqual(manifest.node.metadata["implementation"], "medulla-node")

    def test_invalid_config_and_missing_id_are_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(NodeConfigurationError, "unknown fields"):
                load_node_config(self._write(root, VALID_CONFIG + "shell: rm -rf /\n"))
            with self.assertRaisesRegex(NodeConfigurationError, "node.id"):
                load_node_config(self._write(root, VALID_CONFIG.replace("  id: workshop-pi\n", "", 1)))

    def test_duplicate_capability_and_resource_are_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            config = load_node_config(self._write(Path(temporary)))
        duplicate_capability = MedullaNodeAdapter(
            adapter_id="duplicate-capability",
            capabilities=(config.capabilities[0],),
        )
        with self.assertRaisesRegex(ValueError, "capability ids"):
            build_node_manifest(config, (duplicate_capability,))
        duplicate_resource = MedullaNodeAdapter(
            adapter_id="duplicate-resource",
            resources=(config.resources[0],),
        )
        with self.assertRaisesRegex(ValueError, "resource ids"):
            build_node_manifest(config, (duplicate_resource,))

    def test_unsupported_protocol_and_invalid_manifest_are_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            config = load_node_config(
                self._write(Path(temporary), VALID_CONFIG.replace("medulla/1", "medulla/99", 1))
            )
            with self.assertRaises(NodeProtocolError) as raised:
                build_node_manifest(config)
            self.assertEqual(raised.exception.code, "unsupported_version")
            invalid = build_node_manifest(
                load_node_config(self._write(Path(temporary), VALID_CONFIG))
            ).to_dict()
        invalid["executable"] = "remote.module:callback"
        with self.assertRaises(NodeProtocolError):
            MedullaNodeManifest.from_dict(invalid)

    def test_serialization_round_trip(self) -> None:
        with TemporaryDirectory() as temporary:
            config = load_node_config(self._write(Path(temporary)))
            manifest = build_node_manifest(config)
        encoded = json.dumps(manifest.to_dict(), allow_nan=False)
        self.assertEqual(MedullaNodeManifest.from_dict(json.loads(encoded)), manifest)
        self.assertEqual(type(config).from_dict(config.to_dict()), config)

    def test_registered_adapter_contributes_declarations_only(self) -> None:
        with TemporaryDirectory() as temporary:
            config = load_node_config(self._write(Path(temporary)))
        provider = config.provider
        capability = Capability(
            capability_id="workshop-pi.light.read.v1",
            name="light.read",
            description="Read ambient light",
            provider=provider,
            input_schema={},
            output_schema={},
            availability=CapabilityAvailability(state=CapabilityAvailabilityState.AVAILABLE),
            permissions=CapabilityPermissions(risk=CapabilityRisk.READ),
            effect=CapabilityEffect.READ_ONLY,
            timeout=CapabilityTimeout(expected_seconds=0.1, maximum_seconds=1),
        )
        runtime = MedullaNodeRuntime(config)
        runtime.register_adapter(
            MedullaNodeAdapter(
                adapter_id="light-sensor",
                capabilities=(capability,),
                signals=(MedullaNodeSignal(name="light.changed", schema="light/v1"),),
                resources=(MedullaNodeResource(resource_id="sensor.light", description="Light sensor", schema="light/v1"),),
            )
        )
        manifest = runtime.manifest()
        self.assertEqual(manifest.capabilities[-1], capability)
        self.assertEqual(manifest.signals[-1].name, "light.changed")
        self.assertEqual(manifest.resources[-1].resource_id, "sensor.light")

    def test_yaml_rejects_executable_object_features(self) -> None:
        with TemporaryDirectory() as temporary:
            path = self._write(Path(temporary), VALID_CONFIG.replace("version: 0.2", "version: !!python/object:os.system"))
            with self.assertRaisesRegex(NodeConfigurationError, "forbidden"):
                load_node_config(path)

    def test_cli_init_validate_and_manifest_work_offline(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = [sys.executable, "-m", "medulla_node"]
            environment = {"PYTHONPATH": str(repository / "src")}
            initialized = subprocess.run(command + ["init"], cwd=root, env=environment, capture_output=True, text=True)
            validated = subprocess.run(command + ["validate"], cwd=root, env=environment, capture_output=True, text=True)
            rendered = subprocess.run(command + ["manifest"], cwd=root, env=environment, capture_output=True, text=True)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            self.assertIn("Node: Workshop Pi", rendered.stdout)
            self.assertIn("Protocol: medulla/1", rendered.stdout)
            self.assertIn("Capabilities:", rendered.stdout)
            self.assertIn("Signals:", rendered.stdout)
            self.assertIn("Resources:", rendered.stdout)
            status = subprocess.run(
                command + ["status", "--development", "--gpio-output", "17"],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn("Lifecycle: created", status.stdout)
            self.assertIn("Loaded adapters: development, gpio", status.stdout)
            self.assertIn("gpio.output.set", status.stdout)


if __name__ == "__main__":
    unittest.main()
