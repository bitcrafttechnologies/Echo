"""Typed, validated configuration for Echo applications.

TOML is the Phase 6A configuration format.  The loader intentionally depends
only on :mod:`tomllib`, keeping the Echo kernel usable with the standard
library while replacing provider-specific environment parsing at startup.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import json
import logging
import math
import os
import tomllib
from typing import Any
from urllib.parse import urlsplit

from echo.core.entity import Entity
from echo.core.runtime import Runtime
from echo.providers import (
    LanInferenceConfig,
    LanInferenceProvider,
    OfflineInferenceConfig,
    OfflineInferenceProvider,
    OpenRouterConfig,
    OpenRouterProvider,
    ProviderMode,
    ProviderRouter,
    ProviderSlot,
)


DEFAULT_CONFIG_PATH = Path("echo.toml")


@dataclass(slots=True, kw_only=True, frozen=True)
class ConfigurationIssue:
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


class ConfigurationError(ValueError):
    """One or more configuration values could not be validated."""

    def __init__(
        self,
        issues: Iterable[ConfigurationIssue],
        *,
        source: str | Path | None = None,
    ) -> None:
        self.issues = tuple(issues)
        self.source = str(source) if source is not None else None
        heading = "Invalid Echo configuration"
        if self.source:
            heading += f" from {self.source}"
        details = "\n".join(
            f"  - {issue.path}: {issue.message}" for issue in self.issues
        )
        super().__init__(f"{heading}:\n{details}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": "invalid_configuration",
            "message": str(self),
            "source": self.source,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class RuntimeConfig:
    auto_start: bool = True


@dataclass(slots=True, kw_only=True, frozen=True)
class LoggingConfig:
    level: str = "INFO"
    format: str = "text"


@dataclass(slots=True, kw_only=True, frozen=True)
class HistoryConfig:
    signals: int = 1000
    tasks: int = 1000
    actions: int = 1000
    logs: int = 1000
    errors: int = 1000


@dataclass(slots=True, kw_only=True, frozen=True)
class OpenRouterSettings:
    enabled: bool = False
    api_key: str | None = field(default=None, repr=False)
    model: str = "openrouter/auto"
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 60.0
    app_url: str | None = None
    app_title: str | None = "Echo"

    def provider_config(self) -> OpenRouterConfig:
        return OpenRouterConfig(
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
            timeout_seconds=self.timeout_seconds,
            app_url=self.app_url,
            app_title=self.app_title,
        )


@dataclass(slots=True, kw_only=True, frozen=True)
class LanProviderSettings:
    enabled: bool = False
    base_url: str | None = None
    model: str = "local-model"
    timeout_seconds: float = 30.0
    api_key: str | None = field(default=None, repr=False)

    def provider_config(self) -> LanInferenceConfig:
        return LanInferenceConfig(
            base_url=self.base_url,
            model=self.model,
            timeout_seconds=self.timeout_seconds,
            api_key=self.api_key,
        )


@dataclass(slots=True, kw_only=True, frozen=True)
class OfflineProviderSettings:
    enabled: bool = False
    model_path: Path | None = None
    model: str | None = None
    executable: str = "llama-server"
    request_timeout_seconds: float = 120.0
    startup_timeout_seconds: float = 120.0
    shutdown_timeout_seconds: float = 10.0
    port: int = 0
    extra_args: tuple[str, ...] = ()

    def provider_config(self) -> OfflineInferenceConfig:
        return OfflineInferenceConfig(
            model_path=self.model_path,
            model=self.model,
            executable=self.executable,
            request_timeout_seconds=self.request_timeout_seconds,
            startup_timeout_seconds=self.startup_timeout_seconds,
            shutdown_timeout_seconds=self.shutdown_timeout_seconds,
            port=self.port,
            extra_args=self.extra_args,
        )


@dataclass(slots=True, kw_only=True, frozen=True)
class ProviderRoutingConfig:
    mode: ProviderMode = ProviderMode.AUTO
    preference: tuple[ProviderSlot, ...] = (
        ProviderSlot.REMOTE,
        ProviderSlot.LAN,
        ProviderSlot.OFFLINE,
    )
    history_limit: int = 100
    openrouter: OpenRouterSettings = field(default_factory=OpenRouterSettings)
    lan: LanProviderSettings = field(default_factory=LanProviderSettings)
    offline: OfflineProviderSettings = field(default_factory=OfflineProviderSettings)


@dataclass(slots=True, kw_only=True, frozen=True)
class ApiServerConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8000

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(slots=True, kw_only=True, frozen=True)
class ConsoleConfig:
    api_url: str = "http://127.0.0.1:8000"
    request_timeout_seconds: float = 3.0
    poll_interval_seconds: float = 1.0
    session_name: str = "echo-core"


@dataclass(slots=True, kw_only=True, frozen=True)
class EchoConfig:
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    providers: ProviderRoutingConfig = field(default_factory=ProviderRoutingConfig)
    api: ApiServerConfig = field(default_factory=ApiServerConfig)
    console: ConsoleConfig = field(default_factory=ConsoleConfig)

    def create_runtime(self, entities: Iterable[Entity] = ()) -> Runtime:
        return Runtime(
            entities,
            signal_history_size=self.history.signals,
            task_history_size=self.history.tasks,
            action_history_size=self.history.actions,
            log_history_size=self.history.logs,
            error_history_size=self.history.errors,
            auto_start=self.runtime.auto_start,
        )

    def create_provider_router(self) -> ProviderRouter:
        remote = (
            OpenRouterProvider(self.providers.openrouter.provider_config())
            if self.providers.openrouter.enabled
            else None
        )
        lan = (
            LanInferenceProvider(self.providers.lan.provider_config())
            if self.providers.lan.enabled
            else None
        )
        offline = (
            OfflineInferenceProvider(self.providers.offline.provider_config())
            if self.providers.offline.enabled
            else None
        )
        return ProviderRouter(
            remote=remote,
            lan=lan,
            offline=offline,
            mode=self.providers.mode,
            preference=self.providers.preference,
            history_limit=self.providers.history_limit,
        )

    def configure_logging(self) -> None:
        """Apply the validated Python logging policy for application startup."""

        handler = logging.StreamHandler()
        if self.logging.format == "json":
            handler.setFormatter(_JsonLogFormatter())
        else:
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s: %(message)s"
                )
            )
        root = logging.getLogger()
        root.handlers.clear()
        root.addHandler(handler)
        root.setLevel(getattr(logging, self.logging.level))


_ENV_OVERRIDES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("runtime", "auto_start"), ("ECHO_RUNTIME_AUTO_START",)),
    (("logging", "level"), ("ECHO_LOG_LEVEL",)),
    (("logging", "format"), ("ECHO_LOG_FORMAT",)),
    (("history", "signals"), ("ECHO_HISTORY_SIGNALS",)),
    (("history", "tasks"), ("ECHO_HISTORY_TASKS",)),
    (("history", "actions"), ("ECHO_HISTORY_ACTIONS",)),
    (("history", "logs"), ("ECHO_HISTORY_LOGS",)),
    (("history", "errors"), ("ECHO_HISTORY_ERRORS",)),
    (("providers", "mode"), ("ECHO_PROVIDER_MODE",)),
    (("providers", "preference"), ("ECHO_PROVIDER_PREFERENCE",)),
    (("providers", "history_limit"), ("ECHO_PROVIDER_HISTORY_LIMIT",)),
    (("providers", "openrouter", "enabled"), ("ECHO_OPENROUTER_ENABLED",)),
    (("providers", "openrouter", "api_key"), ("OPENROUTER_API_KEY",)),
    (("providers", "openrouter", "model"), ("OPENROUTER_MODEL",)),
    (("providers", "openrouter", "base_url"), ("OPENROUTER_BASE_URL", "OPENROUTER_URL")),
    (("providers", "openrouter", "timeout_seconds"), ("OPENROUTER_TIMEOUT_SECONDS",)),
    (("providers", "openrouter", "app_url"), ("OPENROUTER_APP_URL",)),
    (("providers", "openrouter", "app_title"), ("OPENROUTER_APP_TITLE",)),
    (("providers", "lan", "enabled"), ("ECHO_LAN_PROVIDER_ENABLED",)),
    (
        ("providers", "lan", "base_url"),
        ("LAN_INFERENCE_URL", "LAN_INFERENCE_BASE_URL", "LLAMA_CPP_BASE_URL"),
    ),
    (("providers", "lan", "model"), ("LAN_INFERENCE_MODEL", "LLAMA_CPP_MODEL")),
    (
        ("providers", "lan", "timeout_seconds"),
        ("LAN_INFERENCE_TIMEOUT_SECONDS", "LLAMA_CPP_TIMEOUT_SECONDS"),
    ),
    (("providers", "lan", "api_key"), ("LAN_INFERENCE_API_KEY", "LLAMA_CPP_API_KEY")),
    (("providers", "offline", "enabled"), ("ECHO_OFFLINE_PROVIDER_ENABLED",)),
    (("providers", "offline", "model_path"), ("OFFLINE_MODEL_PATH", "LLAMA_CPP_MODEL_PATH")),
    (("providers", "offline", "model"), ("OFFLINE_MODEL", "LLAMA_CPP_OFFLINE_MODEL")),
    (("providers", "offline", "executable"), ("LLAMA_CPP_SERVER",)),
    (("providers", "offline", "request_timeout_seconds"), ("OFFLINE_REQUEST_TIMEOUT_SECONDS",)),
    (("providers", "offline", "startup_timeout_seconds"), ("OFFLINE_STARTUP_TIMEOUT_SECONDS",)),
    (("providers", "offline", "shutdown_timeout_seconds"), ("OFFLINE_SHUTDOWN_TIMEOUT_SECONDS",)),
    (("providers", "offline", "port"), ("OFFLINE_SERVER_PORT",)),
    (("api", "enabled"), ("ECHO_API_ENABLED",)),
    (("api", "host"), ("ECHO_API_HOST",)),
    (("api", "port"), ("ECHO_API_PORT",)),
    (("console", "api_url"), ("ECHO_CONSOLE_API_URL",)),
    (("console", "request_timeout_seconds"), ("ECHO_CONSOLE_REQUEST_TIMEOUT_SECONDS",)),
    (("console", "poll_interval_seconds"), ("ECHO_CONSOLE_POLL_INTERVAL_SECONDS",)),
    (("console", "session_name"), ("ECHO_CONSOLE_SESSION_NAME",)),
)


def load_config(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> EchoConfig:
    """Load TOML, overlay supported environment values, and validate once.

    With no explicit path, ``ECHO_CONFIG`` is used, then ``echo.toml`` if it
    exists.  If neither exists, validated defaults plus environment overrides
    are returned.
    """

    values = os.environ if environ is None else environ
    selected: Path | None
    if path is not None:
        selected = Path(path)
    elif values.get("ECHO_CONFIG"):
        selected = Path(values["ECHO_CONFIG"])
    elif DEFAULT_CONFIG_PATH.is_file():
        selected = DEFAULT_CONFIG_PATH
    else:
        selected = None

    raw: dict[str, Any] = {}
    if selected is not None:
        if selected.suffix.lower() != ".toml":
            raise ConfigurationError(
                [
                    ConfigurationIssue(
                        path="$",
                        message="configuration file must use the .toml extension",
                    )
                ],
                source=selected,
            )
        try:
            with selected.open("rb") as handle:
                raw = tomllib.load(handle)
        except FileNotFoundError as error:
            raise ConfigurationError(
                [ConfigurationIssue(path="$", message="configuration file does not exist")],
                source=selected,
            ) from error
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ConfigurationError(
                [ConfigurationIssue(path="$", message=str(error))], source=selected
            ) from error

    merged = deepcopy(raw)
    for key_path, aliases in _ENV_OVERRIDES:
        for name in aliases:
            if name in values:
                _set_nested(merged, key_path, values[name])
                break
    return _parse_config(merged, source=selected)


def parse_config(
    values: Mapping[str, Any],
    *,
    source: str | Path | None = None,
) -> EchoConfig:
    """Validate an already assembled configuration mapping.

    This is the non-file composition entry point used by the live configuration
    control plane. It applies the exact same schema and aggregated validation as
    :func:`load_config`, without reading a file or process environment.
    """

    if not isinstance(values, Mapping):
        raise ConfigurationError(
            [ConfigurationIssue(path="$", message="configuration must be a table")],
            source=source,
        )
    return _parse_config(deepcopy(dict(values)), source=source)


def _set_nested(target: dict[str, Any], path: tuple[str, ...], value: str) -> None:
    current = target
    for key in path[:-1]:
        child = current.get(key)
        if child is None:
            child = {}
            current[key] = child
        elif not isinstance(child, dict):
            # Preserve an invalid table value so startup validation reports it.
            return
        current = child
    current[path[-1]] = value


class _Reader:
    def __init__(self, raw: Mapping[str, Any]) -> None:
        self.raw = raw
        self.issues: list[ConfigurationIssue] = []

    def section(self, path: str, allowed: set[str]) -> Mapping[str, Any]:
        value: Any = self.raw
        for part in path.split("."):
            if not isinstance(value, Mapping):
                value = None
                break
            value = value.get(part, {})
        if not isinstance(value, Mapping):
            self.issue(path, "must be a table")
            return {}
        for key in value:
            if key not in allowed:
                self.issue(f"{path}.{key}", "unknown configuration key")
        return value

    def issue(self, path: str, message: str) -> None:
        self.issues.append(ConfigurationIssue(path=path, message=message))

    def boolean(self, section: Mapping[str, Any], path: str, default: bool) -> bool:
        value = section.get(path.rsplit(".", 1)[-1], default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {
            "true", "false", "1", "0", "yes", "no", "on", "off"
        }:
            return value.strip().lower() in {"true", "1", "yes", "on"}
        self.issue(path, "must be a boolean")
        return default

    def text(
        self,
        section: Mapping[str, Any],
        path: str,
        default: str,
        *,
        nullable: bool = False,
    ) -> str | None:
        value = section.get(path.rsplit(".", 1)[-1], default)
        if nullable and (value is None or value == ""):
            return None
        if isinstance(value, str) and value.strip():
            return value.strip()
        self.issue(path, "must be a non-empty string")
        return default

    def integer(
        self,
        section: Mapping[str, Any],
        path: str,
        default: int,
        *,
        minimum: int,
        maximum: int | None = None,
    ) -> int:
        value = section.get(path.rsplit(".", 1)[-1], default)
        try:
            parsed = int(value)
            if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                raise ValueError
        except (TypeError, ValueError):
            self.issue(path, "must be an integer")
            return default
        if parsed < minimum or (maximum is not None and parsed > maximum):
            range_text = (
                f"between {minimum} and {maximum}"
                if maximum is not None
                else f"at least {minimum}"
            )
            self.issue(path, f"must be {range_text}")
            return default
        return parsed

    def number(self, section: Mapping[str, Any], path: str, default: float) -> float:
        value = section.get(path.rsplit(".", 1)[-1], default)
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            self.issue(path, "must be a number")
            return default
        if isinstance(value, bool) or not math.isfinite(parsed) or parsed <= 0:
            self.issue(path, "must be a positive number")
            return default
        return parsed


def _parse_config(raw: Mapping[str, Any], *, source: str | Path | None) -> EchoConfig:
    reader = _Reader(raw)
    allowed_top = {"runtime", "logging", "history", "providers", "api", "console"}
    for key in raw:
        if key not in allowed_top:
            reader.issue(key, "unknown top-level configuration table")

    runtime = reader.section("runtime", {"auto_start"})
    logging_section = reader.section("logging", {"level", "format"})
    history = reader.section(
        "history", {"signals", "tasks", "actions", "logs", "errors"}
    )
    providers = reader.section(
        "providers",
        {"mode", "preference", "history_limit", "openrouter", "lan", "offline"},
    )
    openrouter = reader.section(
        "providers.openrouter",
        {"enabled", "api_key", "model", "base_url", "timeout_seconds", "app_url", "app_title"},
    )
    lan = reader.section(
        "providers.lan",
        {"enabled", "base_url", "model", "timeout_seconds", "api_key"},
    )
    offline = reader.section(
        "providers.offline",
        {
            "enabled", "model_path", "model", "executable",
            "request_timeout_seconds", "startup_timeout_seconds",
            "shutdown_timeout_seconds", "port", "extra_args",
        },
    )
    api = reader.section("api", {"enabled", "host", "port"})
    console = reader.section(
        "console",
        {"api_url", "request_timeout_seconds", "poll_interval_seconds", "session_name"},
    )

    level = str(reader.text(logging_section, "logging.level", "INFO")).upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        reader.issue("logging.level", "must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        level = "INFO"
    log_format = str(reader.text(logging_section, "logging.format", "text")).lower()
    if log_format not in {"text", "json"}:
        reader.issue("logging.format", "must be 'text' or 'json'")
        log_format = "text"

    mode_value = str(reader.text(providers, "providers.mode", "auto")).lower()
    try:
        mode = ProviderMode(mode_value)
    except ValueError:
        reader.issue("providers.mode", "must be auto, remote, lan, or offline")
        mode = ProviderMode.AUTO

    preference_value = providers.get(
        "preference", ["remote", "lan", "offline"]
    )
    if isinstance(preference_value, str):
        preference_value = [
            item.strip() for item in preference_value.split(",") if item.strip()
        ]
    try:
        preference = tuple(ProviderSlot(item) for item in preference_value)
    except (TypeError, ValueError):
        reader.issue(
            "providers.preference",
            "must list remote, lan, and offline exactly once",
        )
        preference = (
            ProviderSlot.REMOTE,
            ProviderSlot.LAN,
            ProviderSlot.OFFLINE,
        )
    if len(preference) != 3 or set(preference) != set(ProviderSlot):
        reader.issue(
            "providers.preference",
            "must list remote, lan, and offline exactly once",
        )
        preference = (
            ProviderSlot.REMOTE,
            ProviderSlot.LAN,
            ProviderSlot.OFFLINE,
        )

    openrouter_enabled = reader.boolean(openrouter, "providers.openrouter.enabled", False)
    openrouter_key = reader.text(openrouter, "providers.openrouter.api_key", "", nullable=True)
    openrouter_url = str(
        reader.text(
            openrouter,
            "providers.openrouter.base_url",
            "https://openrouter.ai/api/v1",
        )
    )
    openrouter_app_url = reader.text(openrouter, "providers.openrouter.app_url", "", nullable=True)
    lan_enabled = reader.boolean(lan, "providers.lan.enabled", False)
    lan_url = reader.text(lan, "providers.lan.base_url", "", nullable=True)
    offline_enabled = reader.boolean(offline, "providers.offline.enabled", False)
    model_path_value = reader.text(offline, "providers.offline.model_path", "", nullable=True)

    if openrouter_enabled and not openrouter_key:
        reader.issue(
            "providers.openrouter.api_key",
            "is required when the OpenRouter provider is enabled "
            "(OPENROUTER_API_KEY may supply it)",
        )
    if lan_enabled and not lan_url:
        reader.issue("providers.lan.base_url", "is required when the LAN provider is enabled")
    if offline_enabled and not model_path_value:
        reader.issue(
            "providers.offline.model_path",
            "is required when the offline provider is enabled",
        )
    enabled_for_mode = {
        ProviderMode.REMOTE: openrouter_enabled,
        ProviderMode.LAN: lan_enabled,
        ProviderMode.OFFLINE: offline_enabled,
    }
    if mode is not ProviderMode.AUTO and not enabled_for_mode[mode]:
        section = "openrouter" if mode is ProviderMode.REMOTE else mode.value
        reader.issue(
            f"providers.{section}.enabled",
            f"must be true when providers.mode is '{mode.value}'",
        )
    _validate_url(reader, "providers.openrouter.base_url", openrouter_url)
    if openrouter_app_url:
        _validate_url(reader, "providers.openrouter.app_url", openrouter_app_url)
    if lan_url:
        _validate_url(reader, "providers.lan.base_url", lan_url)

    api_host = str(reader.text(api, "api.host", "127.0.0.1"))
    if (
        "://" in api_host
        or "/" in api_host
        or any(character.isspace() for character in api_host)
    ):
        reader.issue(
            "api.host",
            "must be a host name or IP address without a URL scheme or path",
        )
    console_url = str(
        reader.text(console, "console.api_url", "http://127.0.0.1:8000")
    )
    _validate_url(reader, "console.api_url", console_url)

    extra_args_value = offline.get("extra_args", ())
    if isinstance(extra_args_value, list) and all(
        isinstance(item, str) for item in extra_args_value
    ):
        extra_args = tuple(extra_args_value)
    elif isinstance(extra_args_value, tuple) and all(
        isinstance(item, str) for item in extra_args_value
    ):
        extra_args = extra_args_value
    else:
        reader.issue("providers.offline.extra_args", "must be an array of strings")
        extra_args = ()

    config = EchoConfig(
        runtime=RuntimeConfig(
            auto_start=reader.boolean(runtime, "runtime.auto_start", True)
        ),
        logging=LoggingConfig(level=level, format=log_format),
        history=HistoryConfig(
            **{
                name: reader.integer(
                    history, f"history.{name}", 1000, minimum=1
                )
                for name in ("signals", "tasks", "actions", "logs", "errors")
            }
        ),
        providers=ProviderRoutingConfig(
            mode=mode,
            preference=preference,
            history_limit=reader.integer(
                providers, "providers.history_limit", 100, minimum=1
            ),
            openrouter=OpenRouterSettings(
                enabled=openrouter_enabled,
                api_key=openrouter_key,
                model=str(
                    reader.text(
                        openrouter,
                        "providers.openrouter.model",
                        "openrouter/auto",
                    )
                ),
                base_url=openrouter_url,
                timeout_seconds=reader.number(
                    openrouter,
                    "providers.openrouter.timeout_seconds",
                    60.0,
                ),
                app_url=openrouter_app_url,
                app_title=reader.text(
                    openrouter,
                    "providers.openrouter.app_title",
                    "Echo",
                    nullable=True,
                ),
            ),
            lan=LanProviderSettings(
                enabled=lan_enabled,
                base_url=lan_url,
                model=str(reader.text(lan, "providers.lan.model", "local-model")),
                timeout_seconds=reader.number(lan, "providers.lan.timeout_seconds", 30.0),
                api_key=reader.text(lan, "providers.lan.api_key", "", nullable=True),
            ),
            offline=OfflineProviderSettings(
                enabled=offline_enabled,
                model_path=_resolve_config_path(model_path_value, source),
                model=reader.text(offline, "providers.offline.model", "", nullable=True),
                executable=str(
                    reader.text(
                        offline,
                        "providers.offline.executable",
                        "llama-server",
                    )
                ),
                request_timeout_seconds=reader.number(
                    offline,
                    "providers.offline.request_timeout_seconds",
                    120.0,
                ),
                startup_timeout_seconds=reader.number(
                    offline,
                    "providers.offline.startup_timeout_seconds",
                    120.0,
                ),
                shutdown_timeout_seconds=reader.number(
                    offline,
                    "providers.offline.shutdown_timeout_seconds",
                    10.0,
                ),
                port=reader.integer(
                    offline,
                    "providers.offline.port",
                    0,
                    minimum=0,
                    maximum=65535,
                ),
                extra_args=extra_args,
            ),
        ),
        api=ApiServerConfig(
            enabled=reader.boolean(api, "api.enabled", True),
            host=api_host,
            port=reader.integer(api, "api.port", 8000, minimum=1, maximum=65535),
        ),
        console=ConsoleConfig(
            api_url=console_url.rstrip("/"),
            request_timeout_seconds=reader.number(console, "console.request_timeout_seconds", 3.0),
            poll_interval_seconds=reader.number(console, "console.poll_interval_seconds", 1.0),
            session_name=str(reader.text(console, "console.session_name", "echo-core")),
        ),
    )
    if reader.issues:
        raise ConfigurationError(reader.issues, source=source)
    return config


def _validate_url(reader: _Reader, path: str, value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        reader.issue(path, "must be an absolute http:// or https:// URL")


def _resolve_config_path(
    value: str | None, source: str | Path | None
) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute() and source is not None:
        path = Path(source).expanduser().resolve(strict=False).parent / path
    return path.resolve(strict=False)


class _JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": self.formatTime(record, self.datefmt),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            },
            ensure_ascii=False,
        )
