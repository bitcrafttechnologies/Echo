"""Dependency-light ANSI terminal application for Echo's operator surfaces."""

from __future__ import annotations

import asyncio
from datetime import datetime
from importlib.resources import files
import json
import os
import select
import shutil
import sys
import termios
import time
import tty
from typing import Any

from echo.tui.client import EchoConsoleClient
from echo.tui.registry import ConsoleSurfaceRegistry, default_registry

ORANGE = "\x1b[38;2;255;138;61m"
GREEN = "\x1b[38;2;87;215;150m"
MUTED = "\x1b[38;2;132;145;160m"
RED = "\x1b[38;2;239;123;132m"
BOLD = "\x1b[1m"
RESET = "\x1b[0m"


def _mark() -> str:
    return files("echo.tui").joinpath("assets/echo_mark.txt").read_text().rstrip()


def _json(value: Any, width: int = 100) -> str:
    rendered = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return rendered if len(rendered) <= width else rendered[: max(1, width - 1)] + "…"


def _time(value: Any) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%H:%M:%S")
    except ValueError:
        return str(value)


def _title(text: str) -> list[str]:
    return [text.upper(), "─" * len(text)]


def _overview(data: dict[str, Any]) -> list[str]:
    runtime = data.get("runtime", {})
    entities = data.get("entities", [])
    tasks = data.get("tasks", [])
    signals = data.get("signals", [])
    active = [item for item in tasks if item.get("status") in {"pending", "running", "paused", "blocked"}]
    lines = [*_mark().splitlines(), "", *_title("System overview")]
    lines += [
        f"Core        ● {runtime.get('status', 'unavailable')}",
        f"Runtime     {runtime.get('runtime_id', '—')}",
        f"Uptime      {float(runtime.get('uptime_seconds', 0)):.1f}s",
        f"Entities    {len(entities)} loaded",
        f"Tasks       {len(active)} active / {len(tasks)} retained",
        f"Signals     {len(signals)} recent",
        "",
        "LOADED ENTITIES",
    ]
    lines.extend(
        f"  ● {entity.get('id')}  handlers={entity.get('handlers', {}).get('count', 0)}  active_tasks={len(entity.get('active_task_ids', []))}"
        for entity in entities
    )
    lines += ["", "RECENT ACTIVITY"]
    for event in reversed(data.get("logs", [])[-10:]):
        links = " ".join(str(event.get(key)) for key in ("entity_id", "signal_id", "task_id") if event.get(key))
        lines.append(f"  {_time(event.get('timestamp'))} {event.get('severity', 'info'):<7} {event.get('event_type', '—')} {links}")
    return lines


def _signals(data: dict[str, Any]) -> list[str]:
    signals = data.get("signals", [])
    actions = data.get("actions", [])
    lines = _title(f"Signal inspector · {len(signals)} retained")
    if data.get("filters"):
        lines.append("Filters: " + " ".join(f"{key}={value}" for key, value in data["filters"].items()))
    for signal in signals:
        routing = signal.get("routing_result", {})
        related = [item.get("id") for item in actions if item.get("signal_id") == signal.get("id")]
        lines += [
            f"{_time(signal.get('timestamp'))}  {signal.get('type')}  source={signal.get('source')}  {routing.get('status', '—')}",
            f"  id={signal.get('id')} handlers={routing.get('handler_count', 0)} tasks={','.join(routing.get('task_ids', [])) or '—'} actions={','.join(related) or '—'}",
            f"  payload={_json(signal.get('payload', {}))} metadata={_json(signal.get('metadata', {}))}",
        ]
    return lines or ["No Signals are retained."]


def _tasks(data: dict[str, Any]) -> list[str]:
    tasks = data.get("tasks", [])
    lines = _title(f"Task inspector · {len(tasks)} retained")
    for task in tasks:
        relation = f"parent={task.get('parent') or '—'} children={','.join(task.get('children', [])) or '—'}"
        lines += [
            f"{task.get('status', '—'):<10} {task.get('name', '—')}  owner={task.get('owner', '—')} priority={task.get('priority', '—')}",
            f"  id={task.get('id')} signal={task.get('signal_id') or '—'} {relation}",
        ]
        if task.get("error"):
            lines.append(f"  error={task['error']}")
        if task.get("result") is not None:
            lines.append(f"  result={_json(task['result'])}")
    return lines


def _entity(data: dict[str, Any]) -> list[str]:
    entity = data.get("selected_entity")
    lines = _title("Entity inspector")
    lines.append("Loaded: " + (", ".join(item.get("id", "?") for item in data.get("entities", [])) or "none"))
    if not entity:
        return lines + ["", "No Entity is loaded."]
    lines += [
        "",
        f"ENTITY {entity.get('id')}",
        f"State             {_json(entity.get('state', {}))}",
        f"Active Tasks      {', '.join(entity.get('active_task_ids', [])) or '—'}",
        f"Handlers          {entity.get('handlers', {}).get('count', 0)}",
    ]
    for registration in entity.get("handlers", {}).get("registrations", []):
        lines.append(f"  {registration.get('signal')} → {registration.get('handler')}")
    character = entity.get("character", {})
    labels = (
        ("Identity", "identity"), ("Traits", "traits"), ("Control state", "internal_state"),
        ("Drives", "drives"), ("Self model / embodiment", "self_model"),
        ("Attention", "attention_candidates"),
        ("Memory", "memory"),
        ("Durable memory", "durable_memory"),
    )
    lines.append("")
    for label, key in labels:
        lines.append(f"{label:<24} {_json(character.get(key, {}))}")
    lines.append("Relationships")
    for relationship in data.get("relationships", []):
        lines.append(
            f"  {relationship.get('subject_id')} familiarity={relationship.get('familiarity')} trust={relationship.get('trust')} interactions={relationship.get('interaction_count')} context={_json(relationship.get('current_context', {}), 60)}"
        )
    return lines


def _logs(data: dict[str, Any]) -> list[str]:
    logs = data.get("logs", [])
    lines = _title(f"Structured logs · {len(logs)} events")
    if data.get("filters"):
        lines.append("Filters: " + " ".join(f"{key}={value}" for key, value in data["filters"].items()))
    for event in reversed(logs):
        links = " ".join(f"{key[:-3]}={event[key]}" for key in ("entity_id", "signal_id", "task_id", "action_id") if event.get(key))
        lines.append(f"{_time(event.get('timestamp'))} {event.get('severity', 'info'):<7} {event.get('event_type', '—'):<22} {links}")
        if event.get("metadata"):
            lines.append(f"  {_json(event['metadata'])}")
    return lines


def _providers(data: dict[str, Any]) -> list[str]:
    status = data.get("providers", {})
    active = status.get("active_provider") or {}
    latency = status.get("last_latency_ms")
    lines = [*_title("Provider routing"), ""]
    lines += [
        f"Inference mode       {status.get('mode', 'auto')}",
        f"Routing preference   {' → '.join(status.get('preference', [])) or '—'}",
        f"Configured providers {', '.join(status.get('configured_providers', {})) or 'none'}",
        f"Active provider      {active.get('name') or active.get('provider_id') or '—'}",
        f"Model                {status.get('model') or '—'}",
        f"Last latency         {latency:.2f} ms" if isinstance(latency, (int, float)) else "Last latency         —",
        "",
        "HEALTH",
    ]
    for slot, health in status.get("health", {}).items():
        lines.append(f"  {slot:<8} {health.get('status', 'unknown'):<12} {health.get('message') or ''}")
    lines += ["", "RECENT FAILURES"]
    failures = status.get("recent_failures", [])
    if not failures:
        lines.append("  None")
    for failure in failures[:10]:
        lines.append(
            f"  {failure.get('slot', '—'):<8} request={failure.get('request_id', '—')} {failure.get('error_reason', 'unknown error')}"
        )
    lines += [
        "",
        "Switch: provider mode auto|remote|lan|offline",
        "Reload: config reload",
    ]
    return lines


def _configuration(data: dict[str, Any]) -> list[str]:
    configuration = data.get("configuration", {})
    lines = [*_title("Effective configuration"), ""]
    lines.append(f"Source  {configuration.get('source') or 'defaults and environment'}")
    for field in configuration.get("fields", []):
        marker = "hidden" if field.get("secret") else field.get("classification", "restart_required")
        value = (
            "configured — value hidden" if field.get("secret") and field.get("configured")
            else "not configured — value hidden" if field.get("secret")
            else _json(field.get("value"), 55)
        )
        lines.append(f"  {field.get('path', '—'):<42} [{marker}] {value}")
    lines += ["", "Edit: config set <dotted-path> <json-value>", "Reload: config reload"]
    return lines


def _chat(data: dict[str, Any]) -> list[str]:
    actions = data.get("actions", [])
    signals = list(reversed(data.get("signals", [])))
    lines = _title("Chat · UserMessage Signal path")
    lines.append("Messages and responses use normal Signals, Tasks, and Actions.")
    for signal in signals:
        lines += ["", f"You  {_time(signal.get('timestamp'))}", f"  {signal.get('payload', {}).get('text', '')}"]
        related = [item for item in actions if item.get("signal_id") == signal.get("id")]
        if not related:
            status = signal.get("routing_result", {}).get("status")
            lines.append("  [No handler accepted this message]" if status == "unhandled" else "  [Waiting for routed activity]")
        for action in related:
            parameters = action.get("parameters", {})
            response = next((value for value in (parameters.get("text"), parameters.get("message"), parameters.get("content"), action.get("result")) if isinstance(value, str)), _json(parameters))
            lines += [f"Echo · {action.get('type')}  [{action.get('status')}]", f"  {response}"]
    return lines


_RENDERERS = {"overview": _overview, "chat": _chat, "signals": _signals, "tasks": _tasks, "entity": _entity, "providers": _providers, "configuration": _configuration, "logs": _logs}


def render_snapshot(surface: str, data: dict[str, Any], *, width: int = 100, color: bool = False) -> str:
    """Render a complete semantic text snapshot; color is optional decoration."""
    if surface not in _RENDERERS:
        raise ValueError(f"unknown console surface: {surface}")
    runtime = data.get("runtime", {})
    entity = (data.get("selected_entity") or (data.get("entities") or [{}])[0]).get("id", "—")
    state = runtime.get("status", "offline")
    core = "OFFLINE" if state in {"offline", "unavailable"} else "ONLINE"
    header = f"ECHO ◉   entity: {entity}   CORE {core}   runtime: {str(state).upper()}   LOCAL/OFFLINE"
    body = [line[:width] for line in _RENDERERS[surface](data)]
    footer = "[1] Overview [2] Chat [3] Signals [4] Tasks [5] Entity [6] Providers [7] Config [8] Logs   [:] command [r] refresh [q] quit"
    output = "\n".join([header[:width], "━" * min(width, len(header) + 8), *body, "", footer[:width]])
    if color:
        output = output.replace("ECHO ◉", f"{ORANGE}{BOLD}ECHO ◉{RESET}", 1)
        output = output.replace("CORE ONLINE", f"{GREEN}CORE ONLINE{RESET}")
        output = output.replace("CORE OFFLINE", f"{RED}CORE OFFLINE{RESET}")
    return output


class EchoTui:
    """A small full-screen terminal view over an EchoConsoleClient."""

    def __init__(self, client: EchoConsoleClient, *, surface: str = "overview", refresh_seconds: float = 2.0, registry: ConsoleSurfaceRegistry | None = None) -> None:
        if not isinstance(client, EchoConsoleClient):
            raise TypeError("client must implement EchoConsoleClient")
        self.client = client
        self.surface = surface
        self.refresh_seconds = refresh_seconds
        self.registry = registry or default_registry()
        self.entity_id: str | None = None
        self.filters: dict[str, str] = {}
        self.message = ""
        self._original_terminal: list[Any] | None = None

    async def snapshot(self) -> dict[str, Any]:
        return await self.client.snapshot(self.surface, entity_id=self.entity_id, filters=self.filters)

    async def run(self) -> None:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise RuntimeError("interactive Echo Console requires a TTY; use --snapshot for plain output")
        self._original_terminal = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())
        last_refresh = 0.0
        data: dict[str, Any] = {}
        try:
            while True:
                now = time.monotonic()
                if now - last_refresh >= self.refresh_seconds or not data:
                    try:
                        data = await self.snapshot()
                        self.message = ""
                    except Exception as error:
                        data = {"runtime": {"status": "offline"}, "entities": []}
                        self.message = str(error)
                    last_refresh = now
                    self._draw(data)
                ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                if not ready:
                    await asyncio.sleep(0)
                    continue
                key = os.read(sys.stdin.fileno(), 1).decode(errors="ignore")
                if key == "q":
                    break
                if key in "12345678":
                    self.surface = self.registry.list()[int(key) - 1].key
                    self.filters = {}
                    data = {}
                elif key == "r":
                    data = {}
                elif key == "f" and self.surface in {"signals", "logs"}:
                    hint = "type=... source=..." if self.surface == "signals" else "severity=... event_type=..."
                    self.filters = self._parse_filters(self._read_line(f"filter ({hint}; blank clears) › "))
                    data = {}
                elif key == "e" and self.surface in {"entity", "chat"}:
                    self.entity_id = self._read_line("entity id › ") or None
                    data = {}
                elif key == ":":
                    command = self._read_line("echo › ")
                    if command:
                        try:
                            result = await self.client.execute(command)
                            self.message = _json(result, 180)
                        except Exception as error:
                            self.message = str(error)
                    self._draw(data)
                elif key == "c" and self.surface == "chat":
                    message = self._read_line("You › ")
                    if message:
                        try:
                            await self.client.chat(message, entity_id=self.entity_id)
                            data = {}
                        except Exception as error:
                            self.message = str(error)
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._original_terminal)
            sys.stdout.write("\x1b[?25h\x1b[0m\n")
            sys.stdout.flush()

    def _read_line(self, prompt: str) -> str:
        assert self._original_terminal is not None
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._original_terminal)
        try:
            return input(f"\x1b[?25h\n{prompt}").strip()
        finally:
            tty.setcbreak(sys.stdin.fileno())

    def _draw(self, data: dict[str, Any]) -> None:
        size = shutil.get_terminal_size((100, 32))
        rendered = render_snapshot(self.surface, data, width=size.columns, color="NO_COLOR" not in os.environ)
        lines = rendered.splitlines()[: max(1, size.lines - 2)]
        if self.message:
            lines.append(f"{ORANGE}{self.message[:size.columns]}{RESET}")
        sys.stdout.write("\x1b[?25l\x1b[2J\x1b[H" + "\n".join(lines))
        sys.stdout.flush()

    @staticmethod
    def _parse_filters(value: str) -> dict[str, str]:
        filters: dict[str, str] = {}
        for item in value.split():
            if "=" not in item:
                continue
            key, candidate = item.split("=", 1)
            if key and candidate:
                filters[key] = candidate
        return filters
