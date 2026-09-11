"""Deterministic capability-to-transport Action routing for Medulla."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Iterable, Protocol, runtime_checkable

from echo.core.action import Action
from echo.medulla.capability import (
    Capability,
    CapabilityAvailabilityState,
    CapabilityProviderHealthState,
    CapabilityRegistry,
)
from echo.medulla.transport import (
    ActionDispatchResult,
    ActionDispatchState,
    TransportError,
    TransportErrorCode,
    TransportErrorInfo,
    TransportOperation,
    validate_outbound_action,
)


@dataclass(slots=True, frozen=True, kw_only=True)
class CapabilityPermissionDecision:
    """Result returned by application-owned permission policy."""

    allowed: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise TypeError("allowed must be a bool")
        if self.reason is not None and (
            not isinstance(self.reason, str) or not self.reason
        ):
            raise ValueError("reason must be a non-empty string when provided")
        if not self.allowed and self.reason is None:
            raise ValueError("a denied permission decision requires a reason")


@runtime_checkable
class CapabilityPermissionHook(Protocol):
    """Application policy hook; the router itself grants no permission."""

    async def __call__(
        self, action: Action, capability: Capability
    ) -> CapabilityPermissionDecision: ...


@runtime_checkable
class ActionDispatcher(Protocol):
    """Transport-neutral dispatch port implemented by MedullaSupervisor."""

    async def dispatch(
        self, action: Action, *, transport_id: str | None = None
    ) -> ActionDispatchResult: ...


@dataclass(slots=True, frozen=True, kw_only=True)
class CapabilityRouteBinding:
    """Configured ownership path from one provider to one transport."""

    provider_id: str
    transport_id: str
    priority: int = 100

    def __post_init__(self) -> None:
        for name, value in (
            ("provider_id", self.provider_id),
            ("transport_id", self.transport_id),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if type(self.priority) is not int or self.priority < 0:
            raise ValueError("priority must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "transport_id": self.transport_id,
            "priority": self.priority,
        }


@dataclass(slots=True, frozen=True, kw_only=True)
class CapabilityRoute:
    """One currently eligible, deterministic route candidate."""

    capability: Capability
    binding: CapabilityRouteBinding

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability.capability_id,
            "capability_name": self.capability.name,
            "provider_id": self.capability.provider.provider_id,
            "transport_id": self.binding.transport_id,
            "priority": self.binding.priority,
            "availability": self.capability.availability.state.value,
        }


@dataclass(slots=True, frozen=True, kw_only=True)
class CapabilityRouterStatus:
    bindings: tuple[CapabilityRouteBinding, ...]
    eligible_routes: tuple[CapabilityRoute, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "bindings": [item.to_dict() for item in self.bindings],
            "eligible_routes": [item.to_dict() for item in self.eligible_routes],
        }


class CapabilityRouter:
    """Resolve an already-chosen Action type to one configured transport.

    Echo chooses the Action. This router checks declared availability and
    provider health, delegates permission to an injected hook, and selects a
    stable route. It never plans an Action and never retries another provider
    after execution begins.
    """

    def __init__(
        self,
        registry: CapabilityRegistry,
        dispatcher: ActionDispatcher,
        permission_hook: CapabilityPermissionHook,
        bindings: Iterable[CapabilityRouteBinding] = (),
    ) -> None:
        if not isinstance(registry, CapabilityRegistry):
            raise TypeError("registry must be a CapabilityRegistry")
        if not isinstance(dispatcher, ActionDispatcher):
            raise TypeError("dispatcher must implement ActionDispatcher")
        if not isinstance(permission_hook, CapabilityPermissionHook):
            raise TypeError("permission_hook must implement CapabilityPermissionHook")
        self._registry = registry
        self._dispatcher = dispatcher
        self._permission_hook = permission_hook
        self._bindings: dict[str, CapabilityRouteBinding] = {}
        for binding in bindings:
            self.bind(binding)

    @property
    def registry(self) -> CapabilityRegistry:
        return self._registry

    def bind(self, binding: CapabilityRouteBinding) -> None:
        if not isinstance(binding, CapabilityRouteBinding):
            raise TypeError("binding must be a CapabilityRouteBinding")
        if self._registry.provider(binding.provider_id) is None:
            raise ValueError(f"provider is not registered: {binding.provider_id}")
        if binding.provider_id in self._bindings:
            raise ValueError(f"provider already has a route: {binding.provider_id}")
        self._bindings[binding.provider_id] = binding

    def binding(self, provider_id: str) -> CapabilityRouteBinding | None:
        return self._bindings.get(provider_id)

    def unbind(self, provider_id: str) -> CapabilityRouteBinding | None:
        return self._bindings.pop(provider_id, None)

    def candidates(
        self, capability_name: str, *, provider_id: str | None = None
    ) -> tuple[CapabilityRoute, ...]:
        candidates: list[CapabilityRoute] = []
        for capability in self._registry.find(capability_name):
            owner_id = capability.provider.provider_id
            if provider_id is not None and owner_id != provider_id:
                continue
            binding = self._bindings.get(owner_id)
            health = self._registry.health(owner_id)
            if binding is None or health is None:
                continue
            if capability.availability.state not in {
                CapabilityAvailabilityState.AVAILABLE,
                CapabilityAvailabilityState.DEGRADED,
            }:
                continue
            if health.state is CapabilityProviderHealthState.UNAVAILABLE:
                continue
            candidates.append(CapabilityRoute(capability=capability, binding=binding))
        return tuple(sorted(candidates, key=self._route_sort_key))

    def status(self) -> CapabilityRouterStatus:
        bindings = tuple(self._bindings[item] for item in sorted(self._bindings))
        eligible: list[CapabilityRoute] = []
        for capability in self._registry.inspect():
            eligible.extend(self.candidates(capability.name))
        unique = {item.capability.capability_id: item for item in eligible}
        return CapabilityRouterStatus(
            bindings=bindings,
            eligible_routes=tuple(
                sorted(unique.values(), key=lambda item: item.capability.capability_id)
            ),
        )

    async def dispatch(
        self,
        action: Action,
        *,
        provider_id: str | None = None,
        capability_id: str | None = None,
    ) -> ActionDispatchResult:
        """Route one Action and contain every policy/provider/transport failure."""

        try:
            safe_action = validate_outbound_action(action)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            action_id = action.id if isinstance(action, Action) else "invalid"
            return self._failure(
                action_id=action_id,
                transport_id="unresolved",
                code=TransportErrorCode.INVALID_PAYLOAD,
                message=str(error),
            )

        routes = list(self.candidates(safe_action.type, provider_id=provider_id))
        if capability_id is not None:
            routes = [
                route
                for route in routes
                if route.capability.capability_id == capability_id
            ]
        if not routes:
            return self._failure(
                action_id=safe_action.id,
                transport_id="unresolved",
                code=TransportErrorCode.UNAVAILABLE,
                message=f"no available capability route for Action type: {safe_action.type}",
                retryable=True,
            )

        permitted: list[CapabilityRoute] = []
        denials: list[str] = []
        for route in routes:
            try:
                decision = await self._permission_hook(safe_action, route.capability)
                if not isinstance(decision, CapabilityPermissionDecision):
                    raise TypeError(
                        "permission hook must return CapabilityPermissionDecision"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                return self._failure(
                    action_id=safe_action.id,
                    transport_id=route.binding.transport_id,
                    code=TransportErrorCode.PERMISSION_DENIED,
                    message=f"permission check failed: {error}",
                )
            if decision.allowed:
                permitted.append(route)
            else:
                denials.append(
                    f"{route.capability.capability_id}: {decision.reason}"
                )

        if not permitted:
            return self._failure(
                action_id=safe_action.id,
                transport_id=routes[0].binding.transport_id,
                code=TransportErrorCode.PERMISSION_DENIED,
                message="permission denied; " + "; ".join(denials),
            )

        selected = permitted[0]
        try:
            async with asyncio.timeout(selected.capability.timeout.maximum_seconds):
                result = await self._dispatcher.dispatch(
                    safe_action, transport_id=selected.binding.transport_id
                )
            if not isinstance(result, ActionDispatchResult):
                raise TypeError("dispatcher must return ActionDispatchResult")
            if result.action_id != safe_action.id:
                raise ValueError("dispatch result action_id does not match Action")
            if result.transport_id != selected.binding.transport_id:
                raise ValueError("dispatch result transport_id does not match route")
            return result
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return self._failure(
                action_id=safe_action.id,
                transport_id=selected.binding.transport_id,
                code=TransportErrorCode.TIMEOUT,
                message=(
                    "capability dispatch exceeded maximum timeout of "
                    f"{selected.capability.timeout.maximum_seconds:g} seconds"
                ),
                retryable=True,
            )
        except TransportError as error:
            return ActionDispatchResult(
                transport_id=selected.binding.transport_id,
                action_id=safe_action.id,
                state=ActionDispatchState.FAILED,
                error=TransportErrorInfo(
                    transport_id=selected.binding.transport_id,
                    operation=TransportOperation.EXECUTE,
                    code=error.code,
                    message=str(error),
                    retryable=error.retryable,
                ),
            )
        except Exception as error:
            return self._failure(
                action_id=safe_action.id,
                transport_id=selected.binding.transport_id,
                code=TransportErrorCode.EXECUTION_FAILED,
                message=f"capability provider failed: {error}",
            )

    def _route_sort_key(self, route: CapabilityRoute) -> tuple[int, int, int, str, str]:
        availability_rank = {
            CapabilityAvailabilityState.AVAILABLE: 0,
            CapabilityAvailabilityState.DEGRADED: 1,
        }[route.capability.availability.state]
        health = self._registry.health(route.capability.provider.provider_id)
        health_rank = {
            CapabilityProviderHealthState.HEALTHY: 0,
            CapabilityProviderHealthState.DEGRADED: 1,
            CapabilityProviderHealthState.UNKNOWN: 2,
        }[health.state]
        return (
            availability_rank,
            health_rank,
            route.binding.priority,
            route.capability.provider.provider_id,
            route.capability.capability_id,
        )

    @staticmethod
    def _failure(
        *,
        action_id: str,
        transport_id: str,
        code: TransportErrorCode,
        message: str,
        retryable: bool = False,
    ) -> ActionDispatchResult:
        return ActionDispatchResult(
            transport_id=transport_id,
            action_id=action_id,
            state=ActionDispatchState.FAILED,
            error=TransportErrorInfo(
                transport_id=transport_id,
                operation=TransportOperation.EXECUTE,
                code=code,
                message=message,
                retryable=retryable,
            ),
        )


__all__ = [
    "ActionDispatcher",
    "CapabilityPermissionDecision",
    "CapabilityPermissionHook",
    "CapabilityRoute",
    "CapabilityRouteBinding",
    "CapabilityRouter",
    "CapabilityRouterStatus",
]
