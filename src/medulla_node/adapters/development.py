"""Deterministic in-memory adapter for development and integration tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from medulla_node.adapters import MedullaAdapter
from medulla_protocol import (
    AdapterEvent,
    Capability,
    CapabilityAvailability,
    CapabilityAvailabilityState,
    CapabilityEffect,
    CapabilityPermissions,
    CapabilityProvider,
    CapabilityRisk,
    CapabilityTimeout,
    MedullaNodeResource,
    MedullaNodeSignal,
    NodeAction,
)


class DevelopmentAdapter(MedullaAdapter):
    def __init__(self, provider: CapabilityProvider) -> None:
        if not isinstance(provider, CapabilityProvider):
            raise TypeError("provider must be CapabilityProvider")
        self._counter = 0
        self._started = False
        super().__init__(
            adapter_id="development",
            capabilities=tuple(
                _capability(provider, name, description, effect)
                for name, description, effect in (
                    ("dev.echo", "Return the supplied parameters", CapabilityEffect.READ_ONLY),
                    ("counter.increment", "Increment the development counter", CapabilityEffect.STATE_CHANGING),
                    ("counter.reset", "Reset the development counter", CapabilityEffect.STATE_CHANGING),
                )
            ),
            signals=(
                MedullaNodeSignal(
                    name="counter.changed",
                    schema={"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]},
                    adapter="development",
                    description="The development counter changed",
                ),
            ),
            resources=(
                MedullaNodeResource(
                    resource_id="counter.main",
                    description="Deterministic development counter",
                    schema={"type": "integer"},
                ),
            ),
        )

    @property
    def counter(self) -> int:
        return self._counter

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    async def execute(self, action: NodeAction) -> Mapping[str, Any]:
        if not self._started:
            raise RuntimeError("development adapter is not running")
        if action.type == "dev.echo":
            return {"echo": action.parameters}
        if action.type == "counter.increment":
            amount = action.parameters.get("amount", 1)
            if type(amount) is not int:
                raise ValueError("counter.increment amount must be an integer")
            self._counter += amount
            await self._counter_changed(action)
            return {"value": self._counter}
        if action.type == "counter.reset":
            self._counter = 0
            await self._counter_changed(action)
            return {"value": self._counter}
        raise ValueError(f"unsupported development Action: {action.type}")

    async def _counter_changed(self, action: NodeAction) -> None:
        await self.emit(
            AdapterEvent(
                signal_type="counter.changed",
                payload={"value": self._counter},
                resource_id="counter.main",
                correlation_id=action.correlation_id or action.id,
            )
        )


def _capability(
    provider: CapabilityProvider,
    name: str,
    description: str,
    effect: CapabilityEffect,
) -> Capability:
    return Capability(
        capability_id=f"{provider.provider_id}.{name}.v1",
        name=name,
        description=description,
        provider=provider,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        availability=CapabilityAvailability(state=CapabilityAvailabilityState.AVAILABLE),
        permissions=CapabilityPermissions(
            risk=CapabilityRisk.READ if effect is CapabilityEffect.READ_ONLY else CapabilityRisk.STATE_CHANGE
        ),
        effect=effect,
        timeout=CapabilityTimeout(expected_seconds=0.1, maximum_seconds=5.0),
    )


__all__ = ["DevelopmentAdapter"]
