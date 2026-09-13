"""Generic standalone adapter runtime contract."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from medulla_protocol import (
    AdapterEvent,
    Capability,
    MedullaNodeResource,
    MedullaNodeSignal,
    NodeAction,
)


EventSink = Callable[[AdapterEvent], Awaitable[None]]


class MedullaAdapter:
    """Hardware-neutral lifecycle, description, execution, and event contract."""

    def __init__(
        self,
        *,
        adapter_id: str,
        capabilities: tuple[Capability, ...] = (),
        signals: tuple[MedullaNodeSignal, ...] = (),
        resources: tuple[MedullaNodeResource, ...] = (),
    ) -> None:
        if type(adapter_id) is not str or not adapter_id:
            raise ValueError("adapter_id must be a non-empty string")
        self._adapter_id = adapter_id
        self._capabilities = self._typed_tuple(capabilities, Capability, "capabilities")
        self._signals = self._typed_tuple(signals, MedullaNodeSignal, "signals")
        self._resources = self._typed_tuple(resources, MedullaNodeResource, "resources")
        self._event_sink: EventSink | None = None

    @staticmethod
    def _typed_tuple(values: tuple[Any, ...], item_type: type, name: str) -> tuple[Any, ...]:
        result = tuple(values)
        if any(not isinstance(item, item_type) for item in result):
            raise TypeError(f"{name} contains an invalid item")
        return result

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    def capabilities(self) -> tuple[Capability, ...]:
        return self._capabilities

    def resources(self) -> tuple[MedullaNodeResource, ...]:
        return self._resources

    def signals(self) -> tuple[MedullaNodeSignal, ...]:
        return self._signals

    def bind_event_sink(self, sink: EventSink | None) -> None:
        if sink is not None and not callable(sink):
            raise TypeError("event sink must be callable")
        self._event_sink = sink

    async def emit(self, event: AdapterEvent) -> None:
        if not isinstance(event, AdapterEvent):
            raise TypeError("event must be AdapterEvent")
        if self._event_sink is None:
            raise RuntimeError("adapter is not attached to a running node")
        await self._event_sink(event)

    async def start(self) -> None:
        """Acquire local resources. The base data-only adapter has none."""

    async def execute(self, action: NodeAction) -> Mapping[str, Any]:
        raise NotImplementedError(f"adapter {self.adapter_id!r} does not execute Actions")

    async def stop(self) -> None:
        """Release local resources. The base data-only adapter has none."""


MedullaNodeAdapter = MedullaAdapter
NodeAdapter = MedullaAdapter

from medulla_node.adapters.development import DevelopmentAdapter
from medulla_node.adapters.gpio import GpioAdapter, GpioBackend, GpioZeroBackend
from medulla_node.adapters.macbook import (
    MacbookAdapter,
    MacbookLocation,
    MacbookSystemBackend,
    NativeMacbookBackend,
)
from medulla_node.adapters.web import (
    DEFAULT_RESEARCH_DOMAINS,
    StandardWebResearchBackend,
    WebResearchAdapter,
    WebResearchBackend,
)

__all__ = [
    "DevelopmentAdapter", "EventSink", "GpioAdapter", "GpioBackend",
    "GpioZeroBackend", "MacbookAdapter", "MacbookLocation",
    "MacbookSystemBackend", "NativeMacbookBackend", "DEFAULT_RESEARCH_DOMAINS",
    "StandardWebResearchBackend", "WebResearchAdapter", "WebResearchBackend",
    "MedullaAdapter", "MedullaNodeAdapter", "NodeAdapter",
]
