"""Structured developer commands resolved through the runtime service API."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
import json
import shlex
from typing import Any

from echo.core.inspection import json_safe
from echo.core.scheduler import SignalPriority
from echo.core.signal import Signal
from echo.runtime_service import (
    ActionQuery,
    EmitSignalRequest,
    LogQuery,
    RuntimeServiceError,
    RuntimeServiceProtocol,
    SetStateValuesRequest,
    SignalQuery,
    TaskQuery,
)


class DeveloperCommandError(Exception):
    """Base class for parser, validation, and command execution failures."""

    code = "developer_command_error"

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = _copy(dict(details or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": _copy(self.details),
        }


class CommandParseError(DeveloperCommandError):
    code = "command_parse_error"


class CommandValidationError(DeveloperCommandError):
    code = "command_validation_error"


class UnknownCommandError(DeveloperCommandError):
    code = "unknown_command"


class CommandExecutionError(DeveloperCommandError):
    code = "command_execution_error"


class CommandName(StrEnum):
    RUNTIME_STATUS = "runtime.status"
    ENTITY_INSPECT = "entity.inspect"
    SIGNAL_LIST = "signal.list"
    SIGNAL_INSPECT = "signal.inspect"
    SIGNAL_INJECT = "signal.inject"
    TASK_LIST = "task.list"
    TASK_INSPECT = "task.inspect"
    TASK_CANCEL = "task.cancel"
    ACTION_LIST = "action.list"
    STATE_GET = "state.get"
    STATE_SET = "state.set"
    LOGS = "logs"
    PROVIDER_STATUS = "provider.status"
    PROVIDER_MODE = "provider.mode"
    CONFIG_INSPECT = "config.inspect"
    CONFIG_SET = "config.set"
    CONFIG_RELOAD = "config.reload"


def _copy(value: Any) -> Any:
    try:
        return deepcopy(value)
    except Exception:
        return json_safe(value)


def _require_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise CommandValidationError(f"{name} must be a non-empty string")


@dataclass(slots=True, kw_only=True, frozen=True)
class RuntimeStatusCommand:
    name: CommandName = field(default=CommandName.RUNTIME_STATUS, init=False)


@dataclass(slots=True, kw_only=True, frozen=True)
class EntityInspectCommand:
    entity_id: str
    name: CommandName = field(default=CommandName.ENTITY_INSPECT, init=False)

    def __post_init__(self) -> None:
        _require_identifier(self.entity_id, "entity_id")


@dataclass(slots=True, kw_only=True, frozen=True)
class SignalListCommand:
    query: SignalQuery = field(default_factory=SignalQuery)
    name: CommandName = field(default=CommandName.SIGNAL_LIST, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.query, SignalQuery):
            raise CommandValidationError("query must be a SignalQuery")


@dataclass(slots=True, kw_only=True, frozen=True)
class SignalInspectCommand:
    signal_id: str
    name: CommandName = field(default=CommandName.SIGNAL_INSPECT, init=False)

    def __post_init__(self) -> None:
        _require_identifier(self.signal_id, "signal_id")


@dataclass(slots=True, kw_only=True, frozen=True)
class SignalInjectCommand:
    signal_type: str
    source: str = "developer"
    payload: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    priority: SignalPriority | int | str = SignalPriority.NORMAL
    name: CommandName = field(default=CommandName.SIGNAL_INJECT, init=False)

    def __post_init__(self) -> None:
        _require_identifier(self.signal_type, "signal_type")
        _require_identifier(self.source, "source")
        if not isinstance(self.payload, Mapping):
            raise CommandValidationError("payload must be a mapping")
        if not isinstance(self.metadata, Mapping):
            raise CommandValidationError("metadata must be a mapping")
        object.__setattr__(self, "payload", _copy(dict(self.payload)))
        object.__setattr__(self, "metadata", _copy(dict(self.metadata)))


@dataclass(slots=True, kw_only=True, frozen=True)
class TaskListCommand:
    query: TaskQuery = field(default_factory=TaskQuery)
    name: CommandName = field(default=CommandName.TASK_LIST, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.query, TaskQuery):
            raise CommandValidationError("query must be a TaskQuery")


@dataclass(slots=True, kw_only=True, frozen=True)
class TaskInspectCommand:
    task_id: str
    name: CommandName = field(default=CommandName.TASK_INSPECT, init=False)

    def __post_init__(self) -> None:
        _require_identifier(self.task_id, "task_id")


@dataclass(slots=True, kw_only=True, frozen=True)
class TaskCancelCommand:
    task_id: str
    name: CommandName = field(default=CommandName.TASK_CANCEL, init=False)

    def __post_init__(self) -> None:
        _require_identifier(self.task_id, "task_id")


@dataclass(slots=True, kw_only=True, frozen=True)
class ActionListCommand:
    query: ActionQuery = field(default_factory=ActionQuery)
    name: CommandName = field(default=CommandName.ACTION_LIST, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.query, ActionQuery):
            raise CommandValidationError("query must be an ActionQuery")


@dataclass(slots=True, kw_only=True, frozen=True)
class StateGetCommand:
    entity_id: str
    name: CommandName = field(default=CommandName.STATE_GET, init=False)

    def __post_init__(self) -> None:
        _require_identifier(self.entity_id, "entity_id")


@dataclass(slots=True, kw_only=True, frozen=True)
class StateSetCommand:
    entity_id: str
    values: Mapping[str, Any]
    name: CommandName = field(default=CommandName.STATE_SET, init=False)

    def __post_init__(self) -> None:
        _require_identifier(self.entity_id, "entity_id")
        if not isinstance(self.values, Mapping) or not self.values:
            raise CommandValidationError("values must be a non-empty mapping")
        object.__setattr__(self, "values", _copy(dict(self.values)))


@dataclass(slots=True, kw_only=True, frozen=True)
class LogsCommand:
    query: LogQuery = field(default_factory=LogQuery)
    name: CommandName = field(default=CommandName.LOGS, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.query, LogQuery):
            raise CommandValidationError("query must be a LogQuery")


@dataclass(slots=True, kw_only=True, frozen=True)
class ProviderStatusCommand:
    name: CommandName = field(default=CommandName.PROVIDER_STATUS, init=False)


@dataclass(slots=True, kw_only=True, frozen=True)
class ProviderModeCommand:
    mode: str
    name: CommandName = field(default=CommandName.PROVIDER_MODE, init=False)

    def __post_init__(self) -> None:
        if self.mode not in {"auto", "remote", "lan", "offline"}:
            raise CommandValidationError(
                "provider mode must be auto, remote, lan, or offline"
            )


@dataclass(slots=True, kw_only=True, frozen=True)
class ConfigReloadCommand:
    name: CommandName = field(default=CommandName.CONFIG_RELOAD, init=False)


@dataclass(slots=True, kw_only=True, frozen=True)
class ConfigInspectCommand:
    name: CommandName = field(default=CommandName.CONFIG_INSPECT, init=False)


@dataclass(slots=True, kw_only=True, frozen=True)
class ConfigSetCommand:
    path: str
    value: Any
    name: CommandName = field(default=CommandName.CONFIG_SET, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.strip():
            raise CommandValidationError("configuration path must not be empty")


DeveloperCommand = (
    RuntimeStatusCommand
    | EntityInspectCommand
    | SignalListCommand
    | SignalInspectCommand
    | SignalInjectCommand
    | TaskListCommand
    | TaskInspectCommand
    | TaskCancelCommand
    | ActionListCommand
    | StateGetCommand
    | StateSetCommand
    | LogsCommand
    | ProviderStatusCommand
    | ProviderModeCommand
    | ConfigInspectCommand
    | ConfigSetCommand
    | ConfigReloadCommand
)


@dataclass(slots=True, kw_only=True, frozen=True)
class CommandResult:
    command: CommandName
    data: Any

    def to_dict(self) -> dict[str, Any]:
        return {"command": self.command.value, "data": _copy(self.data)}


class DeveloperCommandDispatcher:
    """Execute explicit command schemas exclusively through RuntimeService."""

    def __init__(self, service: RuntimeServiceProtocol) -> None:
        if not isinstance(service, RuntimeServiceProtocol):
            raise CommandValidationError(
                "service must implement RuntimeServiceProtocol"
            )
        self._service = service

    async def execute(self, command: DeveloperCommand) -> CommandResult:
        if not isinstance(
            command,
            (
                RuntimeStatusCommand,
                EntityInspectCommand,
                SignalListCommand,
                SignalInspectCommand,
                SignalInjectCommand,
                TaskListCommand,
                TaskInspectCommand,
                TaskCancelCommand,
                ActionListCommand,
                StateGetCommand,
                StateSetCommand,
                LogsCommand,
                ProviderStatusCommand,
                ProviderModeCommand,
                ConfigInspectCommand,
                ConfigSetCommand,
                ConfigReloadCommand,
            ),
        ):
            raise UnknownCommandError(
                "unsupported command schema",
                details={"type": type(command).__name__},
            )

        try:
            value = await self._resolve(command)
        except RuntimeServiceError as error:
            raise CommandExecutionError(
                f"{command.name.value} failed",
                details={"service_error": error.to_dict()},
            ) from error
        return CommandResult(command=command.name, data=_serialize(value))

    async def execute_text(self, text: str) -> CommandResult:
        return await self.execute(parse_developer_command(text))

    async def _resolve(self, command: DeveloperCommand) -> Any:
        if isinstance(command, RuntimeStatusCommand):
            return self._service.get_runtime_status()
        if isinstance(command, EntityInspectCommand):
            return self._service.inspect_entity(command.entity_id)
        if isinstance(command, SignalListCommand):
            return self._service.get_recent_signals(command.query)
        if isinstance(command, SignalInspectCommand):
            return self._service.inspect_signal(command.signal_id)
        if isinstance(command, SignalInjectCommand):
            signal = Signal(
                type=command.signal_type,
                source=command.source,
                payload=dict(command.payload),
                metadata=dict(command.metadata),
            )
            return await self._service.emit_signal(
                EmitSignalRequest(signal=signal, priority=command.priority)
            )
        if isinstance(command, TaskListCommand):
            return self._service.get_tasks(command.query)
        if isinstance(command, TaskInspectCommand):
            return self._service.inspect_task(command.task_id)
        if isinstance(command, TaskCancelCommand):
            return await self._service.cancel_task(command.task_id)
        if isinstance(command, ActionListCommand):
            return self._service.get_actions(command.query)
        if isinstance(command, StateGetCommand):
            return self._service.get_entity_state(command.entity_id)
        if isinstance(command, StateSetCommand):
            return self._service.set_allowed_state_values(
                SetStateValuesRequest(
                    entity_id=command.entity_id,
                    values=command.values,
                )
            )
        if isinstance(command, LogsCommand):
            return self._service.get_logs(command.query)
        if isinstance(command, ProviderStatusCommand):
            return await self._service.inspect_providers()
        if isinstance(command, ProviderModeCommand):
            return await self._service.set_provider_mode(command.mode)
        if isinstance(command, ConfigInspectCommand):
            return self._service.inspect_configuration()
        if isinstance(command, ConfigSetCommand):
            return self._service.update_configuration(
                {command.path: command.value}
            )
        if isinstance(command, ConfigReloadCommand):
            return self._service.reload_configuration()
        raise UnknownCommandError("unsupported command schema")


def _serialize(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return json_safe(to_dict())
    return json_safe(_copy(value))


def parse_developer_command(text: str) -> DeveloperCommand:
    """Parse the deliberately small Phase 3B text command grammar."""

    if not isinstance(text, str) or not text.strip():
        raise CommandParseError("command text must not be empty")
    try:
        tokens = shlex.split(text)
    except ValueError as error:
        raise CommandParseError(f"malformed command text: {error}") from error

    if tokens == ["runtime", "status"]:
        return RuntimeStatusCommand()
    if tokens == ["signal", "list"]:
        return SignalListCommand()
    if tokens == ["task", "list"]:
        return TaskListCommand()
    if tokens == ["action", "list"]:
        return ActionListCommand()
    if tokens == ["logs"]:
        return LogsCommand()
    if tokens == ["provider", "status"]:
        return ProviderStatusCommand()
    if len(tokens) == 3 and tokens[:2] == ["provider", "mode"]:
        return ProviderModeCommand(mode=tokens[2])
    if tokens == ["config", "reload"]:
        return ConfigReloadCommand()
    if tokens == ["config", "inspect"]:
        return ConfigInspectCommand()
    if len(tokens) == 4 and tokens[:2] == ["config", "set"]:
        try:
            value = json.loads(tokens[3])
        except json.JSONDecodeError as error:
            raise CommandParseError("configuration value must be valid JSON") from error
        return ConfigSetCommand(path=tokens[2], value=value)
    if len(tokens) == 3 and tokens[:2] == ["entity", "inspect"]:
        return EntityInspectCommand(entity_id=tokens[2])
    if len(tokens) == 3 and tokens[:2] == ["signal", "inspect"]:
        return SignalInspectCommand(signal_id=tokens[2])
    if len(tokens) in {3, 4} and tokens[:2] == ["signal", "inject"]:
        payload = _parse_json_object(tokens[3], "payload") if len(tokens) == 4 else {}
        return SignalInjectCommand(signal_type=tokens[2], payload=payload)
    if len(tokens) == 3 and tokens[:2] == ["task", "inspect"]:
        return TaskInspectCommand(task_id=tokens[2])
    if len(tokens) == 3 and tokens[:2] == ["task", "cancel"]:
        return TaskCancelCommand(task_id=tokens[2])
    if len(tokens) == 3 and tokens[:2] == ["state", "get"]:
        return StateGetCommand(entity_id=tokens[2])
    if len(tokens) == 4 and tokens[:2] == ["state", "set"]:
        return StateSetCommand(
            entity_id=tokens[2],
            values=_parse_json_object(tokens[3], "values"),
        )

    attempted = " ".join(tokens[:2]) if len(tokens) > 1 else tokens[0]
    raise UnknownCommandError(
        f"unknown or malformed command: {attempted}",
        details={"tokens": tokens},
    )


def _parse_json_object(value: str, name: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise CommandParseError(f"{name} must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise CommandParseError(f"{name} must be a JSON object")
    return parsed
