"""Hardware-neutral adapter lifecycle, Action routing, and Signal normalization."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from medulla_node.adapters import MedullaAdapter
from medulla_node.config import MedullaNodeConfig
from medulla_node.lifecycle import MedullaNodeLifecycle
from medulla_node.manifest import build_node_manifest
from medulla_protocol import (
    AdapterEvent,
    Capability,
    CapabilityAvailabilityState,
    MedullaNodeIdentity,
    MedullaNodeManifest,
    NodeAction,
    NodeActionError,
    NodeActionErrorCode,
    NodeActionResult,
    NodeActionResultState,
    NodeSignalEnvelope,
)


@dataclass(slots=True, frozen=True, kw_only=True)
class MedullaNodeStatus:
    identity: MedullaNodeIdentity
    lifecycle: MedullaNodeLifecycle
    adapter_ids: tuple[str, ...]
    signal_sequence: int
    queued_signals: int
    transport: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "node": self.identity.to_dict(),
            "lifecycle": self.lifecycle.value,
            "adapter_ids": list(self.adapter_ids),
            "signal_sequence": self.signal_sequence,
            "queued_signals": self.queued_signals,
            "transport": self.transport,
        }


class AdapterLifecycleError(RuntimeError):
    pass


class MedullaNodeRuntime:
    """Coordinate local adapters without importing Echo or opening a network."""

    def __init__(self, config: MedullaNodeConfig) -> None:
        if not isinstance(config, MedullaNodeConfig):
            raise TypeError("config must be MedullaNodeConfig")
        self._config = config
        self._lifecycle = MedullaNodeLifecycle.CREATED
        self._adapters: dict[str, MedullaAdapter] = {}
        self._signal_sequence = 0
        self._signals: asyncio.Queue[NodeSignalEnvelope] = asyncio.Queue()
        self._transport: Any | None = None

    @property
    def config(self) -> MedullaNodeConfig:
        return self._config

    def register_adapter(self, adapter: MedullaAdapter) -> None:
        if not isinstance(adapter, MedullaAdapter):
            raise TypeError("adapter must be MedullaAdapter")
        if self._lifecycle not in {MedullaNodeLifecycle.CREATED, MedullaNodeLifecycle.STOPPED}:
            raise RuntimeError("adapters can only be registered while the node is stopped")
        if adapter.adapter_id in self._adapters:
            raise ValueError(f"adapter is already registered: {adapter.adapter_id}")
        build_node_manifest(self._config, (*self._adapters.values(), adapter))
        self._adapters[adapter.adapter_id] = adapter

    def manifest(self) -> MedullaNodeManifest:
        return build_node_manifest(self._config, self._adapters.values())

    def attach_transport(self, transport: Any) -> None:
        if self._lifecycle not in {MedullaNodeLifecycle.CREATED, MedullaNodeLifecycle.STOPPED}:
            raise RuntimeError("transport can only be attached while the node is stopped")
        if self._transport is not None:
            raise ValueError("a node transport is already attached")
        for method in ("start", "stop", "status"):
            if not callable(getattr(transport, method, None)):
                raise TypeError(f"transport must provide {method}()")
        self._transport = transport

    async def start(self) -> MedullaNodeStatus:
        if self._lifecycle is MedullaNodeLifecycle.RUNNING:
            return self.status()
        if self._lifecycle not in {MedullaNodeLifecycle.CREATED, MedullaNodeLifecycle.STOPPED}:
            raise AdapterLifecycleError(f"cannot start node while {self._lifecycle.value}")
        self.manifest()
        self._lifecycle = MedullaNodeLifecycle.STARTING
        started: list[MedullaAdapter] = []
        try:
            for adapter in self._adapters.values():
                adapter.bind_event_sink(self._sink_for(adapter))
                started.append(adapter)
                await adapter.start()
        except Exception as error:
            for adapter in reversed(started):
                try:
                    await adapter.stop()
                except Exception:
                    pass
            for adapter in self._adapters.values():
                adapter.bind_event_sink(None)
            self._lifecycle = MedullaNodeLifecycle.FAILED
            raise AdapterLifecycleError(f"adapter startup failed: {error}") from error
        self._lifecycle = MedullaNodeLifecycle.RUNNING
        try:
            if self._transport is None and self._config.echo is not None:
                from medulla_node.transport import NodeWebSocketTransport
                self._transport = NodeWebSocketTransport(self, self._config.echo)
            if self._transport is not None:
                await self._transport.start()
        except Exception as error:
            for adapter in reversed(started):
                with suppress(Exception):
                    await adapter.stop()
                adapter.bind_event_sink(None)
            self._lifecycle = MedullaNodeLifecycle.FAILED
            raise AdapterLifecycleError(f"transport startup failed: {error}") from error
        return self.status()

    async def stop(self) -> MedullaNodeStatus:
        if self._lifecycle in {MedullaNodeLifecycle.CREATED, MedullaNodeLifecycle.STOPPED}:
            self._lifecycle = MedullaNodeLifecycle.STOPPED
            return self.status()
        if self._transport is not None:
            await self._transport.stop()
        self._lifecycle = MedullaNodeLifecycle.STOPPING
        failures: list[str] = []
        for adapter in reversed(tuple(self._adapters.values())):
            try:
                await adapter.stop()
            except Exception as error:
                failures.append(f"{adapter.adapter_id}: {error}")
            finally:
                adapter.bind_event_sink(None)
        self._lifecycle = MedullaNodeLifecycle.FAILED if failures else MedullaNodeLifecycle.STOPPED
        if failures:
            raise AdapterLifecycleError("adapter shutdown failed: " + "; ".join(failures))
        return self.status()

    async def execute(self, action: NodeAction) -> NodeActionResult:
        if not isinstance(action, NodeAction):
            raise TypeError("action must be NodeAction")
        if self._lifecycle is not MedullaNodeLifecycle.RUNNING:
            return self._rejected(action, NodeActionErrorCode.NOT_RUNNING, "node is not running")
        capability = self._resolve_capability(action.type)
        if capability is None:
            return self._rejected(
                action, NodeActionErrorCode.UNKNOWN_ACTION,
                f"manifest does not declare Action {action.type!r}",
            )
        adapter = self._adapter_for(capability)
        if adapter is None:
            return self._rejected(
                action, NodeActionErrorCode.CAPABILITY_UNAVAILABLE,
                f"no runtime adapter implements capability {capability.capability_id!r}",
                capability=capability,
            )
        if capability.availability.state is not CapabilityAvailabilityState.AVAILABLE:
            return self._rejected(
                action, NodeActionErrorCode.CAPABILITY_UNAVAILABLE,
                f"capability is not available: {capability.capability_id}",
                adapter=adapter, capability=capability,
            )
        if action.resource_id is not None and action.resource_id not in {
            item.resource_id for item in adapter.resources()
        }:
            return self._rejected(
                action, NodeActionErrorCode.INVALID_RESOURCE,
                f"adapter {adapter.adapter_id!r} does not declare resource {action.resource_id!r}",
                adapter=adapter, capability=capability,
            )
        adapter_action = action
        if action.type != capability.name:
            adapter_action = NodeAction(
                id=action.id,
                type=capability.name,
                parameters=action.parameters,
                resource_id=action.resource_id,
                correlation_id=action.correlation_id,
                created_at=action.created_at,
            )
        try:
            raw_result = await adapter.execute(adapter_action)
        except Exception as error:
            return self._failed(action, adapter, capability, NodeActionErrorCode.EXECUTION_FAILED, str(error))
        if not isinstance(raw_result, Mapping):
            return self._failed(
                action, adapter, capability, NodeActionErrorCode.INVALID_RESULT,
                "adapter result must be a mapping",
            )
        try:
            return NodeActionResult(
                action_id=action.id,
                state=NodeActionResultState.COMPLETED,
                node_id=self._config.node_id,
                provider_id=self._config.provider_id,
                adapter_id=adapter.adapter_id,
                capability_id=capability.capability_id,
                result=dict(raw_result),
            )
        except (TypeError, ValueError) as error:
            return self._failed(action, adapter, capability, NodeActionErrorCode.INVALID_RESULT, str(error))

    async def receive_signal(self) -> NodeSignalEnvelope:
        if self._lifecycle is not MedullaNodeLifecycle.RUNNING and self._signals.empty():
            raise RuntimeError("node is not running")
        return await self._signals.get()

    def status(self) -> MedullaNodeStatus:
        transport_status = self._transport.status().to_dict() if self._transport else None
        return MedullaNodeStatus(
            identity=MedullaNodeIdentity(
                node_id=self._config.node_id,
                type=self._config.node_type,
                display_name=self._config.display_name,
            ),
            lifecycle=self._lifecycle,
            adapter_ids=tuple(self._adapters),
            signal_sequence=self._signal_sequence,
            queued_signals=self._signals.qsize(),
            transport=transport_status,
        )

    def authorized_requirements(self):
        """Return the current in-memory authorization, if the transport is active."""

        return getattr(self._transport, "authorization", None)

    def _resolve_capability(self, requested: str) -> Capability | None:
        for capability in self.manifest().capabilities:
            if requested in {capability.name, capability.capability_id}:
                return capability
        return None

    def _adapter_for(self, requested: Capability) -> MedullaAdapter | None:
        for adapter in self._adapters.values():
            for capability in adapter.capabilities():
                if requested.capability_id == capability.capability_id:
                    return adapter
        return None

    def _sink_for(self, adapter: MedullaAdapter):
        async def sink(event: AdapterEvent) -> None:
            declared_signals = {item.name for item in adapter.signals()}
            if event.signal_type not in declared_signals:
                raise ValueError(
                    f"adapter {adapter.adapter_id!r} emitted undeclared Signal {event.signal_type!r}"
                )
            declared_resources = {item.resource_id for item in adapter.resources()}
            if event.resource_id is not None and event.resource_id not in declared_resources:
                raise ValueError(
                    f"adapter {adapter.adapter_id!r} emitted undeclared resource {event.resource_id!r}"
                )
            self._signal_sequence += 1
            await self._signals.put(
                NodeSignalEnvelope(
                    id=event.id,
                    node_id=self._config.node_id,
                    provider_id=self._config.provider_id,
                    adapter_id=adapter.adapter_id,
                    resource_id=event.resource_id,
                    signal_type=event.signal_type,
                    timestamp=event.timestamp,
                    correlation_id=event.correlation_id,
                    sequence=self._signal_sequence,
                    payload=event.payload,
                )
            )
        return sink

    def _rejected(
        self,
        action: NodeAction,
        code: NodeActionErrorCode,
        message: str,
        *,
        adapter: MedullaAdapter | None = None,
        capability: Capability | None = None,
    ) -> NodeActionResult:
        return NodeActionResult(
            action_id=action.id, state=NodeActionResultState.REJECTED,
            node_id=self._config.node_id, provider_id=self._config.provider_id,
            adapter_id=adapter.adapter_id if adapter else None,
            capability_id=capability.capability_id if capability else None,
            error=NodeActionError(code=code, message=message),
        )

    def _failed(
        self,
        action: NodeAction,
        adapter: MedullaAdapter,
        capability: Capability,
        code: NodeActionErrorCode,
        message: str,
    ) -> NodeActionResult:
        return NodeActionResult(
            action_id=action.id, state=NodeActionResultState.FAILED,
            node_id=self._config.node_id, provider_id=self._config.provider_id,
            adapter_id=adapter.adapter_id, capability_id=capability.capability_id,
            error=NodeActionError(code=code, message=message or "adapter execution failed"),
        )


NodeRuntime = MedullaNodeRuntime
NodeStatus = MedullaNodeStatus

__all__ = [
    "AdapterLifecycleError", "MedullaNodeRuntime", "MedullaNodeStatus",
    "NodeRuntime", "NodeStatus",
]
