"""Unified asynchronous coordination for the Echo kernel."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from copy import deepcopy
from inspect import isawaitable
from typing import Any
from uuid import uuid4

from echo.core.action import Action
from echo.core.entity import Entity
from echo.core.runtime_log import (
    InMemoryLogSink,
    LogSink,
    RuntimeEventType,
    RuntimeLogEvent,
)
from echo.core.scheduler import Scheduler, SignalPriority
from echo.core.signal import Signal
from echo.core.signal_history import (
    SignalHistory,
    SignalHistoryEntry,
    SignalRoutingResult,
)
from echo.core.task import Task


class Runtime:
    """Route scheduled Signals to Entity handlers and record their work."""

    def __init__(
        self,
        entities: Iterable[Entity] = (),
        *,
        scheduler: Scheduler | None = None,
        log_sink: LogSink | None = None,
        signal_history_size: int = 1000,
    ) -> None:
        self.id = str(uuid4())
        self.scheduler = scheduler or Scheduler()
        self.log_sink = log_sink or InMemoryLogSink()
        self.signal_history = SignalHistory(max_size=signal_history_size)
        self.running = False
        self.entities: dict[str, Entity] = {}
        self.signals: list[Signal] = []
        self.tasks: list[Task] = []
        self.actions: list[Action] = []
        self._dispatch_lock = asyncio.Lock()
        self._dispatch_owner: asyncio.Task[Any] | None = None
        self._current_entity: Entity | None = None
        self._current_task: Task | None = None
        for entity in entities:
            self.register(entity)
        self.start()

    def start(self) -> None:
        """Mark the Runtime active and record the transition."""

        if self.running:
            return
        self.running = True
        self._log(RuntimeEventType.RUNTIME_STARTED, metadata={"runtime_id": self.id})

    def stop(self) -> None:
        """Mark the Runtime inactive and record the transition."""

        if not self.running:
            return
        self.running = False
        self._log(RuntimeEventType.RUNTIME_STOPPED, metadata={"runtime_id": self.id})

    def register(self, entity: Entity) -> Entity:
        if entity.id in self.entities and self.entities[entity.id] is not entity:
            raise ValueError(f"entity id is already registered: {entity.id}")
        self.entities[entity.id] = entity
        entity._runtime = self
        return entity

    def get_entity(self, entity_id: str) -> Entity | None:
        return self.entities.get(entity_id)

    def latest_signals(self, limit: int | None = None) -> tuple[SignalHistoryEntry, ...]:
        return self.signal_history.latest(limit)

    def get_signal(self, signal_id: str) -> SignalHistoryEntry | None:
        return self.signal_history.get(signal_id)

    def filter_signals(
        self,
        *,
        signal_type: str | None = None,
        source: str | None = None,
    ) -> tuple[SignalHistoryEntry, ...]:
        return self.signal_history.filter(signal_type=signal_type, source=source)

    async def emit(
        self,
        signal: Signal,
        *,
        priority: SignalPriority | int | str = SignalPriority.NORMAL,
    ) -> None:
        """Schedule a Signal and return after that Signal is dispatched."""

        self._log(
            RuntimeEventType.SIGNAL_RECEIVED,
            entity_id=(
                self._current_entity.id if self._current_entity is not None else None
            ),
            signal_id=signal.id,
            task_id=self._current_task.id if self._current_task is not None else None,
            metadata={"signal_type": signal.type, "priority": str(priority)},
        )

        current = asyncio.current_task()
        if current is not None and self._dispatch_owner is current:
            await self.scheduler.schedule(signal, priority)
            await self._process_through(signal)
            return

        async with self._dispatch_lock:
            self._dispatch_owner = current
            try:
                await self.scheduler.schedule(signal, priority)
                await self._process_through(signal)
            finally:
                self._dispatch_owner = None

    async def run_once(self) -> Signal:
        """Process the next Signal already present in the Scheduler."""

        async with self._dispatch_lock:
            self._dispatch_owner = asyncio.current_task()
            try:
                signal = await self.scheduler.next_signal()
                try:
                    await self._dispatch(signal)
                finally:
                    self.scheduler.task_done()
                return signal
            finally:
                self._dispatch_owner = None

    async def drain(self) -> None:
        """Process all Signals currently waiting in priority order."""

        while not self.scheduler.empty():
            await self.run_once()

    async def _process_through(self, target: Signal) -> None:
        while True:
            signal = await self.scheduler.next_signal()
            try:
                await self._dispatch(signal)
            finally:
                self.scheduler.task_done()
            if signal is target:
                return

    async def _dispatch(self, signal: Signal) -> None:
        self.signals.append(signal)
        if len(self.signals) > self.signal_history.max_size:
            del self.signals[0]
        self.signal_history.record(signal)
        entity_ids: list[str] = []
        handler_count = 0
        routing_error: BaseException | None = None
        try:
            for entity in self.entities.values():
                handlers = entity.handlers.resolve(signal)
                if handlers:
                    entity_ids.append(entity.id)
                    self._log(
                        RuntimeEventType.SIGNAL_ROUTED,
                        entity_id=entity.id,
                        signal_id=signal.id,
                        metadata={
                            "signal_type": signal.type,
                            "handler_count": len(handlers),
                        },
                    )
                for handler in handlers:
                    handler_count += 1
                    await self._run_handler(entity, handler, signal)
        except BaseException as error:
            routing_error = error
            raise
        finally:
            routed_tasks = [
                task
                for task in self.tasks
                if isinstance(task.context.get("signal"), dict)
                and task.context["signal"].get("id") == signal.id
            ]
            statuses = {task.id: task.status.value for task in routed_tasks}
            if routing_error is not None:
                status = (
                    "cancelled"
                    if isinstance(routing_error, asyncio.CancelledError)
                    else "failed"
                )
                error_data = {
                    "type": type(routing_error).__name__,
                    "message": str(routing_error),
                }
            else:
                status = "completed" if handler_count else "unhandled"
                error_data = None
            self.signal_history.set_routing_result(
                signal.id,
                SignalRoutingResult(
                    status=status,
                    entity_ids=tuple(entity_ids),
                    handler_count=handler_count,
                    task_ids=tuple(task.id for task in routed_tasks),
                    task_statuses=statuses,
                    error=error_data,
                ),
            )

    async def _run_handler(self, entity: Entity, handler: Any, signal: Signal) -> None:
        task = Task(
            name=getattr(handler, "__name__", handler.__class__.__name__),
            owner=entity.id,
            context={"signal": signal.to_dict()},
        )
        self.tasks.append(task)
        self._log(
            RuntimeEventType.TASK_CREATED,
            entity_id=entity.id,
            signal_id=signal.id,
            task_id=task.id,
            metadata={"name": task.name, "status": task.status.value},
        )
        entity.active_tasks[task.id] = task
        previous_entity = self._current_entity
        previous_task = self._current_task
        self._current_entity = entity
        self._current_task = task
        state_before = self._copy_state(entity.state)
        task.start()
        self._log_task_status(task, signal, "pending")
        try:
            result = handler(signal)
            if isawaitable(result):
                result = await result
            self._collect_result(result, entity, task)
            task.complete(result)
            self._log_task_status(task, signal, "running")
        except asyncio.CancelledError:
            task.cancel()
            self._log_task_status(task, signal, "running")
            raise
        except Exception as error:
            task.fail(error)
            self._log_task_status(task, signal, "running")
            self._log(
                RuntimeEventType.ERROR,
                entity_id=entity.id,
                signal_id=signal.id,
                task_id=task.id,
                metadata={
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "operation": "handler",
                },
            )
            raise
        finally:
            self._log_state_changes(entity, signal, task, state_before)
            entity.active_tasks.pop(task.id, None)
            self._current_entity = previous_entity
            self._current_task = previous_task

    def record_action(
        self,
        action: Action,
        entity: Entity,
        task: Task | None = None,
    ) -> Action:
        if task is None and self._current_entity is entity:
            task = self._current_task
        if action.entity_id is None:
            action.entity_id = entity.id
        if action.task_id is None and task is not None:
            action.task_id = task.id
        is_new = all(existing.id != action.id for existing in self.actions)
        if is_new:
            self.actions.append(action)
            signal_id = None
            if task is not None:
                signal_data = task.context.get("signal", {})
                if isinstance(signal_data, dict):
                    signal_id = signal_data.get("id")
            metadata = {
                "action_type": action.type,
                "parameters": action.parameters.copy(),
            }
            self._log(
                RuntimeEventType.ACTION_CREATED,
                entity_id=action.entity_id,
                signal_id=signal_id,
                task_id=action.task_id,
                action_id=action.id,
                metadata=metadata,
            )
            self._log(
                RuntimeEventType.ACTION_EXECUTED,
                entity_id=action.entity_id,
                signal_id=signal_id,
                task_id=action.task_id,
                action_id=action.id,
                metadata=metadata,
            )
        return action

    def _collect_result(self, result: Any, entity: Entity, task: Task) -> None:
        if isinstance(result, Action):
            self.record_action(result, entity, task)
        elif isinstance(result, (list, tuple)):
            for value in result:
                if isinstance(value, Action):
                    self.record_action(value, entity, task)

    def _log_task_status(self, task: Task, signal: Signal, previous: str) -> None:
        self._log(
            RuntimeEventType.TASK_STATUS_CHANGED,
            entity_id=task.owner,
            signal_id=signal.id,
            task_id=task.id,
            metadata={"previous_status": previous, "status": task.status.value},
        )

    def _log_state_changes(
        self,
        entity: Entity,
        signal: Signal,
        task: Task,
        before: dict[str, Any],
    ) -> None:
        try:
            after = self._copy_state(entity.state)
            keys = before.keys() | after.keys()
            changes: dict[str, dict[str, Any]] = {}
            for key in keys:
                if key not in before:
                    changes[key] = {"operation": "added", "after": after[key]}
                elif key not in after:
                    changes[key] = {"operation": "removed", "before": before[key]}
                elif before[key] != after[key]:
                    changes[key] = {
                        "operation": "updated",
                        "before": before[key],
                        "after": after[key],
                    }
            if changes:
                self._log(
                    RuntimeEventType.STATE_CHANGED,
                    entity_id=entity.id,
                    signal_id=signal.id,
                    task_id=task.id,
                    metadata={"changes": changes},
                )
        except Exception:
            pass

    @staticmethod
    def _copy_state(state: dict[str, Any]) -> dict[str, Any]:
        try:
            return deepcopy(state)
        except Exception:
            return state.copy()

    def _log(
        self,
        event_type: RuntimeEventType,
        *,
        entity_id: str | None = None,
        signal_id: str | None = None,
        task_id: str | None = None,
        action_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Write an observation without allowing sink failures into the Runtime."""

        try:
            self.log_sink.write(
                RuntimeLogEvent(
                    event_type=event_type,
                    entity_id=entity_id,
                    signal_id=signal_id,
                    task_id=task_id,
                    action_id=action_id,
                    metadata=dict(metadata or {}),
                )
            )
        except Exception:
            pass
