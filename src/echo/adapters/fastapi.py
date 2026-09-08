"""Optional FastAPI transport for the Echo runtime service contract."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from echo import (
    ActionQuery,
    EmitSignalRequest,
    LogQuery,
    RuntimeServiceError,
    RuntimeServiceProtocol,
    RuntimeEventSubscription,
    RuntimeSubscriptionError,
    RuntimeSubscriptionRequest,
    SetStateValuesRequest,
    Signal,
    SignalQuery,
    TaskQuery,
)


class SignalBody(BaseModel):
    """HTTP representation used to inject a Signal."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    id: str | None = Field(default=None, min_length=1)
    source: str = "internal"
    timestamp: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    priority: str | int = "normal"

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("signal timestamp must be timezone-aware")
        return value

    def to_service_request(self) -> EmitSignalRequest:
        values = self.model_dump(exclude={"priority"}, exclude_none=True)
        return EmitSignalRequest(signal=Signal(**values), priority=self.priority)


class StateUpdateBody(BaseModel):
    """Allowlisted ordinary Entity state values to update."""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]


_ERROR_STATUS = {
    "invalid_request": status.HTTP_400_BAD_REQUEST,
    "not_found": status.HTTP_404_NOT_FOUND,
    "state_update_not_allowed": status.HTTP_403_FORBIDDEN,
    "task_not_cancellable": status.HTTP_409_CONFLICT,
    "signal_emission_failed": 422,
    "invalid_character_influence": 422,
    "runtime_service_error": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value.to_dict()


def _as_list(values: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [_as_dict(value) for value in values]


async def _stream_events(
    websocket: WebSocket,
    subscription: RuntimeEventSubscription,
) -> None:
    """Forward one bounded subscription while independently watching its peer."""

    async def send_events() -> None:
        try:
            async for event in subscription:
                await websocket.send_json(event.to_dict())
        except WebSocketDisconnect:
            subscription.close()

    async def watch_client() -> None:
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
        finally:
            subscription.close()

    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(send_events())
            tasks.create_task(watch_client())
    finally:
        subscription.close()


def create_app(service: RuntimeServiceProtocol) -> FastAPI:
    """Create an HTTP adapter around an existing runtime service."""

    if not isinstance(service, RuntimeServiceProtocol):
        raise TypeError("service must implement RuntimeServiceProtocol")

    app = FastAPI(title="Echo Runtime API", version="0.4.2")

    @app.exception_handler(RuntimeServiceError)
    async def handle_runtime_service_error(
        request: Request,
        error: RuntimeServiceError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=_ERROR_STATUS.get(
                error.code,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            ),
            content={"error": error.to_dict()},
        )

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        service.get_runtime_status()
        return {"status": "ok"}

    @app.get("/runtime/status", tags=["runtime"])
    async def runtime_status() -> dict[str, Any]:
        return _as_dict(service.get_runtime_status())

    @app.get("/entities", tags=["entities"])
    async def list_entities() -> list[dict[str, Any]]:
        return _as_list(service.get_entities())

    @app.get("/entities/{entity_id}", tags=["entities"])
    async def inspect_entity(entity_id: str) -> dict[str, Any]:
        return _as_dict(service.inspect_entity(entity_id))

    @app.get("/signals", tags=["signals"])
    async def list_signals(
        limit: int | None = Query(default=20, ge=0),
        signal_type: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        return _as_list(
            service.get_recent_signals(
                SignalQuery(limit=limit, signal_type=signal_type, source=source)
            )
        )

    @app.post(
        "/signals",
        tags=["signals"],
        status_code=status.HTTP_201_CREATED,
    )
    async def emit_signal(body: SignalBody) -> dict[str, Any]:
        return _as_dict(await service.emit_signal(body.to_service_request()))

    @app.get("/signals/{signal_id}", tags=["signals"])
    async def inspect_signal(signal_id: str) -> dict[str, Any]:
        return _as_dict(service.inspect_signal(signal_id))

    @app.get("/tasks", tags=["tasks"])
    async def list_tasks(
        limit: int | None = Query(default=20, ge=0),
        task_status: str | None = Query(default=None, alias="status"),
    ) -> list[dict[str, Any]]:
        return _as_list(
            service.get_tasks(TaskQuery(limit=limit, status=task_status))
        )

    @app.get("/tasks/{task_id}", tags=["tasks"])
    async def inspect_task(task_id: str) -> dict[str, Any]:
        return _as_dict(service.inspect_task(task_id))

    @app.post("/tasks/{task_id}/cancel", tags=["tasks"])
    async def cancel_task(task_id: str) -> dict[str, Any]:
        return _as_dict(await service.cancel_task(task_id))

    @app.get("/actions", tags=["actions"])
    async def list_actions(
        limit: int | None = Query(default=20, ge=0),
        action_type: str | None = Query(default=None, alias="type"),
        action_status: str | None = Query(default=None, alias="status"),
        task_id: str | None = None,
        signal_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return _as_list(
            service.get_actions(
                ActionQuery(
                    limit=limit,
                    action_type=action_type,
                    status=action_status,
                    task_id=task_id,
                    signal_id=signal_id,
                )
            )
        )

    @app.get("/actions/{action_id}", tags=["actions"])
    async def inspect_action(action_id: str) -> dict[str, Any]:
        return _as_dict(service.inspect_action(action_id))

    @app.get("/entities/{entity_id}/state", tags=["state"])
    async def get_entity_state(entity_id: str) -> dict[str, Any]:
        return _as_dict(service.get_entity_state(entity_id))

    @app.patch("/entities/{entity_id}/state", tags=["state"])
    async def set_entity_state(
        entity_id: str,
        body: StateUpdateBody,
    ) -> dict[str, Any]:
        return _as_dict(
            service.set_allowed_state_values(
                SetStateValuesRequest(entity_id=entity_id, values=body.values)
            )
        )

    @app.get("/logs", tags=["logs"])
    async def get_logs(
        limit: int | None = Query(default=100, ge=0),
        event_type: str | None = None,
        entity_id: str | None = None,
        signal_id: str | None = None,
        task_id: str | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        return _as_dict(
            service.get_logs(
                LogQuery(
                    limit=limit,
                    event_type=event_type,
                    entity_id=entity_id,
                    signal_id=signal_id,
                    task_id=task_id,
                    action_id=action_id,
                )
            )
        )

    @app.websocket("/events")
    async def runtime_events(
        websocket: WebSocket,
        categories: list[str] = Query(default=["logs"], alias="category"),
        max_queue_size: int = Query(default=100, ge=1),
        backpressure: str = Query(default="drop_oldest"),
    ) -> None:
        try:
            subscription = service.subscribe_events(
                RuntimeSubscriptionRequest(
                    categories=categories,
                    max_queue_size=max_queue_size,
                    backpressure=backpressure,
                )
            )
        except (RuntimeServiceError, RuntimeSubscriptionError) as error:
            await websocket.close(code=1008, reason=error.message)
            return

        try:
            await websocket.accept()
            await _stream_events(websocket, subscription)
        finally:
            subscription.close()

    return app
