"""Explicit, transactional live configuration reload for Phase 6B."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum, StrEnum
from pathlib import Path
from threading import RLock
from typing import Any

from echo.config import (
    ConfigurationError,
    ConfigurationIssue,
    EchoConfig,
    load_config,
    parse_config,
)
from echo.core.runtime import Runtime
from echo.providers import ProviderRouter


class ConfigurationChangeClass(StrEnum):
    LIVE_SAFE = "live_safe"
    RESTART_REQUIRED = "restart_required"


class ConfigurationReloadStatus(StrEnum):
    APPLIED = "applied"
    NO_CHANGE = "no_change"
    RESTART_REQUIRED = "restart_required"


@dataclass(slots=True, kw_only=True, frozen=True)
class ConfigurationChange:
    path: str
    classification: ConfigurationChangeClass
    previous: Any
    requested: Any
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "classification": self.classification.value,
            "previous": _display_value(self.previous),
            "requested": _display_value(self.requested),
            "reason": self.reason,
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class ConfigurationReloadResult:
    status: ConfigurationReloadStatus
    applied: bool
    source: str | None
    changes: tuple[ConfigurationChange, ...]
    completed_at: datetime

    @property
    def live_safe_changes(self) -> tuple[ConfigurationChange, ...]:
        return tuple(
            change
            for change in self.changes
            if change.classification is ConfigurationChangeClass.LIVE_SAFE
        )

    @property
    def restart_required_changes(self) -> tuple[ConfigurationChange, ...]:
        return tuple(
            change
            for change in self.changes
            if change.classification
            is ConfigurationChangeClass.RESTART_REQUIRED
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "applied": self.applied,
            "source": self.source,
            "changes": [change.to_dict() for change in self.changes],
            "live_safe_changes": [
                change.to_dict() for change in self.live_safe_changes
            ],
            "restart_required_changes": [
                change.to_dict() for change in self.restart_required_changes
            ],
            "completed_at": self.completed_at.isoformat(),
        }


class ConfigurationReloadApplyError(RuntimeError):
    code = "configuration_reload_failed"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(slots=True, kw_only=True, frozen=True)
class ConfigurationField:
    path: str
    value: Any
    classification: ConfigurationChangeClass
    editable: bool
    secret: bool
    configured: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "value": None if self.secret else _display_value(self.value),
            "classification": (
                "live_editable"
                if self.editable
                else ConfigurationChangeClass.RESTART_REQUIRED.value
            ),
            "editable": self.editable,
            "secret": self.secret,
            "configured": self.configured,
            "reason": self.reason,
        }


@dataclass(slots=True, kw_only=True, frozen=True)
class ConfigurationInspectionResult:
    source: str | None
    fields: tuple[ConfigurationField, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "fields": [field.to_dict() for field in self.fields],
        }


_LIVE_SAFE_REASONS = {
    "logging.level": "Python logging level can change in place",
    "logging.format": "Python logging formatter can change in place",
    "providers.mode": "routing mode is selected per inference request",
    "providers.preference": "automatic routing order is selected per request",
    "providers.history_limit": "router retention can be resized in place",
}
_SECRET_PATHS = {
    "providers.openrouter.api_key",
    "providers.lan.api_key",
}


class RuntimeConfigurationManager:
    """Own the active config and perform explicit all-or-nothing reloads."""

    def __init__(
        self,
        active_config: EchoConfig,
        runtime: Runtime,
        *,
        provider_router: ProviderRouter | None = None,
        config_path: str | Path | None = None,
    ) -> None:
        if not isinstance(active_config, EchoConfig):
            raise TypeError("active_config must be an EchoConfig")
        if not isinstance(runtime, Runtime):
            raise TypeError("runtime must be a Runtime")
        if provider_router is not None and not isinstance(
            provider_router, ProviderRouter
        ):
            raise TypeError("provider_router must be a ProviderRouter")
        self._active_config = active_config
        self._runtime = runtime
        self._provider_router = provider_router
        self._config_path = Path(config_path) if config_path is not None else None
        self._lock = RLock()

    @property
    def active_config(self) -> EchoConfig:
        return self._active_config

    @property
    def runtime(self) -> Runtime:
        return self._runtime

    @property
    def provider_router(self) -> ProviderRouter | None:
        return self._provider_router

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    def inspect(self) -> ConfigurationInspectionResult:
        """Return the effective configuration without exposing secret values."""

        with self._lock:
            return self._inspect_unlocked()

    def _inspect_unlocked(self) -> ConfigurationInspectionResult:

        fields_result: list[ConfigurationField] = []
        for path, value in sorted(_flatten(self._active_config).items()):
            classification, reason = self._classification(path)
            secret = path in _SECRET_PATHS
            fields_result.append(
                ConfigurationField(
                    path=path,
                    value=None if secret else value,
                    classification=classification,
                    editable=(
                        classification is ConfigurationChangeClass.LIVE_SAFE
                    ),
                    secret=secret,
                    configured=value is not None,
                    reason=("secret value is hidden" if secret else reason),
                )
            )
        return ConfigurationInspectionResult(
            source=str(self._config_path) if self._config_path else None,
            fields=tuple(fields_result),
        )

    def update(
        self,
        values: Mapping[str, Any],
        *,
        source: str = "management",
    ) -> ConfigurationReloadResult:
        """Validate and apply approved dotted-path live-safe values."""

        with self._lock:
            return self._update_unlocked(values, source=source)

    def _update_unlocked(
        self,
        values: Mapping[str, Any],
        *,
        source: str,
    ) -> ConfigurationReloadResult:

        if not isinstance(values, Mapping) or not values:
            raise ConfigurationError(
                [
                    ConfigurationIssue(
                        path="values",
                        message="must contain at least one dotted configuration field",
                    )
                ],
                source=source,
            )
        active_values = _flatten(self._active_config)
        issues: list[ConfigurationIssue] = []
        for path in values:
            if not isinstance(path, str) or path not in active_values:
                issues.append(
                    ConfigurationIssue(
                        path=str(path), message="unknown configuration field"
                    )
                )
                continue
            classification, _ = self._classification(path)
            if classification is not ConfigurationChangeClass.LIVE_SAFE:
                issues.append(
                    ConfigurationIssue(
                        path=path,
                        message="requires restart and cannot be changed live",
                    )
                )
        if issues:
            error = ConfigurationError(issues, source=source)
            self._audit_invalid(error, source)
            raise error

        raw = _nested(self._active_config)
        for path, value in values.items():
            _set_dotted(raw, path, value)
        try:
            candidate = parse_config(raw, source=self._config_path)
        except ConfigurationError as error:
            self._audit_invalid(error, source)
            raise
        return self.reload_config(candidate, source=source)

    def reload(
        self,
        path: str | Path | None = None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> ConfigurationReloadResult:
        """Explicitly load, validate, classify, and conditionally apply TOML."""

        selected_path = path if path is not None else self._config_path
        source = str(selected_path) if selected_path is not None else None
        try:
            candidate = load_config(selected_path, environ=environ)
        except ConfigurationError as error:
            self._audit_invalid(error, source)
            raise
        return self.reload_config(candidate, source=source)

    def reload_config(
        self,
        candidate: EchoConfig,
        *,
        source: str | Path | None = None,
    ) -> ConfigurationReloadResult:
        """Explicitly apply an already validated typed configuration."""

        if not isinstance(candidate, EchoConfig):
            raise TypeError("candidate must be an EchoConfig")
        source_text = str(source) if source is not None else None
        with self._lock:
            changes = self._changes(candidate)
            restart_required = tuple(
                change
                for change in changes
                if change.classification
                is ConfigurationChangeClass.RESTART_REQUIRED
            )
            if restart_required:
                result = self._result(
                    ConfigurationReloadStatus.RESTART_REQUIRED,
                    applied=False,
                    source=source_text,
                    changes=changes,
                )
                self._audit(result)
                return result

            previous = self._active_config
            try:
                self._apply_live_safe(candidate)
            except Exception as error:
                self._restore_live_safe(previous)
                self._runtime.audit_configuration_reload(
                    {
                        "outcome": "failed",
                        "source": source_text,
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
                raise ConfigurationReloadApplyError(
                    "validated configuration could not be applied; active "
                    "live-safe settings were restored"
                ) from error

            self._active_config = candidate
            result = self._result(
                (
                    ConfigurationReloadStatus.APPLIED
                    if changes
                    else ConfigurationReloadStatus.NO_CHANGE
                ),
                applied=True,
                source=source_text,
                changes=changes,
            )
            self._audit(result)
            return result

    def _changes(self, candidate: EchoConfig) -> tuple[ConfigurationChange, ...]:
        active_values = _flatten(self._active_config)
        candidate_values = _flatten(candidate)
        changes: list[ConfigurationChange] = []
        for path in sorted(active_values):
            previous = active_values[path]
            requested = candidate_values[path]
            if previous == requested:
                continue
            classification, reason = self._classification(path)
            if path in _SECRET_PATHS:
                previous = requested = "<redacted>"
            changes.append(
                ConfigurationChange(
                    path=path,
                    classification=classification,
                    previous=previous,
                    requested=requested,
                    reason=reason,
                )
            )
        return tuple(changes)

    def _classification(
        self, path: str
    ) -> tuple[ConfigurationChangeClass, str]:
        classification, reason = _classify(path)
        if (
            classification is ConfigurationChangeClass.LIVE_SAFE
            and path.startswith("providers.")
            and self._provider_router is None
        ):
            return (
                ConfigurationChangeClass.RESTART_REQUIRED,
                "no live ProviderRouter is attached",
            )
        return classification, reason

    def _audit_invalid(
        self, error: ConfigurationError, source: str | None
    ) -> None:
        self._runtime.audit_configuration_reload(
            {
                "outcome": "invalid",
                "source": error.source or source,
                "issues": [issue.to_dict() for issue in error.issues],
            }
        )

    def _apply_live_safe(self, candidate: EchoConfig) -> None:
        router = self._provider_router
        if router is not None:
            router.set_mode(candidate.providers.mode)
            router.set_preference(candidate.providers.preference)
            router.resize_history(candidate.providers.history_limit)
        candidate.configure_logging()
        history = candidate.history
        self._runtime.resize_histories(
            signals=history.signals,
            tasks=history.tasks,
            actions=history.actions,
            logs=history.logs,
            errors=history.errors,
        )

    def _restore_live_safe(self, config: EchoConfig) -> None:
        router = self._provider_router
        if router is not None:
            router.set_mode(config.providers.mode)
            router.set_preference(config.providers.preference)
            router.resize_history(config.providers.history_limit)
        config.configure_logging()
        history = config.history
        self._runtime.resize_histories(
            signals=history.signals,
            tasks=history.tasks,
            actions=history.actions,
            logs=history.logs,
            errors=history.errors,
        )

    def _audit(self, result: ConfigurationReloadResult) -> None:
        self._runtime.audit_configuration_reload(
            {
                "outcome": result.status.value,
                "applied": result.applied,
                "source": result.source,
                "changes": [change.to_dict() for change in result.changes],
                "restart_required": [
                    change.path for change in result.restart_required_changes
                ],
            }
        )

    @staticmethod
    def _result(
        status: ConfigurationReloadStatus,
        *,
        applied: bool,
        source: str | None,
        changes: tuple[ConfigurationChange, ...],
    ) -> ConfigurationReloadResult:
        return ConfigurationReloadResult(
            status=status,
            applied=applied,
            source=source,
            changes=changes,
            completed_at=datetime.now(timezone.utc),
        )


def _classify(path: str) -> tuple[ConfigurationChangeClass, str]:
    if path.startswith("history."):
        return (
            ConfigurationChangeClass.LIVE_SAFE,
            "bounded retention can be resized in place",
        )
    if path in _LIVE_SAFE_REASONS:
        return ConfigurationChangeClass.LIVE_SAFE, _LIVE_SAFE_REASONS[path]
    return (
        ConfigurationChangeClass.RESTART_REQUIRED,
        "the setting owns startup construction or external connectivity",
    )


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        flattened: dict[str, Any] = {}
        for item in fields(value):
            child_prefix = f"{prefix}.{item.name}" if prefix else item.name
            flattened.update(_flatten(getattr(value, item.name), child_prefix))
        return flattened
    return {prefix: value}


def _display_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_display_value(item) for item in value]
    return value


def _nested(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _nested(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_nested(item) for item in value]
    return deepcopy(value)


def _set_dotted(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = target
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = deepcopy(value)
