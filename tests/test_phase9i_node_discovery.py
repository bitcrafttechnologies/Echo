from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from echo.medulla import (
    NODE_PROTOCOL,
    Capability,
    CapabilityAvailability,
    CapabilityAvailabilityState,
    CapabilityEffect,
    CapabilityPermissions,
    CapabilityProvider,
    CapabilityProviderKind,
    CapabilityProviderLocation,
    CapabilityRegistry,
    CapabilityRisk,
    CapabilityTimeout,
    DiscoveryHealth,
    DiscoveryLifecycle,
    HttpNodeManifestFetcher,
    MDNSDiscoveryBackend,
    MedullaNodeDiscovery,
    MedullaNodeEndpoint,
    MedullaNodeHealth,
    MedullaNodeIdentity,
    MedullaNodeManifest,
    MedullaNodeSecurity,
    MedullaNodeType,
    NodeAdvertisement,
    NodeDiscoveryError,
)
from echo.medulla.capability import CapabilityProviderHealthState


def make_manifest(node_id: str = "bit-head-controller") -> MedullaNodeManifest:
    provider = CapabilityProvider(
        provider_id=node_id,
        kind=CapabilityProviderKind.WEBSOCKET,
        location=CapabilityProviderLocation.REMOTE,
    )
    return MedullaNodeManifest(
        node=MedullaNodeIdentity(
            node_id=node_id,
            type=MedullaNodeType.EMBEDDED,
            display_name="Bit Head",
        ),
        provider=provider,
        endpoints=(
            MedullaNodeEndpoint(
                endpoint_id="control",
                kind=CapabilityProviderKind.WEBSOCKET,
                address="ws://192.0.2.20:8765/medulla",
            ),
        ),
        capabilities=(
            Capability(
                capability_id=f"{node_id}.head.rotate.v1",
                name="head.rotate",
                description="Rotate the robot head",
                provider=provider,
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                availability=CapabilityAvailability(
                    state=CapabilityAvailabilityState.AVAILABLE
                ),
                permissions=CapabilityPermissions(
                    risk=CapabilityRisk.PHYSICAL_MOTION,
                    required=("robot.motion",),
                ),
                effect=CapabilityEffect.STATE_CHANGING,
                timeout=CapabilityTimeout(expected_seconds=0.2, maximum_seconds=2),
            ),
        ),
        health=MedullaNodeHealth(state=CapabilityProviderHealthState.HEALTHY),
        security=MedullaNodeSecurity(pairing_required=True),
    )


def advertisement(
    advertisement_id: str = "Bit Head._medulla._tcp.local.",
    *,
    node_id: str = "bit-head-controller",
    protocol: str = NODE_PROTOCOL,
    address: str = "192.0.2.20",
) -> NodeAdvertisement:
    return NodeAdvertisement(
        advertisement_id=advertisement_id,
        node_id=node_id,
        protocol=protocol,
        addresses=(address,),
        port=8080,
        manifest_url=f"http://{address}:8080/.well-known/medulla/manifest",
    )


class FakeBackend:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.on_advertisement = None
        self.on_removal = None
        self.on_error = None
        self.stopped = False

    async def start(self, on_advertisement, on_removal, on_error) -> None:
        if self.failure is not None:
            raise self.failure
        self.on_advertisement = on_advertisement
        self.on_removal = on_removal
        self.on_error = on_error

    async def stop(self) -> None:
        self.stopped = True


class NodeDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovers_and_identifies_without_authorizing_capabilities(self) -> None:
        manifest = make_manifest()
        registry = CapabilityRegistry()

        async def fetch(_advertisement):
            return manifest.to_dict()

        discovery = MedullaNodeDiscovery(manifest_fetcher=fetch)
        await discovery.observe(advertisement())

        status = discovery.status("bit-head-controller")
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.lifecycle, DiscoveryLifecycle.IDENTIFIED)
        self.assertEqual(status.identity, manifest.node)
        self.assertFalse(status.authorized)
        self.assertIsNone(registry.provider("bit-head-controller"))
        self.assertEqual(registry.find("head.rotate"), ())
        json.dumps(discovery.to_dict())

    async def test_duplicate_advertisements_are_coalesced_and_removed_individually(self) -> None:
        calls = 0
        manifest = make_manifest()

        async def fetch(_advertisement):
            nonlocal calls
            calls += 1
            return manifest.to_dict()

        discovery = MedullaNodeDiscovery(manifest_fetcher=fetch)
        first = advertisement("a._medulla._tcp.local.")
        duplicate = advertisement(
            "b._medulla._tcp.local.", address="192.0.2.21"
        )
        await discovery.observe(first)
        await discovery.observe(duplicate)

        status = discovery.status(first.node_id)
        assert status is not None
        self.assertEqual(calls, 1)
        self.assertEqual(
            status.advertisement_ids,
            (first.advertisement_id, duplicate.advertisement_id),
        )
        self.assertEqual(status.addresses, ("192.0.2.20", "192.0.2.21"))

        await discovery.remove_advertisement(first.advertisement_id)
        self.assertIsNotNone(discovery.status(first.node_id))
        await discovery.remove_advertisement(duplicate.advertisement_id)
        self.assertIsNone(discovery.status(first.node_id))

    async def test_stale_mdns_nodes_expire_but_static_nodes_do_not(self) -> None:
        now = 10.0
        manifest = make_manifest()

        async def fetch(_advertisement):
            return manifest.to_dict()

        discovery = MedullaNodeDiscovery(
            manifest_fetcher=fetch,
            stale_after=30,
            clock=lambda: now,
        )
        await discovery.observe(advertisement())
        now = 41.0
        self.assertEqual(await discovery.expire_stale(), (manifest.node.node_id,))
        self.assertIsNone(discovery.status(manifest.node.node_id))

        await discovery.add_static_manifest(manifest)
        now = 1000.0
        self.assertEqual(await discovery.expire_stale(), ())
        self.assertEqual(
            discovery.status(manifest.node.node_id).lifecycle,  # type: ignore[union-attr]
            DiscoveryLifecycle.IDENTIFIED,
        )

    async def test_unsupported_protocol_and_identity_mismatch_fail_safely(self) -> None:
        manifest = make_manifest()
        calls = 0

        async def fetch(_advertisement):
            nonlocal calls
            calls += 1
            return manifest.to_dict()

        discovery = MedullaNodeDiscovery(manifest_fetcher=fetch)
        await discovery.observe(advertisement(protocol="medulla/99"))
        status = discovery.status(manifest.node.node_id)
        assert status is not None
        self.assertEqual(status.lifecycle, DiscoveryLifecycle.DISCOVERED)
        self.assertEqual(status.error_code, "unsupported_version")
        self.assertEqual(calls, 0)

        await discovery.observe(
            advertisement("other._medulla._tcp.local.", node_id="other-node")
        )
        mismatch = discovery.status("other-node")
        assert mismatch is not None
        self.assertEqual(mismatch.error_code, "identity_mismatch")
        self.assertIsNone(mismatch.identity)

    async def test_discovery_backend_failure_is_contained_and_static_still_works(self) -> None:
        backend = FakeBackend(failure=OSError("multicast unavailable"))
        discovery = MedullaNodeDiscovery(backend)

        service = await discovery.start()
        self.assertEqual(service.health, DiscoveryHealth.DEGRADED)
        self.assertIn("multicast unavailable", service.error or "")

        status = await discovery.add_static_manifest(make_manifest())
        self.assertEqual(status.lifecycle, DiscoveryLifecycle.IDENTIFIED)
        self.assertEqual(discovery.service_status().node_count, 1)

    async def test_backend_callbacks_drive_inventory_and_disconnect_removes_node(self) -> None:
        backend = FakeBackend()
        manifest = make_manifest()

        async def fetch(_advertisement):
            return manifest.to_dict()

        discovery = MedullaNodeDiscovery(backend, manifest_fetcher=fetch)
        self.assertEqual((await discovery.start()).health, DiscoveryHealth.RUNNING)
        assert backend.on_advertisement is not None
        assert backend.on_removal is not None
        item = advertisement()
        await backend.on_advertisement(item)
        self.assertIsNotNone(discovery.identity(item.node_id))
        await backend.on_removal(item.advertisement_id)
        self.assertIsNone(discovery.status(item.node_id))
        await discovery.stop()
        self.assertTrue(backend.stopped)

    async def test_no_backend_is_a_nonfatal_degraded_mode(self) -> None:
        discovery = MedullaNodeDiscovery()
        status = await discovery.start()
        self.assertEqual(status.health, DiscoveryHealth.DEGRADED)
        self.assertEqual(status.node_count, 0)

    def test_mdns_relative_manifest_url_supports_ipv4_and_ipv6(self) -> None:
        self.assertEqual(
            MDNSDiscoveryBackend._manifest_url("192.0.2.4", 8080, "/manifest"),
            "http://192.0.2.4:8080/manifest",
        )
        self.assertEqual(
            MDNSDiscoveryBackend._manifest_url("2001:db8::1", 8080, "/manifest"),
            "http://[2001:db8::1]:8080/manifest",
        )
        with self.assertRaises(NodeDiscoveryError):
            MDNSDiscoveryBackend._manifest_url(
                "192.0.2.4", 8080, "https://unrelated.example/manifest"
            )

    def test_mdns_service_info_maps_to_transport_neutral_advertisement(self) -> None:
        class Info:
            properties = {
                b"node_id": b"bit-head-controller",
                b"protocol": b"medulla/1",
                b"manifest": b"/node-manifest",
            }
            port = 8080
            server = "bit-head.local."

            @staticmethod
            def parsed_scoped_addresses():
                return ["192.0.2.20"]

        item = MDNSDiscoveryBackend._advertisement_from_info(
            "Bit Head._medulla._tcp.local.", Info()
        )
        self.assertEqual(item.node_id, "bit-head-controller")
        self.assertEqual(item.protocol, NODE_PROTOCOL)
        self.assertEqual(item.addresses, ("192.0.2.20",))
        self.assertEqual(item.manifest_url, "http://192.0.2.20:8080/node-manifest")

    async def test_http_manifest_fetch_is_bounded_and_rejects_duplicate_keys(self) -> None:
        class Response:
            def __init__(self, body: bytes) -> None:
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, amount: int) -> bytes:
                return self.body[:amount]

        fetcher = HttpNodeManifestFetcher(max_bytes=32)
        with patch(
            "echo.medulla.discovery.urlopen",
            return_value=Response(b'{"node": 1, "node": 2}'),
        ):
            with self.assertRaisesRegex(NodeDiscoveryError, "duplicate object key"):
                await fetcher(advertisement())

        with patch(
            "echo.medulla.discovery.urlopen",
            return_value=Response(b"x" * 33),
        ):
            with self.assertRaisesRegex(NodeDiscoveryError, "size limit"):
                await fetcher(advertisement())


if __name__ == "__main__":
    unittest.main()
