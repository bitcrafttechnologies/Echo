"""Unified asynchronous coordination for the Echo kernel."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from collections import deque
from copy import deepcopy
from inspect import isawaitable
from time import monotonic
from typing import Any
from uuid import uuid4

from echo.core.action import Action
from echo.core.action_history import ActionHistory, ActionHistoryEntry, ActionStatus
from echo.core.entity import Entity
from echo.core.inspection import json_safe
from echo.core.runtime_log import (
    InMemoryLogSink,
    LogSink,
    RuntimeEventType,
    RuntimeLogSeverity,
    RuntimeLogEvent,
)
from echo.core.scheduler import Scheduler, SignalPriority
from echo.core.signal import Signal
from echo.core.signal_history import (
    SignalHistory,
    SignalHistoryEntry,
    SignalRoutingResult,
)
from echo.core.task import Task, TaskStatus
from echo.core.task_history import TaskHistory, TaskHistoryEntry
from echo.entity.influence import SignalInfluence
from echo.entity.state_store import StateCategory
from echo.runtime_events import (
    RuntimeEventBroker,
    RuntimeEventSubscription,
    RuntimeSubscriptionRequest,
)


class RuntimeNotAcceptingWorkError(RuntimeError):
    """A Signal was submitted while the Runtime was quiescing or stopped."""


class Runtime:
    """Route scheduled Signals to Entity handlers and record their work."""

    def __init__(
        self,
        entities: Iterable[Entity] = (),
        *,
        scheduler: Scheduler | None = None,
        log_sink: LogSink | None = None,
        signal_history_size: int = 1000,
        task_history_size: int = 1000,
        action_history_size: int = 1000,
        log_history_size: int = 1000,
        error_history_size: int = 1000,
        auto_start: bool = True,
    ) -> None:
        self.id = str(uuid4())
        self.scheduler = scheduler or Scheduler()
        self.log_sink = log_sink or InMemoryLogSink()
        self.signal_history = SignalHistory(max_size=signal_history_size)
        self.task_history = TaskHistory(max_size=task_history_size)
        self.action_history = ActionHistory(max_size=action_history_size)
        self.running = False
        self._accepting_work = False
        self._restart_report: dict[str, Any] | None = None
        self._uptime_seconds = 0.0
        self._uptime_started: float | None = None
        self.entities: dict[str, Entity] = {}
        self.signals: list[Signal] = []
        self.tasks: list[Task] = []
        self.actions: list[Action] = []
        self._event_broker = RuntimeEventBroker()
        self._dispatch_lock = asyncio.Lock()
        self._dispatch_owner: asyncio.Task[Any] | None = None
        self._current_entity: Entity | None = None
        self._current_task: Task | None = None
        self._current_action_ids: set[str] | None = None
        self._routing_tasks: dict[str, list[Task]] = {}
        self._active_task_runs: dict[
            str, tuple[asyncio.Task[Any], asyncio.Event]
        ] = {}
        if isinstance(log_history_size, bool) or log_history_size < 1:
            raise ValueError("log_history_size must be a positive integer")
        if isinstance(error_history_size, bool) or error_history_size < 1:
            raise ValueError("error_history_size must be a positive integer")
        if not isinstance(auto_start, bool):
            raise ValueError("auto_start must be a boolean")
        self._log_events: deque[RuntimeLogEvent] = deque(maxlen=log_history_size)
        self._recent_errors: deque[RuntimeLogEvent] = deque(maxlen=error_history_size)
        for entity in entities:
            self.register(entity)
        if auto_start:
            self.start()

    def start(self) -> None:
        """Mark the Runtime active and record the transition."""

        if self.running:
            return
        self.running = True
        self._accepting_work = True
        self._uptime_started = monotonic()
        self._log(RuntimeEventType.RUNTIME_STARTED, metadata={"runtime_id": self.id})

    def stop(
        self,
        *,
        restart_reason: str | None = None,
        restart_status: str | None = None,
    ) -> None:
        """Mark the Runtime inactive and record the transition."""

        self._accepting_work = False
        if not self.running:
            return
        if self._uptime_started is not None:
            self._uptime_seconds += monotonic() - self._uptime_started
            self._uptime_started = None
        self.running = False
        metadata = {"runtime_id": self.id}
        if restart_reason is not None:
            metadata["restart_reason"] = restart_reason
        if restart_status is not None:
            metadata["restart_status"] = restart_status
        self._log(RuntimeEventType.RUNTIME_STOPPED, metadata=metadata)

    @property
    def accepting_work(self) -> bool:
        return self._accepting_work

    @property
    def active_task_ids(self) -> tuple[str, ...]:
        return tuple(self._active_task_runs)

    def quiesce(self, reason: str) -> None:
        """Stop admitting new Signals while existing Tasks are settled."""

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("restart reason must not be empty")
        if not self._accepting_work:
            return
        self._accepting_work = False
        self._log(
            RuntimeEventType.RUNTIME_QUIESCING,
            metadata={"runtime_id": self.id, "restart_reason": reason.strip()},
        )

    async def wait_for_active_tasks(self, timeout_seconds: float) -> bool:
        """Wait for current handler Tasks, returning false on timeout."""

        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds < 0
        ):
            raise ValueError("task wait timeout must be non-negative")
        completions = tuple(
            completed.wait() for _execution, completed in self._active_task_runs.values()
        )
        if not completions:
            return True
        try:
            await asyncio.wait_for(
                asyncio.gather(*completions), timeout=float(timeout_seconds)
            )
        except TimeoutError:
            return False
        return True

    def report_restart(
        self,
        *,
        reason: str,
        previous_runtime_id: str,
        status: str,
    ) -> None:
        """Attach and publish startup metadata for a completed restart."""

        if not all(
            isinstance(value, str) and value.strip()
            for value in (reason, previous_runtime_id, status)
        ):
            raise ValueError("restart report values must not be empty")
        self._restart_report = {
            "reason": reason.strip(),
            "status": status.strip(),
            "previous_runtime_id": previous_runtime_id,
            "runtime_id": self.id,
        }
        self._log(
            RuntimeEventType.RUNTIME_RESTARTED,
            metadata=deepcopy(self._restart_report),
        )

    def resize_histories(
        self,
        *,
        signals: int,
        tasks: int,
        actions: int,
        logs: int,
        errors: int,
    ) -> None:
        """Apply validated retention limits without replacing the Runtime."""

        limits = {
            "signals": signals,
            "tasks": tasks,
            "actions": actions,
            "logs": logs,
            "errors": errors,
        }
        for name, limit in limits.items():
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise ValueError(f"{name} history limit must be a positive integer")
        self.signal_history.resize(signals)
        self.task_history.resize(tasks)
        self.action_history.resize(actions)
        self._log_events = deque(self._log_events, maxlen=logs)
        self._recent_errors = deque(self._recent_errors, maxlen=errors)

    def audit_configuration_reload(self, metadata: dict[str, Any]) -> None:
        """Publish one structured, detached configuration reload audit event."""

        if not isinstance(metadata, dict):
            raise TypeError("configuration reload metadata must be a dictionary")
        self._log(
            RuntimeEventType.CONFIGURATION_RELOAD,
            metadata=deepcopy(metadata),
        )

    def register(self, entity: Entity) -> Entity:
        if entity.id in self.entities and self.entities[entity.id] is not entity:
            raise ValueError(f"entity id is already registered: {entity.id}")
        self.entities[entity.id] = entity
        entity._runtime = self
        return entity

    def get_entity(self, entity_id: str) -> Entity | None:
        return self.entities.get(entity_id)

    def subscribe_events(
        self,
        request: RuntimeSubscriptionRequest | None = None,
    ) -> RuntimeEventSubscription:
        """Create an isolated bounded subscription to future Runtime events."""

        return self._event_broker.subscribe(request)

    @property
    def uptime(self) -> float:
        uptime = self._uptime_seconds
        if self.running and self._uptime_started is not None:
            uptime += monotonic() - self._uptime_started
        return uptime

    def inspect(self, *, recent_limit: int = 20) -> dict[str, Any]:
        """Return a detached, JSON-safe snapshot of current Runtime state."""

        if (
            isinstance(recent_limit, bool)
            or not isinstance(recent_limit, int)
            or recent_limit < 0
        ):
            raise ValueError("recent_limit must be a non-negative integer")

        active_tasks = [
            TaskHistoryEntry.from_task(task).to_dict()
            for entity in self.entities.values()
            for task in entity.active_tasks.values()
        ]
        scheduler = self.scheduler.inspect()
        queued_signals = scheduler.pop("queued_signals")
        errors = list(reversed(self._recent_errors))[:recent_limit]
        if not self.running:
            runtime_status = "stopped"
        elif active_tasks:
            runtime_status = "active"
        else:
            runtime_status = "idle"

        snapshot = {
            "runtime_id": self.id,
            "runtime_status": runtime_status,
            "uptime_seconds": self.uptime,
            "accepting_work": self.accepting_work,
            "restart": deepcopy(self._restart_report),
            "entity_ids": list(self.entities),
            "entity_state": {
                entity.id: entity.state for entity in self.entities.values()
            },
            "entity_character": {
                entity.id: entity.inspect_character()
                for entity in self.entities.values()
            },
            "active_tasks": active_tasks,
            "queued_signals": queued_signals,
            "recent_signals": [
                entry.to_dict() for entry in self.latest_signals(recent_limit)
            ],
            "recent_actions": [
                entry.to_dict() for entry in self.latest_actions(recent_limit)
            ],
            "recent_errors": [event.to_dict() for event in errors],
            "scheduler": scheduler,
            "handler_registry": {
                entity.id: entity.handlers.inspect()
                for entity in self.entities.values()
            },
        }
        return json_safe(snapshot)

    def snapshot(self, *, recent_limit: int = 20) -> dict[str, Any]:
        """Alias for inspect()."""

        return self.inspect(recent_limit=recent_limit)

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

    def latest_tasks(self, limit: int | None = None) -> tuple[TaskHistoryEntry, ...]:
        return self.task_history.latest(limit)

    def get_task(self, task_id: str) -> TaskHistoryEntry | None:
        return self.task_history.get(task_id)

    def filter_tasks(
        self,
        *,
        status: TaskStatus | str | None = None,
    ) -> tuple[TaskHistoryEntry, ...]:
        return self.task_history.filter(status=status)

    def latest_actions(
        self,
        limit: int | None = None,
    ) -> tuple[ActionHistoryEntry, ...]:
        return self.action_history.latest(limit)

    def get_action(self, action_id: str) -> ActionHistoryEntry | None:
        return self.action_history.get(action_id)

    def filter_actions(
        self,
        *,
        action_type: str | None = None,
        status: ActionStatus | str | None = None,
        task_id: str | None = None,
        signal_id: str | None = None,
    ) -> tuple[ActionHistoryEntry, ...]:
        return self.action_history.filter(
            action_type=action_type,
            status=status,
            task_id=task_id,
            signal_id=signal_id,
        )

    def latest_logs(
        self,
        limit: int | None = None,
        *,
        event_type: RuntimeEventType | str | None = None,
        severity: RuntimeLogSeverity | str | None = None,
        entity_id: str | None = None,
        signal_id: str | None = None,
        task_id: str | None = None,
        action_id: str | None = None,
    ) -> tuple[RuntimeLogEvent, ...]:
        """Return detached log events, newest first, independent of the sink."""

        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
        ):
            raise ValueError("log limit must be a non-negative integer")
        if isinstance(event_type, str):
            event_type = RuntimeEventType(event_type)
        if isinstance(severity, str):
            severity = RuntimeLogSeverity(severity)
        events = (
            event
            for event in reversed(self._log_events)
            if (event_type is None or event.event_type is event_type)
            and (severity is None or event.severity is severity)
            and (entity_id is None or event.entity_id == entity_id)
            and (signal_id is None or event.signal_id == signal_id)
            and (task_id is None or event.task_id == task_id)
            and (action_id is None or event.action_id == action_id)
        )
        selected = list(events)
        if limit is not None:
            selected = selected[:limit]
        return tuple(
            RuntimeLogEvent(
                event_type=event.event_type,
                severity=event.severity,
                timestamp=event.timestamp,
                entity_id=event.entity_id,
                signal_id=event.signal_id,
                task_id=event.task_id,
                action_id=event.action_id,
                metadata=self._copy_state(event.metadata),
            )
            for event in selected
        )

    async def cancel_task(self, task_id: str) -> TaskHistoryEntry | None:
        """Cancel an executing handler Task and wait for lifecycle cleanup."""

        entry = self.get_task(task_id)
        if entry is None or entry.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            return entry
        active = self._active_task_runs.get(task_id)
        if active is None:
            return entry
        execution, completed = active
        if execution is asyncio.current_task():
            raise RuntimeError("a task cannot cancel its own handler execution")
        execution.cancel()
        await completed.wait()
        return self.get_task(task_id)

    def update_entity_state(
        self,
        entity_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Apply copied state values and record the detached change summary."""

        entity = self.get_entity(entity_id)
        if entity is None:
            return None
        before = self._copy_state(entity.state)
        updates = self._copy_state(values)
        entity.state.update(updates)
        changes = {
            key: {
                "operation": "updated" if key in before else "added",
                **({"before": before[key]} if key in before else {}),
                "after": self._copy_state({key: entity.state[key]})[key],
            }
            for key in updates
            if key not in before or before[key] != entity.state[key]
        }
        if changes:
            self._log(
                RuntimeEventType.STATE_CHANGED,
                entity_id=entity.id,
                metadata={"changes": changes, "operation": "runtime_service"},
            )
        return self._copy_state(entity.state)

    def _apply_signal_influence(
        self,
        entity_id: str,
        signal_id: str,
        influence: SignalInfluence,
    ) -> dict[str, Any] | None:
        """Coordinate a service-validated character influence and observation."""

        entity = self.get_entity(entity_id)
        if entity is None:
            return None
        result = entity._apply_signal_influence(signal_id, influence)
        candidate = result["attention_candidate"]
        self._log(
            RuntimeEventType.STATE_CHANGED,
            entity_id=entity_id,
            signal_id=signal_id,
            metadata={
                "operation": "signal_influence",
                "internal_state_changes": result["internal_state_changes"],
                "drive_activation_changes": result["drive_activation_changes"],
                "attention_candidate": (
                    candidate.to_dict() if candidate is not None else None
                ),
            },
        )
        return result

    async def emit(
        self,
        signal: Signal,
        *,
        priority: SignalPriority | int | str = SignalPriority.NORMAL,
    ) -> None:
        """Schedule a Signal and return after that Signal is dispatched."""

        self._require_accepting_work()

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
        is_handler_execution = current is not None and any(
            execution is current
            for execution, _completed in self._active_task_runs.values()
        )
        if current is not None and (
            self._dispatch_owner is current or is_handler_execution
        ):
            await self.scheduler.schedule(signal, priority)
            await self._process_through(signal)
            return

        async with self._dispatch_lock:
            self._dispatch_owner = current
            try:
                self._require_accepting_work()
                await self.scheduler.schedule(signal, priority)
                await self._process_through(signal)
            finally:
                self._dispatch_owner = None

    def _require_accepting_work(self) -> None:
        if not self._accepting_work:
            raise RuntimeNotAcceptingWorkError(
                "runtime is not accepting new work"
            )

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
        self._routing_tasks[signal.id] = []
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
                    await asyncio.create_task(
                        self._run_handler(entity, handler, signal)
                    )
        except BaseException as error:
            routing_error = error
            raise
        finally:
            routed_tasks = self._routing_tasks.pop(signal.id, [])
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
        if len(self.tasks) > self.task_history.max_size:
            del self.tasks[0]
        self.task_history.record(task)
        self._routing_tasks[signal.id].append(task)
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
        previous_action_ids = self._current_action_ids
        self._current_entity = entity
        self._current_task = task
        self._current_action_ids = set()
        execution = asyncio.current_task()
        completed = asyncio.Event()
        if execution is not None:
            self._active_task_runs[task.id] = (execution, completed)
        state_before = self._copy_entity_state(entity)
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
            self._current_action_ids = previous_action_ids
            self._active_task_runs.pop(task.id, None)
            completed.set()

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
        is_new = action.id not in self.action_history
        if self._current_action_ids is not None:
            is_new = is_new and action.id not in self._current_action_ids
        if is_new:
            self.actions.append(action)
            if len(self.actions) > self.action_history.max_size:
                del self.actions[0]
            signal_id = None
            if task is not None:
                signal_data = task.context.get("signal", {})
                if isinstance(signal_data, dict):
                    signal_id = signal_data.get("id")
            self.action_history.record(action, signal_id=signal_id)
            if self._current_action_ids is not None:
                self._current_action_ids.add(action.id)
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
            self.action_history.mark_executed(action.id)
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
        before: dict[StateCategory, dict[str, Any]],
    ) -> None:
        try:
            after = self._copy_entity_state(entity)
            for category in StateCategory:
                category_before = before[category]
                category_after = after[category]
                keys = category_before.keys() | category_after.keys()
                changes: dict[str, dict[str, Any]] = {}
                for key in keys:
                    if key not in category_before:
                        changes[key] = {
                            "operation": "added",
                            "after": category_after[key],
                        }
                    elif key not in category_after:
                        changes[key] = {
                            "operation": "removed",
                            "before": category_before[key],
                        }
                    elif category_before[key] != category_after[key]:
                        changes[key] = {
                            "operation": "updated",
                            "before": category_before[key],
                            "after": category_after[key],
                        }
                if changes:
                    metadata: dict[str, Any] = {"changes": changes}
                    if category is not StateCategory.SESSION:
                        metadata["category"] = category.value
                    self._log(
                        RuntimeEventType.STATE_CHANGED,
                        entity_id=entity.id,
                        signal_id=signal.id,
                        task_id=task.id,
                        metadata=metadata,
                    )
        except Exception:
            pass

    @classmethod
    def _copy_entity_state(
        cls, entity: Entity
    ) -> dict[StateCategory, dict[str, Any]]:
        return {
            category: cls._copy_state(entity.list_state(category=category))
            for category in StateCategory
        }

    @staticmethod
    def _copy_state(state: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(state)
        try:
            return deepcopy(data)
        except Exception:
            return data.copy()

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

        event = RuntimeLogEvent(
            event_type=event_type,
            entity_id=entity_id,
            signal_id=signal_id,
            task_id=task_id,
            action_id=action_id,
            metadata=dict(metadata or {}),
        )
        if event_type is RuntimeEventType.ERROR:
            self._recent_errors.append(event)
        self._log_events.append(event)
        self._event_broker.publish(event)
        try:
            self.log_sink.write(event)
        except Exception:
            pass
