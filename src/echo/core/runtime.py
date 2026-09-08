"""Unified asynchronous coordination for the Echo kernel."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from inspect import isawaitable
from typing import Any

from echo.core.action import Action
from echo.core.entity import Entity
from echo.core.scheduler import Scheduler, SignalPriority
from echo.core.signal import Signal
from echo.core.task import Task


class Runtime:
    """Route scheduled Signals to Entity handlers and record their work."""

    def __init__(
        self,
        entities: Iterable[Entity] = (),
        *,
        scheduler: Scheduler | None = None,
    ) -> None:
        self.scheduler = scheduler or Scheduler()
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

    def register(self, entity: Entity) -> Entity:
        if entity.id in self.entities and self.entities[entity.id] is not entity:
            raise ValueError(f"entity id is already registered: {entity.id}")
        self.entities[entity.id] = entity
        entity._runtime = self
        return entity

    def get_entity(self, entity_id: str) -> Entity | None:
        return self.entities.get(entity_id)

    async def emit(
        self,
        signal: Signal,
        *,
        priority: SignalPriority | int | str = SignalPriority.NORMAL,
    ) -> None:
        """Schedule a Signal and return after that Signal is dispatched."""

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
        for entity in self.entities.values():
            for handler in entity.handlers.resolve(signal):
                await self._run_handler(entity, handler, signal)

    async def _run_handler(self, entity: Entity, handler: Any, signal: Signal) -> None:
        task = Task(
            name=getattr(handler, "__name__", handler.__class__.__name__),
            owner=entity.id,
            context={"signal": signal.to_dict()},
        )
        self.tasks.append(task)
        entity.active_tasks[task.id] = task
        previous_entity = self._current_entity
        previous_task = self._current_task
        self._current_entity = entity
        self._current_task = task
        task.start()
        try:
            result = handler(signal)
            if isawaitable(result):
                result = await result
            self._collect_result(result, entity, task)
            task.complete(result)
        except BaseException as error:
            task.fail(error)
            raise
        finally:
            entity.active_tasks.pop(task.id, None)
            self._current_entity = previous_entity
            self._current_task = previous_task

    def record_action(self, action: Action, entity: Entity, task: Task | None) -> Action:
        if action.entity_id is None:
            action.entity_id = entity.id
        if action.task_id is None and task is not None:
            action.task_id = task.id
        if all(existing.id != action.id for existing in self.actions):
            self.actions.append(action)
        return action

    def _collect_result(self, result: Any, entity: Entity, task: Task) -> None:
        if isinstance(result, Action):
            self.record_action(result, entity, task)
        elif isinstance(result, (list, tuple)):
            for value in result:
                if isinstance(value, Action):
                    self.record_action(value, entity, task)

