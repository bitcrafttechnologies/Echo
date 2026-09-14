"""Read-only macOS host capabilities and bounded local telemetry."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import json
import shutil
import subprocess
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from medulla_node.adapters import MedullaAdapter
from medulla_protocol import (
    AdapterEvent,
    Capability,
    CapabilityAvailability,
    CapabilityAvailabilityState,
    CapabilityEffect,
    CapabilityPermissions,
    CapabilityProvider,
    CapabilityRisk,
    CapabilityTimeout,
    MedullaNodeResource,
    MedullaNodeSignal,
    NodeAction,
)


class MacbookSystemBackend(Protocol):
    def battery(self) -> Mapping[str, Any]: ...
    def health(self) -> Mapping[str, Any]: ...
    def now(self) -> datetime: ...
    def weather(self, latitude: float, longitude: float) -> Mapping[str, Any]: ...


class NativeMacbookBackend:
    """Standard-library macOS probes; no shell strings or elevated commands."""

    def battery(self) -> Mapping[str, Any]:
        if platform.system() != "Darwin":
            return {"available": False, "reason": "battery probe requires macOS"}
        completed = subprocess.run(
            ["/usr/bin/pmset", "-g", "batt"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        text = completed.stdout
        percent = None
        for token in text.replace(";", " ").split():
            if token.endswith("%") and token[:-1].isdigit():
                percent = int(token[:-1])
                break
        lowered = text.lower()
        result: dict[str, Any] = {
            "available": completed.returncode == 0 and percent is not None,
            "percent": percent,
            "charging": "charging" in lowered and "not charging" not in lowered,
            "power_source": "ac" if "ac power" in lowered else "battery",
        }
        try:
            details = subprocess.run(
                ["/usr/sbin/system_profiler", "SPPowerDataType", "-json"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            profile = json.loads(details.stdout) if details.returncode == 0 else {}
            flattened = _flatten_mapping(profile)
            for source, target in (
                ("sppower_battery_cycle_count", "cycle_count"),
                ("sppower_battery_health", "condition"),
                ("sppower_battery_maximum_capacity", "maximum_capacity"),
            ):
                if source in flattened:
                    result[target] = flattened[source]
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            pass
        return result

    def health(self) -> Mapping[str, Any]:
        root = shutil.disk_usage("/")
        loads = os.getloadavg()
        return {
            "platform": platform.platform(),
            "hostname": platform.node(),
            "cpu_count": os.cpu_count(),
            "load_average": [round(value, 3) for value in loads],
            "disk_total_bytes": root.total,
            "disk_free_bytes": root.free,
            "disk_used_percent": round((root.used / root.total) * 100, 2) if root.total else 0,
        }

    def now(self) -> datetime:
        return datetime.now().astimezone()

    def weather(self, latitude: float, longitude: float) -> Mapping[str, Any]:
        query = urlencode(
            {
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
                "timezone": "auto",
            }
        )
        request = Request(
            f"https://api.open-meteo.com/v1/forecast?{query}",
            headers={"Accept": "application/json", "User-Agent": "medulla-macbook/0.1"},
        )
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read(262_144).decode("utf-8"))
        return {
            "latitude": payload.get("latitude"),
            "longitude": payload.get("longitude"),
            "timezone": payload.get("timezone"),
            "current": payload.get("current", {}),
            "units": payload.get("current_units", {}),
            "source": "open-meteo.com",
        }


@dataclass(slots=True, frozen=True, kw_only=True)
class MacbookLocation:
    latitude: float
    longitude: float
    label: str | None = None

    def __post_init__(self) -> None:
        if type(self.latitude) not in (int, float) or not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be between -90 and 90")
        if type(self.longitude) not in (int, float) or not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be between -180 and 180")

    def to_dict(self) -> dict[str, Any]:
        result = {"latitude": float(self.latitude), "longitude": float(self.longitude)}
        if self.label:
            result["label"] = self.label
        return result


class MacbookAdapter(MedullaAdapter):
    """Expose selected roots and read-only host information from one Mac."""

    def __init__(
        self,
        provider: CapabilityProvider,
        *,
        file_roots: Sequence[str | Path],
        location: MacbookLocation | None = None,
        telemetry_interval: float = 60.0,
        maximum_file_bytes: int = 262_144,
        backend: MacbookSystemBackend | None = None,
    ) -> None:
        if not isinstance(provider, CapabilityProvider):
            raise TypeError("provider must be CapabilityProvider")
        roots = tuple(Path(item).expanduser().resolve() for item in file_roots)
        if not roots or len(set(roots)) != len(roots):
            raise ValueError("file_roots must contain unique allowed roots")
        if telemetry_interval <= 0:
            raise ValueError("telemetry_interval must be positive")
        if type(maximum_file_bytes) is not int or maximum_file_bytes < 1:
            raise ValueError("maximum_file_bytes must be a positive integer")
        self._roots = roots
        self._location = location
        self._telemetry_interval = float(telemetry_interval)
        self._maximum_file_bytes = maximum_file_bytes
        self._backend = backend or NativeMacbookBackend()
        self._telemetry_task: asyncio.Task[None] | None = None
        self._started = False
        capabilities = [
            _capability(provider, "filesystem.list", "List one allowed local directory", CapabilityRisk.SENSITIVE_DATA),
            _capability(provider, "filesystem.read", "Read one bounded UTF-8 file under an allowed root", CapabilityRisk.SENSITIVE_DATA),
            _capability(provider, "filesystem.stat", "Inspect metadata for one allowed path", CapabilityRisk.SENSITIVE_DATA),
            _capability(provider, "battery.status", "Read Mac battery status", CapabilityRisk.READ),
            _capability(provider, "system.health", "Read bounded Mac system health", CapabilityRisk.READ),
            _capability(provider, "system.datetime", "Read the Mac local date and time", CapabilityRisk.READ),
        ]
        if location is not None:
            capabilities.extend(
                [
                    _capability(provider, "location.current", "Read the configured coarse location", CapabilityRisk.SENSITIVE_DATA),
                    _capability(provider, "weather.current", "Read current Open-Meteo weather for the configured location", CapabilityRisk.NETWORK, maximum=12),
                ]
            )
        super().__init__(
            adapter_id="macbook",
            capabilities=tuple(capabilities),
            signals=(
                MedullaNodeSignal(name="battery.telemetry", schema={"type": "object"}, adapter="macbook"),
                MedullaNodeSignal(name="system.health.telemetry", schema={"type": "object"}, adapter="macbook"),
                MedullaNodeSignal(name="system.datetime.telemetry", schema={"type": "object"}, adapter="macbook"),
            ),
            resources=tuple(
                MedullaNodeResource(
                    resource_id=f"filesystem.root.{index}",
                    description=f"Allowed file root {root.name or root}",
                    schema={"type": "string"},
                    metadata={"root_label": root.name or "filesystem"},
                )
                for index, root in enumerate(roots)
            )
            + (
                MedullaNodeResource(resource_id="system.macbook", description="Local MacBook system", schema={"type": "object"}),
            ),
        )

    async def start(self) -> None:
        self._started = True
        self._telemetry_task = asyncio.create_task(self._telemetry_loop(), name="macbook-telemetry")

    async def stop(self) -> None:
        self._started = False
        if self._telemetry_task is not None:
            self._telemetry_task.cancel()
            await asyncio.gather(self._telemetry_task, return_exceptions=True)
        self._telemetry_task = None

    async def execute(self, action: NodeAction) -> Mapping[str, Any]:
        if not self._started:
            raise RuntimeError("MacBook adapter is not running")
        if action.type.startswith("filesystem."):
            path = self._allowed_path(
                action.parameters.get("path"), resource_id=action.resource_id
            )
            if action.type == "filesystem.list":
                if not path.is_dir():
                    raise ValueError("path is not a directory")
                entries = await asyncio.to_thread(lambda: sorted(path.iterdir(), key=lambda item: item.name.lower()))
                return {
                    "path": os.fspath(path),
                    "entries": [
                        {"name": item.name, "type": "directory" if item.is_dir() else "file"}
                        for item in entries[:500]
                    ],
                    "truncated": len(entries) > 500,
                }
            if action.type == "filesystem.stat":
                stat = await asyncio.to_thread(path.stat)
                return {"path": os.fspath(path), "size": stat.st_size, "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(), "is_directory": path.is_dir()}
            if action.type == "filesystem.read":
                if not path.is_file():
                    raise ValueError("path is not a file")
                size = path.stat().st_size
                if size > self._maximum_file_bytes:
                    raise ValueError(f"file exceeds {self._maximum_file_bytes} byte limit")
                content = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="strict")
                return {"path": os.fspath(path), "content": content, "bytes": size}
        if action.type == "battery.status":
            return dict(await asyncio.to_thread(self._backend.battery))
        if action.type == "system.health":
            return dict(await asyncio.to_thread(self._backend.health))
        if action.type == "system.datetime":
            return self._datetime_payload()
        if action.type == "location.current" and self._location is not None:
            return self._location.to_dict()
        if action.type == "weather.current" and self._location is not None:
            return dict(await asyncio.to_thread(self._backend.weather, self._location.latitude, self._location.longitude))
        raise ValueError(f"unsupported MacBook Action: {action.type}")

    def _allowed_path(self, value: Any, *, resource_id: str | None = None) -> Path:
        root: Path | None = None
        if resource_id is not None:
            prefix = "filesystem.root."
            if not resource_id.startswith(prefix):
                raise ValueError("filesystem Action requires a filesystem root resource_id")
            try:
                root = self._roots[int(resource_id.removeprefix(prefix))]
            except (ValueError, IndexError) as error:
                raise ValueError("filesystem Action has an unknown root resource_id") from error
        if value in (None, "") and root is not None:
            return root
        if type(value) is not str or not value:
            raise ValueError("filesystem Action requires a path or root resource_id")
        supplied = Path(value).expanduser()
        candidate = (root / supplied).resolve() if root is not None and not supplied.is_absolute() else supplied.resolve()
        if not any(candidate == root or candidate.is_relative_to(root) for root in self._roots):
            raise PermissionError("path is outside configured file roots")
        return candidate

    def _datetime_payload(self) -> dict[str, Any]:
        now = self._backend.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("system clock returned a naive datetime")
        return {"iso8601": now.isoformat(), "timezone": str(now.tzinfo), "utc_offset_seconds": int(now.utcoffset().total_seconds())}

    async def _telemetry_loop(self) -> None:
        while True:
            try:
                await self.emit(AdapterEvent(signal_type="battery.telemetry", resource_id="system.macbook", payload=dict(await asyncio.to_thread(self._backend.battery))))
                await self.emit(AdapterEvent(signal_type="system.health.telemetry", resource_id="system.macbook", payload=dict(await asyncio.to_thread(self._backend.health))))
                await self.emit(AdapterEvent(signal_type="system.datetime.telemetry", resource_id="system.macbook", payload=self._datetime_payload()))
            except asyncio.CancelledError:
                raise
            except Exception:
                # One failed probe must not stop later telemetry.
                pass
            await asyncio.sleep(self._telemetry_interval)


def _capability(provider: CapabilityProvider, name: str, description: str, risk: CapabilityRisk, *, maximum: float = 5) -> Capability:
    return Capability(
        capability_id=f"{provider.provider_id}.{name}.v1",
        name=name,
        description=description,
        provider=provider,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        availability=CapabilityAvailability(state=CapabilityAvailabilityState.AVAILABLE),
        permissions=CapabilityPermissions(risk=risk, required=(name,)),
        effect=CapabilityEffect.READ_ONLY,
        timeout=CapabilityTimeout(expected_seconds=0.1, maximum_seconds=maximum),
    )


def _flatten_mapping(value: Any) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and not isinstance(item, (Mapping, list)):
                flattened[key] = item
            flattened.update(_flatten_mapping(item))
    elif isinstance(value, list):
        for item in value:
            flattened.update(_flatten_mapping(item))
    return flattened


__all__ = ["MacbookAdapter", "MacbookLocation", "MacbookSystemBackend", "NativeMacbookBackend"]
