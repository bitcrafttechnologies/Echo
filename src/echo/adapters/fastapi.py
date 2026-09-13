"""Optional FastAPI transport for the Echo runtime service contract."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
from typing import Any, Literal

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from echo import (
    ActionQuery,
    EmitSignalRequest,
    InferenceRequest,
    LogQuery,
    RestartRequest,
    RuntimeServiceError,
    RuntimeServiceProtocol,
    RuntimeEventSubscription,
    RuntimeSubscriptionError,
    RuntimeSubscriptionRequest,
    SetStateValuesRequest,
    Signal,
    SignalQuery,
    StartRecordingRequest,
    StartReplayRequest,
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


class MedullaDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "decline", "block", "reconsider", "unblock"]
    reason: str | None = Field(default=None, min_length=1)


class MedullaAuthorizationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization: dict[str, Any] = Field(default_factory=dict)


class InferenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)
    instructions: str | None = Field(default=None, min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = Field(default=None, min_length=1)

    def to_request(self) -> InferenceRequest:
        values = self.model_dump(exclude_none=True)
        return InferenceRequest(**values)


class ProviderModeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str


class ConfigurationUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]


class RestartBody(BaseModel):
    """A deliberately confirmed, state-preserving development restart."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)
    confirmation: Literal["RESTART"]
    preserve_state: Literal[True]

    def to_request(self) -> RestartRequest:
        return RestartRequest(
            reason=self.reason,
            confirmation=self.confirmation,
            preserve_state=self.preserve_state,
        )


class RecordingStartBody(BaseModel):
    """Start one explicit local Signal recording session."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    durable: bool = True

    def to_request(self) -> StartRecordingRequest:
        return StartRecordingRequest(
            path=self.path,
            metadata=self.metadata,
            durable=self.durable,
        )


class ReplayStartBody(BaseModel):
    """Load and start a safe Signal replay."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    mode: Literal["signal", "sequential", "step"]
    signal_id: str | None = Field(default=None, min_length=1)
    timing: Literal["realtime", "accelerated", "immediate", "manual_step"] = (
        "immediate"
    )
    multiplier: float | None = None
    safety_policy: Literal["record_only"] = "record_only"

    def to_request(self) -> StartReplayRequest:
        return StartReplayRequest(
            path=self.path,
            mode=self.mode,
            signal_id=self.signal_id,
            timing=self.timing,
            multiplier=self.multiplier,
            safety_policy=self.safety_policy,
        )


_ERROR_STATUS = {
    "invalid_request": status.HTTP_400_BAD_REQUEST,
    "not_found": status.HTTP_404_NOT_FOUND,
    "state_update_not_allowed": status.HTTP_403_FORBIDDEN,
    "task_not_cancellable": status.HTTP_409_CONFLICT,
    "signal_emission_failed": 422,
    "invalid_character_influence": 422,
    "inference_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "configuration_reload_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "invalid_configuration": 422,
    "restart_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "restart_conflict": status.HTTP_409_CONFLICT,
    "recording_conflict": status.HTTP_409_CONFLICT,
    "recording_failed": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "replay_conflict": status.HTTP_409_CONFLICT,
    "replay_failed": 422,
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
        finally:
            close = getattr(websocket, "close", None)
            if subscription.closed and callable(close):
                with suppress(RuntimeError, WebSocketDisconnect):
                    await close(
                        code=1012,
                        reason="Runtime generation restarted",
                    )

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


def create_app(service: RuntimeServiceProtocol, *, lifespan: Any | None = None) -> FastAPI:
    """Create an HTTP adapter around an existing runtime service."""

    if not isinstance(service, RuntimeServiceProtocol):
        raise TypeError("service must implement RuntimeServiceProtocol")

    app = FastAPI(title="Echo Runtime API", version="0.9.5", lifespan=lifespan)

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

    @app.post(
        "/runtime/recording",
        tags=["runtime"],
        status_code=status.HTTP_201_CREATED,
    )
    async def start_runtime_recording(
        body: RecordingStartBody,
    ) -> dict[str, Any]:
        return _as_dict(await service.start_recording(body.to_request()))

    @app.delete("/runtime/recording", tags=["runtime"])
    async def stop_runtime_recording() -> dict[str, Any]:
        return _as_dict(await service.stop_recording())

    @app.get("/runtime/recording", tags=["runtime"])
    async def runtime_recording_status() -> dict[str, Any]:
        return _as_dict(service.get_recording_status())

    @app.post(
        "/runtime/replays",
        tags=["runtime"],
        status_code=status.HTTP_201_CREATED,
    )
    async def start_runtime_replay(body: ReplayStartBody) -> dict[str, Any]:
        return _as_dict(await service.start_replay(body.to_request()))

    @app.post("/runtime/replays/{replay_id}/step", tags=["runtime"])
    async def advance_runtime_replay(replay_id: str) -> dict[str, Any]:
        return _as_dict(await service.replay_next_step(replay_id))

    @app.delete("/runtime/replays/{replay_id}", tags=["runtime"])
    async def cancel_runtime_replay(replay_id: str) -> dict[str, Any]:
        return _as_dict(await service.cancel_replay(replay_id))

    @app.get("/runtime/replays/{replay_id}", tags=["runtime"])
    async def runtime_replay_status(replay_id: str) -> dict[str, Any]:
        return _as_dict(service.get_replay_status(replay_id))

    @app.post(
        "/runtime/restart",
        tags=["runtime"],
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def request_runtime_restart(body: RestartBody) -> dict[str, Any]:
        return _as_dict(await service.request_restart(body.to_request()))

    @app.get("/runtime/restart/{operation_id}", tags=["runtime"])
    async def inspect_runtime_restart(operation_id: str) -> dict[str, Any]:
        return _as_dict(service.get_restart_operation(operation_id))

    @app.post("/configuration/reload", tags=["configuration"])
    async def reload_configuration() -> dict[str, Any]:
        return _as_dict(service.reload_configuration())

    @app.get("/configuration", tags=["configuration"])
    async def inspect_configuration() -> dict[str, Any]:
        return _as_dict(service.inspect_configuration())

    @app.patch("/configuration", tags=["configuration"])
    async def update_configuration(
        body: ConfigurationUpdateBody,
    ) -> dict[str, Any]:
        return _as_dict(service.update_configuration(body.values))

    @app.get("/providers", tags=["providers"])
    async def inspect_providers() -> dict[str, Any]:
        return _as_dict(await service.inspect_providers())

    @app.patch("/providers/mode", tags=["providers"])
    async def set_provider_mode(body: ProviderModeBody) -> dict[str, Any]:
        return _as_dict(await service.set_provider_mode(body.mode))

    @app.get("/medulla/nodes", tags=["medulla"])
    async def list_medulla_nodes() -> list[dict[str, Any]]:
        return list(service.get_medulla_nodes())

    @app.get("/medulla/nodes/{node_id}", tags=["medulla"])
    async def inspect_medulla_node(node_id: str) -> dict[str, Any]:
        return service.inspect_medulla_node(node_id)

    @app.post("/medulla/nodes/{node_id}/decision", tags=["medulla"])
    async def decide_medulla_node(node_id: str, body: MedullaDecisionBody) -> dict[str, Any]:
        return await service.decide_medulla_node(node_id, body.decision, body.reason)

    @app.post("/medulla/nodes/{node_id}/authorization", tags=["medulla"])
    async def authorize_medulla_node(node_id: str, body: MedullaAuthorizationBody) -> dict[str, Any]:
        return await service.authorize_medulla_node(node_id, body.authorization)

    @app.post("/inference", tags=["providers"])
    async def infer(body: InferenceBody) -> dict[str, Any]:
        return _as_dict(await service.infer(body.to_request()))

    @app.get("/entities", tags=["entities"])
    async def list_entities() -> list[dict[str, Any]]:
        return _as_list(service.get_entities())

    @app.get("/entities/{entity_id}", tags=["entities"])
    async def inspect_entity(entity_id: str) -> dict[str, Any]:
        return _as_dict(service.inspect_entity(entity_id))

    @app.get("/entities/{entity_id}/relationships", tags=["entities"])
    async def list_relationships(entity_id: str) -> list[dict[str, Any]]:
        return _as_list(service.get_relationships(entity_id))

    @app.get(
        "/entities/{entity_id}/relationships/{subject_id}", tags=["entities"]
    )
    async def inspect_relationship(
        entity_id: str, subject_id: str
    ) -> dict[str, Any]:
        return _as_dict(service.inspect_relationship(entity_id, subject_id))

    @app.get("/entities/{entity_id}/memories", tags=["memory"])
    async def list_durable_memories(
        entity_id: str,
        memory_status: str | None = Query(default=None, alias="status"),
        memory_type: str | None = Query(default=None, alias="type"),
    ) -> dict[str, Any]:
        return _as_dict(
            service.get_durable_memories(
                entity_id, status=memory_status, memory_type=memory_type
            )
        )

    @app.get("/entities/{entity_id}/memories/{memory_id}", tags=["memory"])
    async def inspect_durable_memory(
        entity_id: str, memory_id: str
    ) -> dict[str, Any]:
        return _as_dict(service.inspect_durable_memory(entity_id, memory_id))

    @app.post("/entities/{entity_id}/memories/{memory_id}/archive", tags=["memory"])
    async def archive_durable_memory(
        entity_id: str, memory_id: str
    ) -> dict[str, Any]:
        return _as_dict(service.archive_durable_memory(entity_id, memory_id))

    @app.delete(
        "/entities/{entity_id}/memories/{memory_id}",
        tags=["memory"],
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_durable_memory(entity_id: str, memory_id: str) -> None:
        service.delete_durable_memory(entity_id, memory_id)

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
        severity: str | None = None,
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
                    severity=severity,
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
