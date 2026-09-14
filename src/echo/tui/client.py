"""Management-plane clients used by the TUI; neither exposes Runtime internals."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from typing import Any, Mapping, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from echo.developer_commands import DeveloperCommandDispatcher
from echo.runtime_service import ActionQuery, EmitSignalRequest, LogQuery, RuntimeServiceProtocol, SignalQuery, TaskQuery
from echo.core.signal import Signal


class ConsoleConnectionError(RuntimeError):
    pass


@runtime_checkable
class EchoConsoleClient(Protocol):
    async def snapshot(self, surface: str, *, entity_id: str | None = None, filters: Mapping[str, str] | None = None) -> dict[str, Any]: ...
    async def execute(self, command: str) -> dict[str, Any]: ...
    async def chat(self, text: str, *, entity_id: str | None = None) -> dict[str, Any]: ...
    async def decide_medulla_node(self, node_id: str, decision: str, *, reason: str | None = None) -> dict[str, Any]: ...
    async def authorize_medulla_node(self, node_id: str, authorization: Mapping[str, Any]) -> dict[str, Any]: ...


def _dict(value: Any) -> dict[str, Any]:
    return value.to_dict() if hasattr(value, "to_dict") else dict(value)


@dataclass(slots=True)
class LocalEchoClient:
    """In-process client for embedded/offline deployments."""

    service: RuntimeServiceProtocol
    _commands: DeveloperCommandDispatcher = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._commands = DeveloperCommandDispatcher(self.service)

    async def snapshot(self, surface: str, *, entity_id: str | None = None, filters: Mapping[str, str] | None = None) -> dict[str, Any]:
        filters = filters or {}
        status = _dict(self.service.get_runtime_status())
        entities = [_dict(value) for value in self.service.get_entities()]
        data: dict[str, Any] = {"runtime": status, "entities": entities}
        if surface == "overview":
            data.update(
                signals=[_dict(value) for value in self.service.get_recent_signals(SignalQuery(limit=10))],
                tasks=[_dict(value) for value in self.service.get_tasks(TaskQuery(limit=20))],
                logs=_dict(self.service.get_logs(LogQuery(limit=12)))["events"],
            )
        elif surface == "signals":
            data["signals"] = [_dict(value) for value in self.service.get_recent_signals(SignalQuery(limit=100, signal_type=filters.get("type"), source=filters.get("source")))]
            data["actions"] = [_dict(value) for value in self.service.get_actions(ActionQuery(limit=200))]
        elif surface == "tasks":
            data["tasks"] = [_dict(value) for value in self.service.get_tasks(TaskQuery(limit=200))]
        elif surface == "entity":
            selected = entity_id or (entities[0]["id"] if entities else None)
            data["selected_entity"] = _dict(self.service.inspect_entity(selected)) if selected else None
            data["relationships"] = [_dict(value) for value in self.service.get_relationships(selected)] if selected else []
        elif surface == "logs":
            data["logs"] = _dict(self.service.get_logs(LogQuery(limit=250, severity=filters.get("severity"), event_type=filters.get("event_type"))))["events"]
        elif surface == "providers":
            data["providers"] = _dict(await self.service.inspect_providers())
        elif surface == "configuration":
            data["configuration"] = _dict(self.service.inspect_configuration())
        elif surface == "medulla":
            data["medulla_nodes"] = list(self.service.get_medulla_nodes())
        elif surface == "chat":
            data["signals"] = [_dict(value) for value in self.service.get_recent_signals(SignalQuery(limit=100, signal_type="UserMessage"))]
            data["actions"] = [_dict(value) for value in self.service.get_actions(ActionQuery(limit=200))]
        data["filters"] = dict(filters)
        return data

    async def execute(self, command: str) -> dict[str, Any]:
        return (await self._commands.execute_text(command)).to_dict()

    async def chat(self, text: str, *, entity_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": text}
        if entity_id:
            payload["entity_id"] = entity_id
        value = await self.service.emit_signal(
            EmitSignalRequest(signal=Signal(type="UserMessage", source="console", payload=payload))
        )
        return _dict(value)

    async def decide_medulla_node(self, node_id: str, decision: str, *, reason: str | None = None) -> dict[str, Any]:
        return await self.service.decide_medulla_node(node_id, decision, reason)

    async def authorize_medulla_node(self, node_id: str, authorization: Mapping[str, Any]) -> dict[str, Any]:
        return await self.service.authorize_medulla_node(node_id, authorization)


class HttpEchoClient:
    """Standard-library client for an Echo HTTP management endpoint."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        timeout_seconds: float = 3.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def _request(self, path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
        return await asyncio.to_thread(self._request_sync, path, method, body)

    def _request_sync(self, path: str, method: str, body: dict[str, Any] | None) -> Any:
        encoded = json.dumps(body).encode() if body is not None else None
        request = Request(
            f"{self.base_url}{path}", data=encoded, method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode())
        except HTTPError as error:
            try:
                failure = json.loads(error.read().decode()).get("error", {})
                detail = failure.get("message")
                issues = failure.get("details", {}).get("issues", [])
                if issues:
                    detail = "\n".join(
                        [detail or "Configuration validation failed"]
                        + [
                            f"{issue.get('path', '$')}: {issue.get('message', 'invalid value')}"
                            for issue in issues
                        ]
                    )
            except Exception:
                detail = None
            raise ConsoleConnectionError(detail or f"Echo API returned HTTP {error.code}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise ConsoleConnectionError(f"Echo is unavailable at {self.base_url}: {error}") from error

    async def snapshot(self, surface: str, *, entity_id: str | None = None, filters: Mapping[str, str] | None = None) -> dict[str, Any]:
        filters = filters or {}
        runtime, entities = await asyncio.gather(self._request("/runtime/status"), self._request("/entities"))
        data: dict[str, Any] = {"runtime": runtime, "entities": entities}
        if surface == "overview":
            signals, tasks, logs = await asyncio.gather(self._request("/signals?limit=10"), self._request("/tasks?limit=20"), self._request("/logs?limit=12"))
            data.update(signals=signals, tasks=tasks, logs=logs["events"])
        elif surface == "signals":
            query = urlencode({"limit": 100, **({"signal_type": filters["type"]} if filters.get("type") else {}), **({"source": filters["source"]} if filters.get("source") else {})})
            data["signals"], data["actions"] = await asyncio.gather(self._request(f"/signals?{query}"), self._request("/actions?limit=200"))
        elif surface == "tasks":
            data["tasks"] = await self._request("/tasks?limit=200")
        elif surface == "entity":
            selected = entity_id or (entities[0]["id"] if entities else None)
            if selected:
                encoded = quote(selected, safe="")
                data["selected_entity"], data["relationships"] = await asyncio.gather(self._request(f"/entities/{encoded}"), self._request(f"/entities/{encoded}/relationships"))
            else:
                data.update(selected_entity=None, relationships=[])
        elif surface == "logs":
            query = urlencode({"limit": 250, **({"severity": filters["severity"]} if filters.get("severity") else {}), **({"event_type": filters["event_type"]} if filters.get("event_type") else {})})
            data["logs"] = (await self._request(f"/logs?{query}"))["events"]
        elif surface == "providers":
            data["providers"] = await self._request("/providers")
        elif surface == "configuration":
            data["configuration"] = await self._request("/configuration")
        elif surface == "medulla":
            data["medulla_nodes"] = await self._request("/medulla/nodes")
        elif surface == "chat":
            data["signals"], data["actions"] = await asyncio.gather(self._request("/signals?limit=100&signal_type=UserMessage"), self._request("/actions?limit=200"))
        data["filters"] = dict(filters)
        return data

    async def execute(self, command: str) -> dict[str, Any]:
        # Parse locally to preserve the one safe command grammar, then translate to public HTTP operations.
        from echo.developer_commands import (
            ActionListCommand, EntityInspectCommand, LogsCommand, RuntimeStatusCommand,
            SignalInjectCommand, SignalInspectCommand, SignalListCommand, StateGetCommand,
            StateSetCommand, TaskCancelCommand, TaskInspectCommand, TaskListCommand,
            ProviderModeCommand, ProviderStatusCommand,
            ConfigInspectCommand, ConfigReloadCommand, ConfigSetCommand,
            RecordingStartCommand, RecordingStatusCommand, RecordingStopCommand,
            ReplayCancelCommand, ReplayNextCommand, ReplayStartCommand,
            ReplayStatusCommand,
            parse_developer_command,
        )
        parsed = parse_developer_command(command)
        if isinstance(parsed, RuntimeStatusCommand): path, method, body = "/runtime/status", "GET", None
        elif isinstance(parsed, RecordingStartCommand):
            path, method, body = "/runtime/recording", "POST", {"path": parsed.path, "metadata": dict(parsed.metadata)}
        elif isinstance(parsed, RecordingStopCommand): path, method, body = "/runtime/recording", "DELETE", None
        elif isinstance(parsed, RecordingStatusCommand): path, method, body = "/runtime/recording", "GET", None
        elif isinstance(parsed, ReplayStartCommand):
            path, method, body = "/runtime/replays", "POST", {"path": parsed.path, "mode": parsed.mode.value, "signal_id": parsed.signal_id, "timing": parsed.timing.value, "multiplier": parsed.multiplier, "safety_policy": parsed.safety_policy.value}
        elif isinstance(parsed, ReplayNextCommand): path, method, body = f"/runtime/replays/{quote(parsed.replay_id, safe='')}/step", "POST", None
        elif isinstance(parsed, ReplayCancelCommand): path, method, body = f"/runtime/replays/{quote(parsed.replay_id, safe='')}", "DELETE", None
        elif isinstance(parsed, ReplayStatusCommand): path, method, body = f"/runtime/replays/{quote(parsed.replay_id, safe='')}", "GET", None
        elif isinstance(parsed, EntityInspectCommand): path, method, body = f"/entities/{quote(parsed.entity_id, safe='')}", "GET", None
        elif isinstance(parsed, SignalListCommand): path, method, body = "/signals?limit=100", "GET", None
        elif isinstance(parsed, SignalInspectCommand): path, method, body = f"/signals/{quote(parsed.signal_id, safe='')}", "GET", None
        elif isinstance(parsed, SignalInjectCommand):
            priority = int(parsed.priority) if isinstance(parsed.priority, int) else parsed.priority
            path, method, body = "/signals", "POST", {"type": parsed.signal_type, "source": parsed.source, "payload": dict(parsed.payload), "metadata": dict(parsed.metadata), "priority": priority}
        elif isinstance(parsed, TaskListCommand): path, method, body = "/tasks?limit=200", "GET", None
        elif isinstance(parsed, TaskInspectCommand): path, method, body = f"/tasks/{quote(parsed.task_id, safe='')}", "GET", None
        elif isinstance(parsed, TaskCancelCommand): path, method, body = f"/tasks/{quote(parsed.task_id, safe='')}/cancel", "POST", None
        elif isinstance(parsed, ActionListCommand): path, method, body = "/actions?limit=200", "GET", None
        elif isinstance(parsed, StateGetCommand): path, method, body = f"/entities/{quote(parsed.entity_id, safe='')}/state", "GET", None
        elif isinstance(parsed, StateSetCommand): path, method, body = f"/entities/{quote(parsed.entity_id, safe='')}/state", "PATCH", {"values": dict(parsed.values)}
        elif isinstance(parsed, LogsCommand): path, method, body = "/logs?limit=250", "GET", None
        elif isinstance(parsed, ProviderStatusCommand): path, method, body = "/providers", "GET", None
        elif isinstance(parsed, ProviderModeCommand): path, method, body = "/providers/mode", "PATCH", {"mode": parsed.mode}
        elif isinstance(parsed, ConfigInspectCommand):
            path, method, body = "/configuration", "GET", None
        elif isinstance(parsed, ConfigSetCommand):
            path, method, body = "/configuration", "PATCH", {"values": {parsed.path: parsed.value}}
        elif isinstance(parsed, ConfigReloadCommand):
            path, method, body = "/configuration/reload", "POST", None
        else: raise ValueError(f"unsupported command: {command}")
        return {"command": parsed.name.value, "data": await self._request(path, method=method, body=body)}

    async def chat(self, text: str, *, entity_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": text}
        if entity_id:
            payload["entity_id"] = entity_id
        return await self._request("/signals", method="POST", body={"type": "UserMessage", "source": "console", "payload": payload})

    async def decide_medulla_node(self, node_id: str, decision: str, *, reason: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"decision": decision}
        if reason: body["reason"] = reason
        return await self._request(f"/medulla/nodes/{quote(node_id, safe='')}/decision", method="POST", body=body)

    async def authorize_medulla_node(self, node_id: str, authorization: Mapping[str, Any]) -> dict[str, Any]:
        return await self._request(f"/medulla/nodes/{quote(node_id, safe='')}/authorization", method="POST", body={"authorization": dict(authorization)})
