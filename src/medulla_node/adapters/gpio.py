"""Optional Raspberry Pi GPIO adapter with an injectable validation backend."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any, Protocol

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


class GpioBackend(Protocol):
    def setup_output(self, pin: int) -> None: ...
    def setup_input(self, pin: int, changed: Callable[[bool], None]) -> None: ...
    def write(self, pin: int, enabled: bool) -> None: ...
    def close(self) -> None: ...


class GpioZeroBackend:
    """Lazy gpiozero bridge used only on a configured Raspberry Pi."""

    def __init__(self) -> None:
        try:
            from gpiozero import Button, LED
        except ImportError as error:
            raise RuntimeError("GPIO adapter requires the optional 'gpiozero' package") from error
        self._button_type = Button
        self._led_type = LED
        self._outputs: dict[int, Any] = {}
        self._inputs: dict[int, Any] = {}

    def setup_output(self, pin: int) -> None:
        self._outputs[pin] = self._led_type(pin)

    def setup_input(self, pin: int, changed: Callable[[bool], None]) -> None:
        button = self._button_type(pin)
        button.when_pressed = lambda: changed(True)
        button.when_released = lambda: changed(False)
        self._inputs[pin] = button

    def write(self, pin: int, enabled: bool) -> None:
        (self._outputs[pin].on if enabled else self._outputs[pin].off)()

    def close(self) -> None:
        for device in (*self._inputs.values(), *self._outputs.values()):
            device.close()
        self._inputs.clear()
        self._outputs.clear()


class GpioAdapter(MedullaAdapter):
    def __init__(
        self,
        provider: CapabilityProvider,
        *,
        output_pins: tuple[int, ...] = (),
        input_pins: tuple[int, ...] = (),
        backend: GpioBackend | None = None,
    ) -> None:
        if not isinstance(provider, CapabilityProvider):
            raise TypeError("provider must be CapabilityProvider")
        self._output_pins = self._pins(output_pins, "output_pins")
        self._input_pins = self._pins(input_pins, "input_pins")
        if set(self._output_pins) & set(self._input_pins):
            raise ValueError("a GPIO pin cannot be both input and output")
        if not self._output_pins and not self._input_pins:
            raise ValueError("at least one GPIO pin must be configured")
        self._backend = backend
        self._active_backend: GpioBackend | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        resources = tuple(
            MedullaNodeResource(
                resource_id=f"gpio.pin.{pin}",
                description=f"GPIO {direction} pin {pin}",
                schema={"type": "boolean"},
                metadata={"pin": pin, "direction": direction},
            )
            for direction, pins in (("output", self._output_pins), ("input", self._input_pins))
            for pin in pins
        )
        capabilities = (
            (_capability(provider, "gpio.output.set", "Set a GPIO output") if self._output_pins else None),
        )
        signals = []
        if self._output_pins:
            signals.append(MedullaNodeSignal(name="gpio.output.changed", schema={"type": "object"}, adapter="gpio"))
        if self._input_pins:
            signals.append(MedullaNodeSignal(name="gpio.input.changed", schema={"type": "object"}, adapter="gpio"))
        super().__init__(
            adapter_id="gpio",
            capabilities=tuple(item for item in capabilities if item is not None),
            signals=tuple(signals),
            resources=resources,
        )

    @staticmethod
    def _pins(values: tuple[int, ...], name: str) -> tuple[int, ...]:
        pins = tuple(values)
        if any(type(pin) is not int or pin < 0 for pin in pins):
            raise ValueError(f"{name} must contain non-negative integer BCM pin numbers")
        if len(pins) != len(set(pins)):
            raise ValueError(f"{name} must not contain duplicates")
        return pins

    async def start(self) -> None:
        backend = self._backend or GpioZeroBackend()
        self._loop = asyncio.get_running_loop()
        for pin in self._output_pins:
            backend.setup_output(pin)
        for pin in self._input_pins:
            backend.setup_input(pin, lambda state, pin=pin: self._input_changed(pin, state))
        self._active_backend = backend
        self._started = True

    async def stop(self) -> None:
        self._started = False
        if self._active_backend is not None:
            self._active_backend.close()
        self._active_backend = None
        self._loop = None

    async def execute(self, action: NodeAction) -> Mapping[str, Any]:
        if not self._started or self._active_backend is None:
            raise RuntimeError("GPIO adapter is not running")
        if action.type != "gpio.output.set":
            raise ValueError(f"unsupported GPIO Action: {action.type}")
        if action.resource_id is None or not action.resource_id.startswith("gpio.pin."):
            raise ValueError("gpio.output.set requires a GPIO resource_id")
        try:
            pin = int(action.resource_id.removeprefix("gpio.pin."))
        except ValueError as error:
            raise ValueError("GPIO resource_id contains an invalid pin") from error
        if pin not in self._output_pins:
            raise ValueError("GPIO resource is not a configured output")
        enabled = action.parameters.get("enabled")
        if type(enabled) is not bool:
            raise ValueError("gpio.output.set requires boolean parameter 'enabled'")
        self._active_backend.write(pin, enabled)
        await self.emit(
            AdapterEvent(
                signal_type="gpio.output.changed",
                resource_id=action.resource_id,
                correlation_id=action.correlation_id or action.id,
                payload={"pin": pin, "enabled": enabled},
            )
        )
        return {"pin": pin, "enabled": enabled}

    async def emit_input(self, pin: int, active: bool) -> None:
        if not self._started or pin not in self._input_pins or type(active) is not bool:
            raise ValueError("input event must name a configured pin and boolean state")
        await self.emit(
            AdapterEvent(
                signal_type="gpio.input.changed",
                resource_id=f"gpio.pin.{pin}",
                payload={"pin": pin, "active": active},
            )
        )

    def _input_changed(self, pin: int, active: bool) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(
                asyncio.create_task,
                self.emit_input(pin, bool(active)),
            )


def _capability(
    provider: CapabilityProvider,
    name: str,
    description: str,
) -> Capability:
    return Capability(
        capability_id=f"{provider.provider_id}.{name}.v1",
        name=name,
        description=description,
        provider=provider,
        input_schema={
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
        },
        output_schema={"type": "object"},
        availability=CapabilityAvailability(state=CapabilityAvailabilityState.AVAILABLE),
        permissions=CapabilityPermissions(
            risk=CapabilityRisk.STATE_CHANGE,
            required=("hardware.gpio.write",),
        ),
        effect=CapabilityEffect.STATE_CHANGING,
        timeout=CapabilityTimeout(expected_seconds=0.1, maximum_seconds=2),
    )


__all__ = ["GpioAdapter", "GpioBackend", "GpioZeroBackend"]
