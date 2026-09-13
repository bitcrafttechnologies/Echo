from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from medulla_node._yaml import load_yaml_mapping
from medulla_node.adapters.macbook import MacbookAdapter, MacbookLocation, MacbookSystemBackend
from medulla_node.config import MedullaNodeConfig, NodeConfigurationError
from medulla_node.runtime import MedullaNodeRuntime


@dataclass(slots=True, frozen=True, kw_only=True)
class MacbookNodeSettings:
    file_roots: tuple[str, ...]
    telemetry_interval: float = 60
    maximum_file_bytes: int = 262_144
    location: MacbookLocation | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MacbookNodeSettings":
        if type(data) is not dict:
            raise ValueError("macbook must be a mapping")
        unknown = set(data) - {"file_roots", "telemetry_interval", "maximum_file_bytes", "location"}
        if unknown: raise ValueError(f"macbook contains unknown fields: {sorted(unknown)!r}")
        roots = data.get("file_roots")
        if type(roots) is not list or any(type(item) is not str or not item for item in roots):
            raise ValueError("macbook.file_roots must be a list of paths")
        location_data = data.get("location")
        location = None
        if location_data is not None:
            if type(location_data) is not dict or set(location_data) - {"latitude", "longitude", "label"}:
                raise ValueError("macbook.location contains invalid fields")
            location = MacbookLocation(latitude=location_data.get("latitude"), longitude=location_data.get("longitude"), label=location_data.get("label"))
        return cls(file_roots=tuple(roots), telemetry_interval=data.get("telemetry_interval", 60), maximum_file_bytes=data.get("maximum_file_bytes", 262_144), location=location)


def load_macbook_runtime(path: str | Path, *, backend: MacbookSystemBackend | None = None) -> MedullaNodeRuntime:
    source = Path(path)
    try:
        data = load_yaml_mapping(source.read_text(encoding="utf-8"), source=source)
        settings = MacbookNodeSettings.from_dict(data.pop("macbook", None))
        config = MedullaNodeConfig.from_dict(data)
        runtime = MedullaNodeRuntime(config)
        roots = tuple(
            candidate if candidate.is_absolute() else source.resolve().parent / candidate
            for candidate in (Path(item).expanduser() for item in settings.file_roots)
        )
        runtime.register_adapter(MacbookAdapter(config.provider, file_roots=roots, location=settings.location, telemetry_interval=settings.telemetry_interval, maximum_file_bytes=settings.maximum_file_bytes, backend=backend))
        return runtime
    except NodeConfigurationError: raise
    except (OSError, TypeError, ValueError) as error:
        raise NodeConfigurationError(str(error), source=source) from error


__all__ = ["MacbookNodeSettings", "load_macbook_runtime"]
