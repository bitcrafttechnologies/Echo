from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from echo import EchoNodeWebSocketHost
from medulla_node import EchoConnectionConfig, MedullaNodeConfig, MedullaNodeRuntime
from medulla_node.cli import render_manifest
from medulla_node.config import load_node_config
from medulla_protocol import (
    ConnectionNegotiationState,
    ConnectionRequirements,
    CredentialRequirement,
    EntityIdentityBasic,
    EntityIdentityRequirement,
    EntityIdentityScope,
    MetadataRequirement,
    SessionRequirement,
)


REQUIREMENTS_CONFIG = """protocol: medulla/1
node:
  id: weather-node
  name: Weather Node
  type: service
provider:
  id: weather-node
transport:
  type: websocket
  address: ws://weather-node.local:8765/medulla
metadata:
  implementation: medulla-node
  version: 0.5
capabilities: []
signals: []
resources: []
connection_requirements:
  identity:
    required: true
    scopes:
      - identity.basic
  metadata:
    required: true
    keys:
      - locale
  credentials:
    - id: weather_api
      type: secret
      required: true
  permissions:
    - location.coarse
  protocol_features:
    - heartbeat.v1
  entity_capabilities:
    - location.current
  session:
    required: true
    features:
      - bounded-heartbeat
    metadata:
      maximum_idle_seconds: 30
"""


class ConnectionRequirementsTests(unittest.TestCase):
    def test_configuration_builds_all_declarative_requirement_types(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "node.yaml"
            path.write_text(REQUIREMENTS_CONFIG, encoding="utf-8")
            manifest = MedullaNodeRuntime(load_node_config(path)).manifest()

        requirements = manifest.connection_requirements
        self.assertEqual(requirements.identity.scopes, (EntityIdentityScope.BASIC,))
        self.assertEqual(requirements.metadata.keys, ("locale",))
        self.assertEqual(requirements.credentials[0].credential_id, "weather_api")
        self.assertEqual(requirements.permissions, ("location.coarse",))
        self.assertEqual(requirements.protocol_features, ("heartbeat.v1",))
        self.assertEqual(requirements.entity_capabilities, ("location.current",))
        self.assertEqual(requirements.session.metadata, {"maximum_idle_seconds": 30})

        encoded = json.dumps(manifest.to_dict(), allow_nan=False)
        restored = type(manifest).from_dict(json.loads(encoded))
        self.assertEqual(restored, manifest)
        rendered = render_manifest(manifest)
        self.assertIn("identity.basic", rendered)
        self.assertIn("credential.weather_api", rendered)
        self.assertIn("location.coarse", rendered)

    def test_identity_basic_is_closed_and_contains_no_private_entity_data(self) -> None:
        basic = EntityIdentityBasic(
            entity_id="echo-main",
            display_name="Echo",
            entity_type="assistant",
        )
        self.assertEqual(
            set(basic.to_dict()),
            {"entity_id", "display_name", "entity_type"},
        )
        for excluded in (
            "memory", "conversation_history", "relationships", "reflection",
            "private_goals", "private_state", "credentials", "private_traits",
            "system_prompt", "configuration",
        ):
            with self.assertRaisesRegex(ValueError, "unknown fields"):
                EntityIdentityBasic.from_dict({"entity_id": "echo-main", excluded: {}})

    def test_invalid_scopes_duplicates_and_executable_values_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            EntityIdentityRequirement(required=True, scopes=("identity.memory",))
        with self.assertRaisesRegex(ValueError, "duplicates"):
            ConnectionRequirements(permissions=("location.coarse", "location.coarse"))
        with self.assertRaisesRegex(ValueError, "credential ids"):
            ConnectionRequirements(
                credentials=(
                    CredentialRequirement(credential_id="weather", type="secret"),
                    CredentialRequirement(credential_id="weather", type="token"),
                )
            )
        with self.assertRaisesRegex(ValueError, "unsupported type"):
            SessionRequirement(metadata={"callback": object()})

    def test_requirement_round_trip_is_plain_data(self) -> None:
        requirements = ConnectionRequirements(
            identity=EntityIdentityRequirement(
                required=True,
                scopes=(EntityIdentityScope.BASIC, EntityIdentityScope.PROFILE),
            ),
            metadata=MetadataRequirement(required=True, keys=("locale",)),
            credentials=(CredentialRequirement(credential_id="weather", type="secret"),),
            permissions=("location.coarse",),
            protocol_features=("heartbeat.v1",),
            entity_capabilities=("location.current",),
            session=SessionRequirement(required=True, features=("bounded-heartbeat",)),
        )
        copied = json.loads(json.dumps(requirements.to_dict(), allow_nan=False))
        self.assertEqual(ConnectionRequirements.from_dict(copied), requirements)


class ConnectionNegotiationTests(unittest.IsolatedAsyncioTestCase):
    async def test_echo_inspects_requirements_and_stops_awaiting_approval(self) -> None:
        host = EchoNodeWebSocketHost("ws://127.0.0.1:0/medulla")
        await host.start()
        requirements = ConnectionRequirements(
            identity=EntityIdentityRequirement(
                required=True,
                scopes=(EntityIdentityScope.BASIC,),
            ),
            credentials=(CredentialRequirement(credential_id="weather_api", type="secret"),),
            permissions=("location.coarse",),
        )
        config = MedullaNodeConfig(
            node_id="negotiating-node",
            echo=EchoConnectionConfig(
                endpoint=host.endpoint,
                heartbeat_interval=0.03,
                heartbeat_timeout=0.15,
            ),
            connection_requirements=requirements,
        )
        runtime = MedullaNodeRuntime(config)
        try:
            await runtime.start()
            self.assertTrue(await host.wait_for_node("negotiating-node", 2))
            self.assertEqual(host.requirements("negotiating-node"), requirements)
            self.assertIsNone(host.directory.status("negotiating-node"))
            status = host.status("negotiating-node")
            self.assertEqual(
                status.negotiation_history,
                (
                    ConnectionNegotiationState.CONNECTED_TRANSPORT,
                    ConnectionNegotiationState.MANIFEST_RECEIVED,
                    ConnectionNegotiationState.COMPATIBLE,
                    ConnectionNegotiationState.REQUIREMENTS_RECEIVED,
                    ConnectionNegotiationState.AWAITING_APPROVAL,
                ),
            )
            self.assertEqual(
                status.negotiation_state,
                ConnectionNegotiationState.AWAITING_APPROVAL,
            )
        finally:
            await runtime.stop()
            await host.stop()


if __name__ == "__main__":
    unittest.main()
