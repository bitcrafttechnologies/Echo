from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from medulla_node._yaml import load_yaml_mapping
from medulla_node.adapters.web import DEFAULT_RESEARCH_DOMAINS, WebResearchAdapter, WebResearchBackend
from medulla_node.config import MedullaNodeConfig, NodeConfigurationError
from medulla_node.runtime import MedullaNodeRuntime


@dataclass(slots=True, frozen=True, kw_only=True)
class WebNodeSettings:
    allowed_domains: tuple[str, ...] = DEFAULT_RESEARCH_DOMAINS
    maximum_bytes: int = 524_288

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WebNodeSettings":
        if type(data) is not dict: raise ValueError("web must be a mapping")
        unknown = set(data) - {"allowed_domains", "maximum_bytes"}
        if unknown: raise ValueError(f"web contains unknown fields: {sorted(unknown)!r}")
        domains = data.get("allowed_domains", list(DEFAULT_RESEARCH_DOMAINS))
        if type(domains) is not list or any(type(item) is not str or not item for item in domains):
            raise ValueError("web.allowed_domains must be a list of hostnames")
        return cls(allowed_domains=tuple(domains), maximum_bytes=data.get("maximum_bytes", 524_288))


def load_web_runtime(path: str | Path, *, backend: WebResearchBackend | None = None) -> MedullaNodeRuntime:
    source = Path(path)
    try:
        data = load_yaml_mapping(source.read_text(encoding="utf-8"), source=source)
        settings = WebNodeSettings.from_dict(data.pop("web", None))
        config = MedullaNodeConfig.from_dict(data)
        runtime = MedullaNodeRuntime(config)
        runtime.register_adapter(WebResearchAdapter(config.provider, allowed_domains=settings.allowed_domains, maximum_bytes=settings.maximum_bytes, backend=backend))
        return runtime
    except NodeConfigurationError: raise
    except (OSError, TypeError, ValueError) as error:
        raise NodeConfigurationError(str(error), source=source) from error


__all__ = ["WebNodeSettings", "load_web_runtime"]
