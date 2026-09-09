"""Explicit graceful development restart workflow."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from inspect import isawaitable
from typing import Any

from echo.core.runtime import Runtime
from echo.core.task import TaskStatus
from echo.entity.state_store import StateCategory


class RestartTaskPolicy(StrEnum):
    """How active Tasks are settled before persistence and shutdown."""

    WAIT = "wait"
    CANCEL = "cancel"


class RestartStatus(StrEnum):
    IDLE = "idle"
    QUIESCING = "quiescing"
    SETTLING_TASKS = "settling_tasks"
    PERSISTING_STATE = "persisting_state"
    CLOSING_RESOURCES = "closing_resources"
    STARTING = "starting"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True, kw_only=True, frozen=True)
class RestartTarget:
    """One fresh Runtime generation and the resources it owns."""

    runtime: Runtime
    resources: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, Runtime):
            raise TypeError("restart target runtime must be a Runtime")
        object.__setattr__(self, "resources", tuple(self.resources))


@dataclass(slots=True, kw_only=True, frozen=True)
class RestartResult:
    reason: str
    status: RestartStatus
    task_policy: RestartTaskPolicy
    previous_runtime_id: str
    new_runtime_id: str | None
    initial_task_ids: tuple[str, ...]
    settled_task_ids: tuple[str, ...]
    cancelled_task_ids: tuple[str, ...]
    task_wait_timed_out: bool
    persisted_entity_ids: tuple[str, ...]
    closed_resources: tuple[str, ...]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "status": self.status.value,
            "task_policy": self.task_policy.value,
            "previous_runtime_id": self.previous_runtime_id,
            "new_runtime_id": self.new_runtime_id,
            "initial_task_ids": list(self.initial_task_ids),
            "settled_task_ids": list(self.settled_task_ids),
            "cancelled_task_ids": list(self.cancelled_task_ids),
            "task_wait_timed_out": self.task_wait_timed_out,
            "persisted_entity_ids": list(self.persisted_entity_ids),
            "closed_resources": list(self.closed_resources),
            "error": self.error,
        }


class RestartError(RuntimeError):
    """A graceful restart failed after admission was closed."""

    def __init__(self, result: RestartResult) -> None:
        super().__init__(result.error or "graceful restart failed")
        self.result = result


RuntimeFactory = Callable[
    [], Runtime | RestartTarget | Awaitable[Runtime | RestartTarget]
]


class GracefulRestartCoordinator:
    """Quiesce one Runtime and replace it with a fresh generation."""

    def __init__(
        self,
        runtime: Runtime,
        runtime_factory: RuntimeFactory,
        *,
        resources: Iterable[object] = (),
        task_policy: RestartTaskPolicy | str = RestartTaskPolicy.WAIT,
        task_grace_seconds: float = 5.0,
    ) -> None:
        if not isinstance(runtime, Runtime):
            raise TypeError("runtime must be a Runtime")
        if not callable(runtime_factory):
            raise TypeError("runtime_factory must be callable")
        if (
            isinstance(task_grace_seconds, bool)
            or not isinstance(task_grace_seconds, (int, float))
            or task_grace_seconds < 0
        ):
            raise ValueError("task_grace_seconds must be non-negative")
        self._target = RestartTarget(runtime=runtime, resources=tuple(resources))
        self._runtime_factory = runtime_factory
        self._task_policy = RestartTaskPolicy(task_policy)
        self._task_grace_seconds = float(task_grace_seconds)
        self._status = RestartStatus.IDLE
        self._last_result: RestartResult | None = None
        self._restart_lock = asyncio.Lock()

    @property
    def runtime(self) -> Runtime:
        return self._target.runtime

    @property
    def status(self) -> RestartStatus:
        return self._status

    @property
    def last_result(self) -> RestartResult | None:
        return self._last_result

    async def restart(self, reason: str) -> RestartResult:
        """Run one complete quiesce, persist, close, and fresh-start cycle."""

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("restart reason must not be empty")
        reason = reason.strip()
        async with self._restart_lock:
            old_target = self._target
            old_runtime = old_target.runtime
            initial_task_ids: tuple[str, ...] = ()
            settled_task_ids: tuple[str, ...] = ()
            cancelled_task_ids: tuple[str, ...] = ()
            timed_out = False
            persisted_entity_ids: tuple[str, ...] = ()
            persistent_snapshots: dict[str, dict[str, Any]] = {}
            closed_resources: tuple[str, ...] = ()
            close_attempted = False
            new_target: RestartTarget | None = None
            try:
                self._status = RestartStatus.QUIESCING
                old_runtime.quiesce(reason)
                await _quiesce_resources(old_target.resources)

                self._status = RestartStatus.SETTLING_TASKS
                initial_task_ids = old_runtime.active_task_ids
                if self._task_policy is RestartTaskPolicy.WAIT:
                    timed_out = not await old_runtime.wait_for_active_tasks(
                        self._task_grace_seconds
                    )
                if self._task_policy is RestartTaskPolicy.CANCEL or timed_out:
                    cancelled_task_ids = await _cancel_active_tasks(old_runtime)
                settled_task_ids = tuple(
                    task_id
                    for task_id in initial_task_ids
                    if task_id not in cancelled_task_ids
                )

                self._status = RestartStatus.PERSISTING_STATE
                (
                    persisted_entity_ids,
                    persistent_snapshots,
                ) = _persist_entities(old_runtime)

                self._status = RestartStatus.CLOSING_RESOURCES
                close_attempted = True
                closed_resources = await _close_resources(old_target.resources)
                old_runtime.stop(
                    restart_reason=reason,
                    restart_status="restarting",
                )

                self._status = RestartStatus.STARTING
                new_target = await _build_target(self._runtime_factory)
                if new_target.runtime is old_runtime:
                    raise ValueError("restart factory must create a fresh Runtime")
                if not new_target.runtime.running:
                    new_target.runtime.start()
                _verify_restored_state(new_target.runtime, persistent_snapshots)
                new_target.runtime.report_restart(
                    reason=reason,
                    previous_runtime_id=old_runtime.id,
                    status=RestartStatus.COMPLETED.value,
                )
                self._target = new_target
                self._status = RestartStatus.COMPLETED
                result = RestartResult(
                    reason=reason,
                    status=self._status,
                    task_policy=self._task_policy,
                    previous_runtime_id=old_runtime.id,
                    new_runtime_id=new_target.runtime.id,
                    initial_task_ids=initial_task_ids,
                    settled_task_ids=settled_task_ids,
                    cancelled_task_ids=cancelled_task_ids,
                    task_wait_timed_out=timed_out,
                    persisted_entity_ids=persisted_entity_ids,
                    closed_resources=closed_resources,
                )
                self._last_result = result
                return result
            except Exception as error:
                if new_target is not None and new_target.runtime is not old_runtime:
                    try:
                        await _close_resources(new_target.resources)
                    except Exception:
                        pass
                    new_target.runtime.stop(
                        restart_reason=reason,
                        restart_status=RestartStatus.FAILED.value,
                    )
                try:
                    additionally_cancelled = await _cancel_active_tasks(old_runtime)
                    cancelled_task_ids = tuple(
                        dict.fromkeys(
                            (*cancelled_task_ids, *additionally_cancelled)
                        )
                    )
                except Exception:
                    pass
                settled_task_ids = tuple(
                    task_id
                    for task_id in initial_task_ids
                    if task_id not in cancelled_task_ids
                )
                if not close_attempted:
                    try:
                        closed_resources = await _close_resources(
                            old_target.resources
                        )
                    except Exception:
                        pass
                old_runtime.stop(
                    restart_reason=reason,
                    restart_status=RestartStatus.FAILED.value,
                )
                self._status = RestartStatus.FAILED
                result = RestartResult(
                    reason=reason,
                    status=self._status,
                    task_policy=self._task_policy,
                    previous_runtime_id=old_runtime.id,
                    new_runtime_id=None,
                    initial_task_ids=initial_task_ids,
                    settled_task_ids=settled_task_ids,
                    cancelled_task_ids=cancelled_task_ids,
                    task_wait_timed_out=timed_out,
                    persisted_entity_ids=persisted_entity_ids,
                    closed_resources=closed_resources,
                    error=f"{type(error).__name__}: {error}",
                )
                self._last_result = result
                raise RestartError(result) from error


async def _cancel_active_tasks(runtime: Runtime) -> tuple[str, ...]:
    cancelled: list[str] = []
    for task_id in runtime.active_task_ids:
        result = await runtime.cancel_task(task_id)
        if result is not None and result.status is TaskStatus.CANCELLED:
            cancelled.append(task_id)
    return tuple(cancelled)


def _persist_entities(
    runtime: Runtime,
) -> tuple[tuple[str, ...], dict[str, dict[str, Any]]]:
    persisted: list[str] = []
    snapshots: dict[str, dict[str, Any]] = {}
    for entity in runtime.entities.values():
        persistent = entity.list_state(category=StateCategory.PERSISTENT)
        entity.save_state({StateCategory.PERSISTENT: persistent})
        persisted.append(entity.id)
        snapshots[entity.id] = persistent
    return tuple(persisted), snapshots


def _verify_restored_state(
    runtime: Runtime,
    expected: dict[str, dict[str, Any]],
) -> None:
    for entity_id, persistent in expected.items():
        entity = runtime.get_entity(entity_id)
        if entity is None:
            raise RuntimeError(
                f"fresh Runtime did not restore Entity {entity_id!r}"
            )
        if entity.list_state(category=StateCategory.PERSISTENT) != persistent:
            raise RuntimeError(
                f"fresh Runtime did not restore persistent state for "
                f"Entity {entity_id!r}"
            )


async def _quiesce_resources(resources: tuple[object, ...]) -> None:
    for resource in _unique_resources(resources):
        method = getattr(resource, "quiesce", None)
        if callable(method):
            outcome = method()
            if isawaitable(outcome):
                await outcome


async def _close_resources(resources: tuple[object, ...]) -> tuple[str, ...]:
    closed: list[str] = []
    errors: list[str] = []
    for resource in _unique_resources(resources):
        method = next(
            (
                candidate
                for name in ("aclose", "close", "unload")
                if callable(candidate := getattr(resource, name, None))
            ),
            None,
        )
        if method is None:
            continue
        name = type(resource).__qualname__
        try:
            outcome = method()
            if isawaitable(outcome):
                await outcome
            closed.append(name)
        except Exception as error:
            errors.append(f"{name}: {type(error).__name__}: {error}")
    if errors:
        raise RuntimeError("resource shutdown failed: " + "; ".join(errors))
    return tuple(closed)


def _unique_resources(resources: tuple[object, ...]) -> tuple[object, ...]:
    seen: set[int] = set()
    unique: list[object] = []
    for resource in resources:
        if id(resource) not in seen:
            seen.add(id(resource))
            unique.append(resource)
    return tuple(unique)


async def _build_target(factory: RuntimeFactory) -> RestartTarget:
    built = factory()
    if isawaitable(built):
        built = await built
    if isinstance(built, Runtime):
        return RestartTarget(runtime=built)
    if not isinstance(built, RestartTarget):
        raise TypeError("restart factory must return Runtime or RestartTarget")
    return built
