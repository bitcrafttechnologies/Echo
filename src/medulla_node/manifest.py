"""Offline composition of the shared Phase 9H node manifest."""

from __future__ import annotations

from collections.abc import Iterable

from medulla_node.adapters import MedullaNodeAdapter
from medulla_node.config import MedullaNodeConfig
from medulla_protocol import (
    CapabilityProviderHealthState,
    MedullaNodeEndpoint,
    MedullaNodeHealth,
    MedullaNodeIdentity,
    MedullaNodeManifest,
)


def build_node_manifest(
    config: MedullaNodeConfig,
    adapters: Iterable[MedullaNodeAdapter] = (),
) -> MedullaNodeManifest:
    """Build and fully validate what the node would advertise, without I/O."""

    if not isinstance(config, MedullaNodeConfig):
        raise TypeError("config must be MedullaNodeConfig")
    adapter_items = tuple(adapters)
    if any(not isinstance(item, MedullaNodeAdapter) for item in adapter_items):
        raise TypeError("adapters must contain only MedullaNodeAdapter values")
    provider = config.provider
    capabilities = [*config.capabilities]
    signals = [*config.signals]
    resources = [*config.resources]
    for adapter in adapter_items:
        for capability in adapter.capabilities():
            if capability.provider != provider:
                raise ValueError(
                    f"adapter {adapter.adapter_id!r} capability provider does not match node provider"
                )
            capabilities.append(capability)
        signals.extend(adapter.signals())
        resources.extend(adapter.resources())
    health_details = {**config.health_details, "supported": config.health_supported}
    health_state = (
        config.health_state
        if config.health_supported
        else CapabilityProviderHealthState.UNKNOWN
    )
    return MedullaNodeManifest(
        protocol=config.protocol,
        node=MedullaNodeIdentity(
            node_id=config.node_id,
            type=config.node_type,
            display_name=config.display_name,
            metadata=config.metadata,
        ),
        provider=provider,
        endpoints=(
            MedullaNodeEndpoint(
                endpoint_id=config.endpoint_id,
                kind=config.transport_type,
                address=config.endpoint_address,
                metadata=config.transport_metadata,
            ),
        ),
        capabilities=tuple(capabilities),
        signals=tuple(signals),
        resources=tuple(resources),
        health=MedullaNodeHealth(
            state=health_state,
            message=config.health_message,
            details=health_details,
        ),
        connection_requirements=config.connection_requirements,
    )


build_manifest = build_node_manifest

__all__ = ["build_manifest", "build_node_manifest"]
