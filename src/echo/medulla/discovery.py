"""Failure-contained discovery and identification of remote Medulla Nodes.

Discovery is intentionally separate from ``MedullaNodeDirectory``.  A validated
manifest proves only what a node says it is; it does not pair, authorize, register
capabilities, or create a route.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import ipaddress
import json
import time
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from echo.medulla.node import (
    NODE_PROTOCOL,
    NODE_PROTOCOL_NAME,
    MedullaNodeIdentity,
    MedullaNodeManifest,
    NodeProtocolError,
)


MEDULLA_DNS_SD_SERVICE = "_medulla._tcp.local."
DEFAULT_MANIFEST_PATH = "/.well-known/medulla/manifest"
DEFAULT_DISCOVERY_STALE_AFTER = 120.0
DEFAULT_MANIFEST_TIMEOUT = 5.0
DEFAULT_MAX_MANIFEST_BYTES = 1_048_576


class NodeDiscoveryLifecycle(StrEnum):
    """Full lifecycle vocabulary; Phase 9I advances only through IDENTIFIED."""

    DISCOVERED = "discovered"
    IDENTIFIED = "identified"
    PAIRED = "paired"
    AUTHORIZED = "authorized"
    ACTIVE = "active"


class NodeDiscoverySource(StrEnum):
    MDNS = "mdns"
    STATIC = "static"


class NodeDiscoveryHealth(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    DEGRADED = "degraded"


class NodeDiscoveryError(ValueError):
    """A safely reportable discovery or identification failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _required_string(value: Any, path: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{path} must be a non-empty string")
    return value


@dataclass(slots=True, frozen=True, kw_only=True)
class NodeAdvertisement:
    """Transport-neutral information obtained from one discovery advertisement."""

    advertisement_id: str
    node_id: str
    protocol: str
    addresses: tuple[str, ...]
    port: int
    manifest_url: str
    source: NodeDiscoverySource = NodeDiscoverySource.MDNS
    properties: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_string(self.advertisement_id, "advertisement_id")
        _required_string(self.node_id, "node_id")
        _required_string(self.protocol, "protocol")
        addresses = tuple(self.addresses)
        if not addresses or any(type(item) is not str or not item for item in addresses):
            raise ValueError("addresses must contain at least one non-empty string")
        object.__setattr__(self, "addresses", tuple(sorted(set(addresses))))
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port must be an integer from 1 through 65535")
        parsed = urlparse(_required_string(self.manifest_url, "manifest_url"))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("manifest_url must be an absolute HTTP(S) URL")
        object.__setattr__(self, "source", NodeDiscoverySource(self.source))
        properties = dict(self.properties)
        if any(
            type(key) is not str or type(value) is not str
            for key, value in properties.items()
        ):
            raise ValueError("properties must contain only string keys and values")
        object.__setattr__(self, "properties", properties)

    def to_dict(self) -> dict[str, Any]:
        return {
            "advertisement_id": self.advertisement_id,
            "node_id": self.node_id,
            "protocol": self.protocol,
            "addresses": list(self.addresses),
            "port": self.port,
            "manifest_url": self.manifest_url,
            "source": self.source.value,
            "properties": dict(sorted(self.properties.items())),
        }


@dataclass(slots=True, frozen=True, kw_only=True)
class DiscoveredNodeStatus:
    node_id: str
    lifecycle: NodeDiscoveryLifecycle
    source: NodeDiscoverySource
    advertisement_ids: tuple[str, ...]
    protocol: str
    addresses: tuple[str, ...]
    port: int
    manifest_url: str
    first_seen: float
    last_seen: float
    identity: MedullaNodeIdentity | None = None
    manifest: MedullaNodeManifest | None = field(default=None, repr=False)
    error_code: str | None = None
    error_message: str | None = None

    @property
    def authorized(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "lifecycle": self.lifecycle.value,
            "source": self.source.value,
            "advertisement_ids": list(self.advertisement_ids),
            "protocol": self.protocol,
            "addresses": list(self.addresses),
            "port": self.port,
            "manifest_url": self.manifest_url,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "identity": None if self.identity is None else self.identity.to_dict(),
            "manifest": None if self.manifest is None else self.manifest.to_dict(),
            "error": (
                None
                if self.error_code is None
                else {"code": self.error_code, "message": self.error_message}
            ),
            "authorized": False,
        }


@dataclass(slots=True, frozen=True, kw_only=True)
class DiscoveryServiceStatus:
    health: NodeDiscoveryHealth
    node_count: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "health": self.health.value,
            "node_count": self.node_count,
            "error": self.error,
        }


AdvertisementHandler = Callable[[NodeAdvertisement], Awaitable[None]]
RemovalHandler = Callable[[str], Awaitable[None]]
ErrorHandler = Callable[[Exception], Awaitable[None]]
ManifestFetcher = Callable[[NodeAdvertisement], Awaitable[Mapping[str, Any]]]


class DiscoveryBackend(Protocol):
    async def start(
        self,
        on_advertisement: AdvertisementHandler,
        on_removal: RemovalHandler,
        on_error: ErrorHandler,
    ) -> None: ...

    async def stop(self) -> None: ...


@dataclass(slots=True)
class _AdvertisementPresence:
    advertisement: NodeAdvertisement
    last_seen: float


@dataclass(slots=True)
class _DiscoveredNodeRecord:
    first_seen: float
    advertisements: dict[str, _AdvertisementPresence]
    lifecycle: NodeDiscoveryLifecycle = NodeDiscoveryLifecycle.DISCOVERED
    identity: MedullaNodeIdentity | None = None
    manifest: MedullaNodeManifest | None = None
    error_code: str | None = None
    error_message: str | None = None


class HttpNodeManifestFetcher:
    """Retrieve a bounded JSON manifest without loading executable node code."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_MANIFEST_TIMEOUT,
        max_bytes: int = DEFAULT_MAX_MANIFEST_BYTES,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self._timeout = timeout
        self._max_bytes = max_bytes

    async def __call__(self, advertisement: NodeAdvertisement) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._fetch, advertisement.manifest_url)

    def _fetch(self, url: str) -> Mapping[str, Any]:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=self._timeout) as response:  # noqa: S310
            body = response.read(self._max_bytes + 1)
        if len(body) > self._max_bytes:
            raise NodeDiscoveryError("manifest_too_large", "manifest exceeds size limit")
        try:
            parsed = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_object_without_duplicate_keys,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"invalid JSON constant: {value}")
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise NodeDiscoveryError("invalid_manifest_json", str(error)) from error
        if type(parsed) is not dict:
            raise NodeDiscoveryError("invalid_manifest_json", "manifest must be an object")
        return parsed


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key: {key!r}")
        result[key] = value
    return result


class MedullaNodeDiscovery:
    """Inventory discovered identities without granting trust or capability access."""

    def __init__(
        self,
        backend: DiscoveryBackend | None = None,
        *,
        manifest_fetcher: ManifestFetcher | None = None,
        stale_after: float = DEFAULT_DISCOVERY_STALE_AFTER,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if stale_after <= 0:
            raise ValueError("stale_after must be positive")
        self._backend = backend
        self._fetch_manifest = manifest_fetcher or HttpNodeManifestFetcher()
        self._stale_after = stale_after
        self._clock = clock
        self._records: dict[str, _DiscoveredNodeRecord] = {}
        self._advertisement_nodes: dict[str, str] = {}
        self._health = NodeDiscoveryHealth.STOPPED
        self._error: str | None = None
        self._sweeper_task: asyncio.Task[None] | None = None

    async def start(self) -> DiscoveryServiceStatus:
        if self._health is NodeDiscoveryHealth.RUNNING:
            return self.service_status()
        if self._backend is None:
            self._health = NodeDiscoveryHealth.DEGRADED
            self._error = "automatic discovery is unavailable; static discovery remains enabled"
            return self.service_status()
        try:
            await self._backend.start(self.observe, self.remove_advertisement, self.report_error)
        except Exception as error:
            self._health = NodeDiscoveryHealth.DEGRADED
            self._error = str(error)
        else:
            self._health = NodeDiscoveryHealth.RUNNING
            self._error = None
            self._sweeper_task = asyncio.create_task(
                self._sweep_stale_loop(), name="medulla-node-discovery-stale-sweep"
            )
        return self.service_status()

    async def stop(self) -> DiscoveryServiceStatus:
        if self._sweeper_task is not None:
            self._sweeper_task.cancel()
            await asyncio.gather(self._sweeper_task, return_exceptions=True)
            self._sweeper_task = None
        if self._backend is not None:
            try:
                await self._backend.stop()
            except Exception as error:
                self._error = str(error)
        self._health = NodeDiscoveryHealth.STOPPED
        return self.service_status()

    async def report_error(self, error: Exception) -> None:
        self._health = NodeDiscoveryHealth.DEGRADED
        self._error = str(error)

    async def observe(self, advertisement: NodeAdvertisement) -> None:
        """Record an advertisement and identify its node from a validated manifest."""

        now = self._clock()
        old_node_id = self._advertisement_nodes.get(advertisement.advertisement_id)
        if old_node_id is not None and old_node_id != advertisement.node_id:
            await self.remove_advertisement(advertisement.advertisement_id)
        record = self._records.get(advertisement.node_id)
        if record is None:
            record = _DiscoveredNodeRecord(first_seen=now, advertisements={})
            self._records[advertisement.node_id] = record
        record.advertisements[advertisement.advertisement_id] = _AdvertisementPresence(
            advertisement=advertisement,
            last_seen=now,
        )
        self._advertisement_nodes[advertisement.advertisement_id] = advertisement.node_id

        primary_id = self._primary_presence(record).advertisement.advertisement_id
        if advertisement.advertisement_id != primary_id:
            return
        if advertisement.protocol != NODE_PROTOCOL:
            code = (
                "unsupported_version"
                if advertisement.protocol.startswith(f"{NODE_PROTOCOL_NAME}/")
                else "unsupported_protocol"
            )
            self._mark_error(
                record,
                code,
                f"unsupported node protocol: {advertisement.protocol!r}",
            )
            return
        try:
            data = await self._fetch_manifest(advertisement)
            manifest = MedullaNodeManifest.from_dict(data)
            if manifest.node.node_id != advertisement.node_id:
                raise NodeDiscoveryError(
                    "identity_mismatch",
                    "advertised node id does not match manifest node id",
                )
            if manifest.protocol != advertisement.protocol:
                raise NodeDiscoveryError(
                    "protocol_mismatch",
                    "advertised protocol does not match manifest protocol",
                )
        except NodeDiscoveryError as error:
            if self._advertisement_is_current(record, advertisement):
                self._mark_error(record, error.code, str(error))
        except NodeProtocolError as error:
            if self._advertisement_is_current(record, advertisement):
                self._mark_error(record, error.code, str(error))
        except Exception as error:
            if self._advertisement_is_current(record, advertisement):
                self._mark_error(record, "manifest_retrieval_failed", str(error))
        else:
            if not self._advertisement_is_current(record, advertisement):
                return
            # Identification registers identity only. No provider, capability, or route
            # is installed until a later pairing/authorization phase explicitly does so.
            record.lifecycle = NodeDiscoveryLifecycle.IDENTIFIED
            record.identity = manifest.node
            record.manifest = manifest
            record.error_code = None
            record.error_message = None

    async def add_static_manifest(
        self,
        manifest: MedullaNodeManifest,
        *,
        manifest_url: str = "http://static.invalid/.well-known/medulla/manifest",
    ) -> DiscoveredNodeStatus:
        """Identify a manually configured node without requiring LAN discovery."""

        if not isinstance(manifest, MedullaNodeManifest):
            raise TypeError("manifest must be MedullaNodeManifest")
        endpoint = manifest.endpoints[0]
        parsed = urlparse(endpoint.address)
        address = parsed.hostname or endpoint.address
        port = parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)
        advertisement = NodeAdvertisement(
            advertisement_id=f"static:{manifest.node.node_id}",
            node_id=manifest.node.node_id,
            protocol=manifest.protocol,
            addresses=(address,),
            port=port,
            manifest_url=manifest_url,
            source=NodeDiscoverySource.STATIC,
        )
        now = self._clock()
        record = self._records.get(manifest.node.node_id)
        if record is None:
            record = _DiscoveredNodeRecord(first_seen=now, advertisements={})
            self._records[manifest.node.node_id] = record
        record.advertisements[advertisement.advertisement_id] = _AdvertisementPresence(
            advertisement, now
        )
        record.lifecycle = NodeDiscoveryLifecycle.IDENTIFIED
        record.identity = manifest.node
        record.manifest = manifest
        record.error_code = None
        record.error_message = None
        self._advertisement_nodes[advertisement.advertisement_id] = manifest.node.node_id
        status = self.status(manifest.node.node_id)
        assert status is not None
        return status

    async def remove_advertisement(self, advertisement_id: str) -> None:
        node_id = self._advertisement_nodes.pop(advertisement_id, None)
        if node_id is None:
            return
        record = self._records.get(node_id)
        if record is None:
            return
        was_primary = (
            self._primary_presence(record).advertisement.advertisement_id
            == advertisement_id
        )
        record.advertisements.pop(advertisement_id, None)
        if not record.advertisements:
            self._records.pop(node_id, None)
        elif was_primary:
            await self.observe(self._primary_presence(record).advertisement)

    async def expire_stale(self) -> tuple[str, ...]:
        now = self._clock()
        stale_ids = sorted(
            advertisement_id
            for record in self._records.values()
            for advertisement_id, presence in record.advertisements.items()
            if presence.advertisement.source is NodeDiscoverySource.MDNS
            and now - presence.last_seen > self._stale_after
        )
        removed_nodes = {
            self._advertisement_nodes[advertisement_id]
            for advertisement_id in stale_ids
            if advertisement_id in self._advertisement_nodes
        }
        for advertisement_id in stale_ids:
            await self.remove_advertisement(advertisement_id)
        return tuple(sorted(node_id for node_id in removed_nodes if node_id not in self._records))

    def manifest(self, node_id: str) -> MedullaNodeManifest | None:
        record = self._records.get(node_id)
        return None if record is None else record.manifest

    def identity(self, node_id: str) -> MedullaNodeIdentity | None:
        record = self._records.get(node_id)
        return None if record is None else record.identity

    def status(self, node_id: str) -> DiscoveredNodeStatus | None:
        record = self._records.get(node_id)
        if record is None or not record.advertisements:
            return None
        primary = self._primary_presence(record).advertisement
        addresses = tuple(
            sorted(
                {
                    address
                    for presence in record.advertisements.values()
                    for address in presence.advertisement.addresses
                }
            )
        )
        return DiscoveredNodeStatus(
            node_id=node_id,
            lifecycle=record.lifecycle,
            source=primary.source,
            advertisement_ids=tuple(sorted(record.advertisements)),
            protocol=primary.protocol,
            addresses=addresses,
            port=primary.port,
            manifest_url=primary.manifest_url,
            first_seen=record.first_seen,
            last_seen=max(item.last_seen for item in record.advertisements.values()),
            identity=record.identity,
            manifest=record.manifest,
            error_code=record.error_code,
            error_message=record.error_message,
        )

    def inspect(self) -> tuple[DiscoveredNodeStatus, ...]:
        statuses: list[DiscoveredNodeStatus] = []
        for node_id in sorted(self._records):
            status = self.status(node_id)
            if status is not None:
                statuses.append(status)
        return tuple(statuses)

    def service_status(self) -> DiscoveryServiceStatus:
        return DiscoveryServiceStatus(
            health=self._health,
            node_count=len(self._records),
            error=self._error,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": NODE_PROTOCOL,
            "service": self.service_status().to_dict(),
            "nodes": [item.to_dict() for item in self.inspect()],
        }

    @staticmethod
    def _mark_error(record: _DiscoveredNodeRecord, code: str, message: str) -> None:
        record.lifecycle = NodeDiscoveryLifecycle.DISCOVERED
        record.identity = None
        record.manifest = None
        record.error_code = code
        record.error_message = message

    @staticmethod
    def _primary_presence(record: _DiscoveredNodeRecord) -> _AdvertisementPresence:
        return min(
            record.advertisements.values(),
            key=lambda item: (
                item.advertisement.source is not NodeDiscoverySource.STATIC,
                item.advertisement.advertisement_id,
            ),
        )

    def _advertisement_is_current(
        self,
        record: _DiscoveredNodeRecord,
        advertisement: NodeAdvertisement,
    ) -> bool:
        current_record = self._records.get(advertisement.node_id)
        if current_record is not record or not record.advertisements:
            return False
        return self._primary_presence(record).advertisement == advertisement

    async def _sweep_stale_loop(self) -> None:
        interval = min(self._stale_after / 2, 30.0)
        try:
            while True:
                await asyncio.sleep(interval)
                await self.expire_stale()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # An inventory maintenance failure degrades discovery only.
            await self.report_error(error)


class MDNSDiscoveryBackend:
    """Optional DNS-SD browser backed by python-zeroconf."""

    def __init__(
        self,
        *,
        service_type: str = MEDULLA_DNS_SD_SERVICE,
        resolve_timeout_ms: int = 3000,
    ) -> None:
        _required_string(service_type, "service_type")
        if type(resolve_timeout_ms) is not int or resolve_timeout_ms <= 0:
            raise ValueError("resolve_timeout_ms must be a positive integer")
        self._service_type = service_type
        self._resolve_timeout_ms = resolve_timeout_ms
        self._async_zeroconf: Any = None
        self._browser: Any = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._on_advertisement: AdvertisementHandler | None = None
        self._on_removal: RemovalHandler | None = None
        self._on_error: ErrorHandler | None = None

    async def start(
        self,
        on_advertisement: AdvertisementHandler,
        on_removal: RemovalHandler,
        on_error: ErrorHandler,
    ) -> None:
        if self._browser is not None:
            return
        try:
            from zeroconf import ServiceStateChange
            from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
        except ImportError as error:
            raise NodeDiscoveryError(
                "discovery_unavailable",
                "mDNS discovery requires the optional 'zeroconf' package",
            ) from error
        self._on_advertisement = on_advertisement
        self._on_removal = on_removal
        self._on_error = on_error
        self._async_zeroconf = AsyncZeroconf()

        def handle_service(
            zeroconf: Any,
            service_type: str,
            name: str,
            state_change: Any,
        ) -> None:
            if state_change is ServiceStateChange.Removed:
                task = asyncio.create_task(on_removal(name))
            else:
                task = asyncio.create_task(self._resolve(zeroconf, service_type, name))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        try:
            self._browser = AsyncServiceBrowser(
                self._async_zeroconf.zeroconf,
                self._service_type,
                handlers=[handle_service],
            )
        except Exception:
            await self._async_zeroconf.async_close()
            self._async_zeroconf = None
            raise

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.async_cancel()
            self._browser = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._async_zeroconf is not None:
            await self._async_zeroconf.async_close()
            self._async_zeroconf = None

    async def _resolve(self, zeroconf: Any, service_type: str, name: str) -> None:
        try:
            from zeroconf.asyncio import AsyncServiceInfo

            info = AsyncServiceInfo(service_type, name)
            if not await info.async_request(zeroconf, self._resolve_timeout_ms):
                raise NodeDiscoveryError("resolve_failed", f"could not resolve {name!r}")
            advertisement = self._advertisement_from_info(name, info)
            assert self._on_advertisement is not None
            await self._on_advertisement(advertisement)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if self._on_error is not None:
                await self._on_error(error)

    @staticmethod
    def _decode_txt(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="strict")
        if isinstance(value, str):
            return value
        raise NodeDiscoveryError(
            "invalid_advertisement", "DNS-SD TXT values must be UTF-8 strings"
        )

    @classmethod
    def _advertisement_from_info(cls, name: str, info: Any) -> NodeAdvertisement:
        properties = {
            cls._decode_txt(key): cls._decode_txt(value)
            for key, value in info.properties.items()
        }
        node_id = _required_string(properties.get("node_id"), "TXT node_id")
        protocol = _required_string(properties.get("protocol"), "TXT protocol")
        addresses = tuple(sorted(info.parsed_scoped_addresses()))
        if not addresses and info.server:
            addresses = (info.server.rstrip("."),)
        if not addresses:
            raise NodeDiscoveryError("resolve_failed", "advertisement has no address")
        manifest_ref = properties.get("manifest", DEFAULT_MANIFEST_PATH)
        manifest_url = cls._manifest_url(addresses[0], info.port, manifest_ref)
        return NodeAdvertisement(
            advertisement_id=name,
            node_id=node_id,
            protocol=protocol,
            addresses=addresses,
            port=info.port,
            manifest_url=manifest_url,
            properties=properties,
        )

    @staticmethod
    def _manifest_url(address: str, port: int, reference: str) -> str:
        parsed = urlparse(reference)
        if parsed.scheme:
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise NodeDiscoveryError("invalid_advertisement", "invalid manifest URL")
            if parsed.hostname != address:
                raise NodeDiscoveryError(
                    "invalid_advertisement",
                    "absolute manifest URL must use the resolved advertisement address",
                )
            return reference
        host = address
        try:
            if ipaddress.ip_address(address).version == 6:
                host = f"[{address}]"
        except ValueError:
            pass
        base = f"http://{host}:{port}/"
        return urljoin(base, reference)


DiscoveryLifecycle = NodeDiscoveryLifecycle
DiscoverySource = NodeDiscoverySource
DiscoveryHealth = NodeDiscoveryHealth
NodeDiscovery = MedullaNodeDiscovery


__all__ = [
    "DEFAULT_DISCOVERY_STALE_AFTER",
    "DEFAULT_MANIFEST_PATH",
    "DEFAULT_MANIFEST_TIMEOUT",
    "DEFAULT_MAX_MANIFEST_BYTES",
    "MEDULLA_DNS_SD_SERVICE",
    "DiscoveredNodeStatus",
    "DiscoveryBackend",
    "DiscoveryHealth",
    "DiscoveryLifecycle",
    "DiscoveryServiceStatus",
    "DiscoverySource",
    "HttpNodeManifestFetcher",
    "MDNSDiscoveryBackend",
    "ManifestFetcher",
    "MedullaNodeDiscovery",
    "NodeAdvertisement",
    "NodeDiscovery",
    "NodeDiscoveryError",
    "NodeDiscoveryHealth",
    "NodeDiscoveryLifecycle",
    "NodeDiscoverySource",
]
