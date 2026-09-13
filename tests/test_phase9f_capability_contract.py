from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echo.medulla import (
    CAPABILITY_CONTRACT_VERSION,
    Capability,
    CapabilityDefinition,
    CapabilityAvailability,
    CapabilityAvailabilityState,
    CapabilityEffect,
    CapabilityNameCollisionError,
    CapabilityPermissions,
    CapabilityProvider,
    CapabilityProviderKind,
    CapabilityProviderLocation,
    CapabilityRegistry,
    CapabilityRegistryError,
    CapabilityRisk,
    CapabilityTimeout,
)


def make_capability(
    *,
    capability_id: str = "bit-head.head.rotate.v1",
    provider_id: str = "bit-head",
    name: str = "head.rotate",
    location: CapabilityProviderLocation = CapabilityProviderLocation.LOCAL,
) -> Capability:
    return Capability(
        capability_id=capability_id,
        name=name,
        description="Rotate the robot head",
        provider=CapabilityProvider(
            provider_id=provider_id,
            kind=CapabilityProviderKind.SERIAL,
            location=location,
        ),
        input_schema={
            "type": "object",
            "properties": {
                "yaw": {"type": "number"},
                "pitch": {"type": "number"},
            },
            "required": ["yaw", "pitch"],
            "additionalProperties": False,
        },
        output_schema={"type": "object", "properties": {}},
        availability=CapabilityAvailability(
            state=CapabilityAvailabilityState.AVAILABLE
        ),
        permissions=CapabilityPermissions(
            risk=CapabilityRisk.PHYSICAL_MOTION,
            required=("robot.motion",),
        ),
        effect=CapabilityEffect.STATE_CHANGING,
        timeout=CapabilityTimeout(expected_seconds=0.2, maximum_seconds=2.0),
    )


class CapabilityContractTests(unittest.TestCase):
    def test_contract_is_typed_transport_neutral_and_json_serializable(self) -> None:
        capability = make_capability()

        serialized = capability.to_dict()
        encoded = json.dumps(serialized, allow_nan=False)
        restored = Capability.from_dict(json.loads(encoded))

        self.assertEqual(restored, capability)
        self.assertEqual(serialized["contract_version"], CAPABILITY_CONTRACT_VERSION)
        self.assertEqual(serialized["qualified_name"], "bit-head:head.rotate")
        self.assertTrue(capability.state_changing)
        self.assertFalse(capability.read_only)
        self.assertNotIn("transport", serialized)
        self.assertNotIn("callback", serialized)
        self.assertIs(CapabilityDefinition, Capability)

    def test_local_and_remote_providers_are_explicit(self) -> None:
        local = make_capability()
        remote = make_capability(
            capability_id="remote-fs.filesystem.read.v1",
            provider_id="remote-fs",
            name="filesystem.read",
            location=CapabilityProviderLocation.REMOTE,
        )

        self.assertEqual(local.provider.location, CapabilityProviderLocation.LOCAL)
        self.assertEqual(remote.provider.location, CapabilityProviderLocation.REMOTE)

    def test_schemas_and_serialized_values_are_detached(self) -> None:
        schema = {"type": "object", "properties": {"path": {"type": "string"}}}
        capability = make_capability(name="filesystem.read")
        capability = Capability(
            capability_id="local-system.filesystem.read.v1",
            name="filesystem.read",
            description="Read a local file",
            provider=CapabilityProvider(
                provider_id="local-system",
                kind=CapabilityProviderKind.NATIVE,
                location=CapabilityProviderLocation.LOCAL,
            ),
            input_schema=schema,
            output_schema={"type": "string"},
            availability=CapabilityAvailability(),
            permissions=CapabilityPermissions(risk=CapabilityRisk.READ),
            effect=CapabilityEffect.READ_ONLY,
            timeout=CapabilityTimeout(expected_seconds=0.01, maximum_seconds=1),
        )
        schema["properties"]["path"]["type"] = "integer"
        serialized = capability.to_dict()
        serialized["input_schema"]["properties"]["path"]["type"] = "boolean"

        self.assertEqual(
            capability.input_schema["properties"]["path"]["type"], "string"
        )
        self.assertTrue(capability.read_only)
        self.assertFalse(capability.state_changing)

    def test_non_json_schema_and_availability_details_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported type"):
            Capability(
                capability_id="bad.capability.v1",
                name="bad.capability",
                description="Bad schema",
                provider=CapabilityProvider(
                    provider_id="native",
                    kind=CapabilityProviderKind.NATIVE,
                    location=CapabilityProviderLocation.LOCAL,
                ),
                input_schema={"value": object()},
                output_schema={},
                availability=CapabilityAvailability(),
                permissions=CapabilityPermissions(risk=CapabilityRisk.NONE),
                effect=CapabilityEffect.READ_ONLY,
                timeout=CapabilityTimeout(expected_seconds=1, maximum_seconds=1),
            )
        with self.assertRaisesRegex(ValueError, "must be finite"):
            CapabilityAvailability(details={"latency": float("nan")})

    def test_registry_handles_name_collisions_without_implicit_selection(self) -> None:
        first = make_capability()
        second = make_capability(
            capability_id="other-head.head.rotate.v1", provider_id="other-head"
        )
        registry = CapabilityRegistry((second, first))

        self.assertEqual(registry.get(first.capability_id), first)
        self.assertEqual(
            registry.resolve("head.rotate", provider_id="other-head"), second
        )
        with self.assertRaisesRegex(CapabilityNameCollisionError, "ambiguous"):
            registry.resolve("head.rotate")
        self.assertEqual(
            [item.capability_id for item in registry.inspect()],
            sorted((first.capability_id, second.capability_id)),
        )

    def test_duplicate_id_and_provider_qualified_name_are_rejected(self) -> None:
        capability = make_capability()
        with self.assertRaisesRegex(CapabilityRegistryError, "duplicate capability id"):
            CapabilityRegistry((capability, capability))

        same_qualified_name = make_capability(capability_id="different.stable.id")
        with self.assertRaisesRegex(
            CapabilityRegistryError, "duplicate provider-qualified"
        ):
            CapabilityRegistry((capability, same_qualified_name))

    def test_availability_is_inspectable_and_updated_immutably(self) -> None:
        capability = make_capability()
        registry = CapabilityRegistry((capability,))
        unavailable = CapabilityAvailability(
            state=CapabilityAvailabilityState.UNAVAILABLE,
            message="device disconnected",
            details={"retrying": True},
        )

        updated = registry.update_availability(capability.capability_id, unavailable)

        self.assertEqual(capability.availability.state, CapabilityAvailabilityState.AVAILABLE)
        self.assertEqual(updated.availability, unavailable)
        self.assertEqual(registry.availability()[capability.capability_id], unavailable)
        self.assertEqual(
            registry.to_dict()["capabilities"][0]["availability"]["state"],
            "unavailable",
        )
        self.assertEqual(CapabilityRegistry.from_dict(registry.to_dict()).inspect(), (updated,))

    def test_closed_decoder_rejects_unknown_fields_and_inconsistent_derived_values(self) -> None:
        data = make_capability().to_dict()
        data["provider"]["endpoint"] = "serial:///dev/ttyUSB0"
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            Capability.from_dict(data)

        data = make_capability().to_dict()
        data["state_changing"] = False
        with self.assertRaisesRegex(ValueError, "does not match effect"):
            Capability.from_dict(data)


if __name__ == "__main__":
    unittest.main()
