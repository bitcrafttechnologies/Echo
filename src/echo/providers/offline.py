"""Lazy offline inference with a llama.cpp-compatible backend boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import logging
import os
from pathlib import Path
import shutil
import socket
from time import perf_counter
from typing import Any, Protocol

from echo.providers.base import (
    InferenceRequest,
    InferenceResult,
    ProviderCapability,
    ProviderError,
    ProviderHealthResult,
    ProviderHealthStatus,
    ProviderMetadata,
    ProviderTiming,
    ProviderUnavailableError,
)
from echo.providers.lan import LanInferenceConfig, LanInferenceProvider


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class OfflineModelState(StrEnum):
    UNCONFIGURED = "unconfigured"
    UNLOADED = "unloaded"
    LOADING = "loading"
    LOADED = "loaded"
    UNLOADING = "unloading"
    ERROR = "error"


@dataclass(slots=True, kw_only=True, frozen=True)
class OfflineInferenceConfig:
    """Explicit local model and llama-server process configuration."""

    model_path: Path | str | None = None
    model: str | None = None
    executable: str = "llama-server"
    request_timeout_seconds: float = 120.0
    startup_timeout_seconds: float = 120.0
    shutdown_timeout_seconds: float = 10.0
    port: int = 0
    extra_args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        model_path = (
            Path(self.model_path).expanduser().resolve(strict=False)
            if self.model_path is not None
            else None
        )
        model = self.model.strip() if self.model else None
        executable = self.executable.strip() if self.executable else ""
        if not executable:
            raise ValueError("offline inference executable must not be empty")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self.startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        if self.shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be positive")
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise ValueError("offline inference port must be an integer")
        if not 0 <= self.port <= 65535:
            raise ValueError("offline inference port must be between 0 and 65535")
        if model is None and model_path is not None:
            model = model_path.name
        object.__setattr__(self, "model_path", model_path)
        object.__setattr__(self, "model", model or "local-model")
        object.__setattr__(self, "executable", executable)
        object.__setattr__(self, "extra_args", tuple(self.extra_args))

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> OfflineInferenceConfig:
        values = os.environ if environ is None else environ
        return cls(
            model_path=_first_present(
                values,
                "OFFLINE_MODEL_PATH",
                "LLAMA_CPP_MODEL_PATH",
            ),
            model=_first_present(
                values,
                "OFFLINE_MODEL",
                "LLAMA_CPP_OFFLINE_MODEL",
            ),
            executable=values.get("LLAMA_CPP_SERVER", "llama-server"),
            request_timeout_seconds=_float_env(
                values, "OFFLINE_REQUEST_TIMEOUT_SECONDS", 120.0
            ),
            startup_timeout_seconds=_float_env(
                values, "OFFLINE_STARTUP_TIMEOUT_SECONDS", 120.0
            ),
            shutdown_timeout_seconds=_float_env(
                values, "OFFLINE_SHUTDOWN_TIMEOUT_SECONDS", 10.0
            ),
            port=_int_env(values, "OFFLINE_SERVER_PORT", 0),
        )


@dataclass(slots=True, kw_only=True, frozen=True)
class OfflineBackendResult:
    output: str
    model: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True, frozen=True)
class OfflineBackendHealth:
    status: ProviderHealthStatus
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)


class OfflineInferenceBackend(Protocol):
    @property
    def loaded(self) -> bool: ...

    async def load(self, config: OfflineInferenceConfig) -> None: ...

    async def infer(self, request: InferenceRequest) -> OfflineBackendResult: ...

    async def health(self) -> OfflineBackendHealth: ...

    async def unload(self) -> None: ...


@dataclass(slots=True, kw_only=True, frozen=True)
class OfflineProviderStatus:
    state: OfflineModelState
    loaded: bool
    model: str
    model_path: str | None
    last_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "loaded": self.loaded,
            "model": self.model,
            "model_path": self.model_path,
            "last_error": self.last_error,
        }


class OfflineInferenceError(ProviderError):
    code = "offline_inference_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


class OfflineConfigurationError(
    OfflineInferenceError, ProviderUnavailableError
):
    code = "offline_inference_not_configured"


class OfflineInitializationError(
    OfflineInferenceError, ProviderUnavailableError
):
    code = "offline_inference_initialization_failed"


class OfflineExecutionError(OfflineInferenceError, ProviderUnavailableError):
    code = "offline_inference_execution_failed"


class OfflineUnloadError(OfflineInferenceError):
    code = "offline_inference_unload_failed"


class OfflineInferenceProvider:
    """Lazy local fallback that loads only when inference reaches this slot."""

    def __init__(
        self,
        config: OfflineInferenceConfig | None = None,
        *,
        backend: OfflineInferenceBackend | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if config is not None and environ is not None:
            raise ValueError("config and environ cannot be supplied together")
        self._config = config or OfflineInferenceConfig.from_env(environ)
        self._backend = backend or LlamaCppServerBackend()
        self._state = (
            OfflineModelState.UNCONFIGURED
            if self._config.model_path is None
            else OfflineModelState.UNLOADED
        )
        self._last_error: str | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._inference_lock = asyncio.Lock()
        attributes: dict[str, Any] = {
            "backend": "llama.cpp-server",
            "lazy": True,
            "offline": True,
        }
        if self._config.model_path is not None:
            attributes["model_file"] = self._config.model_path.name
        self._metadata = ProviderMetadata(
            provider_id="offline",
            name="Offline Local Inference",
            model=self._config.model,
            capabilities=(ProviderCapability.INFER,),
            attributes=attributes,
        )

    @property
    def metadata(self) -> ProviderMetadata:
        return self._metadata

    @property
    def loaded(self) -> bool:
        return self._state is OfflineModelState.LOADED and self._backend.loaded

    def status(self) -> OfflineProviderStatus:
        return OfflineProviderStatus(
            state=self._state,
            loaded=self.loaded,
            model=self._config.model,
            model_path=(
                str(self._config.model_path)
                if self._config.model_path is not None
                else None
            ),
            last_error=self._last_error,
        )

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        if not isinstance(request, InferenceRequest):
            raise TypeError("request must be an InferenceRequest")
        async with self._inference_lock:
            return await self._infer_once(request)

    async def _infer_once(self, request: InferenceRequest) -> InferenceResult:
        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        await self._ensure_loaded()
        try:
            backend_result = await self._backend.infer(request)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message = f"offline inference failed: {error}"
            self._last_error = message
            timing = _timing(started_at, started)
            self._log_failure(request.request_id, timing, "execution")
            raise OfflineExecutionError(message) from error

        timing = _timing(started_at, started)
        provider_metadata = ProviderMetadata(
            provider_id=self.metadata.provider_id,
            name=self.metadata.name,
            model=backend_result.model,
            capabilities=self.metadata.capabilities,
            attributes=self.metadata.attributes,
        )
        logger.info(
            "offline inference completed",
            extra={
                "provider_id": self.metadata.provider_id,
                "model": backend_result.model,
                "request_id": request.request_id,
                "duration_ms": timing.duration_ms,
            },
        )
        return InferenceResult(
            request_id=request.request_id,
            output=backend_result.output,
            provider_metadata=provider_metadata,
            timing=timing,
            metadata={
                "configured_model": self._config.model,
                "lazy_loaded": True,
                **dict(backend_result.metadata),
            },
        )

    async def health(self) -> ProviderHealthResult:
        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        if self._config.model_path is None:
            return ProviderHealthResult(
                status=ProviderHealthStatus.UNAVAILABLE,
                provider_metadata=self.metadata,
                timing=_timing(started_at, started),
                message="offline model path is not configured",
                details=self.status().to_dict(),
            )
        if not self.loaded:
            path_exists = self._config.model_path.is_file()
            if self._state is OfflineModelState.ERROR:
                return ProviderHealthResult(
                    status=ProviderHealthStatus.UNAVAILABLE,
                    provider_metadata=self.metadata,
                    timing=_timing(started_at, started),
                    message=self._last_error or "offline model is in an error state",
                    details=self.status().to_dict(),
                )
            return ProviderHealthResult(
                status=(
                    ProviderHealthStatus.DEGRADED
                    if path_exists
                    else ProviderHealthStatus.UNAVAILABLE
                ),
                provider_metadata=self.metadata,
                timing=_timing(started_at, started),
                message=(
                    "offline model is configured and lazily unloaded"
                    if path_exists
                    else "offline model file is unavailable"
                ),
                details=self.status().to_dict(),
            )

        try:
            backend_health = await self._backend.health()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message = f"offline backend health check failed: {error}"
            self._last_error = message
            return ProviderHealthResult(
                status=ProviderHealthStatus.UNAVAILABLE,
                provider_metadata=self.metadata,
                timing=_timing(started_at, started),
                message=message,
                details=self.status().to_dict(),
            )
        return ProviderHealthResult(
            status=backend_health.status,
            provider_metadata=self.metadata,
            timing=_timing(started_at, started),
            message=backend_health.message,
            details={
                **self.status().to_dict(),
                "backend": dict(backend_health.details),
            },
        )

    async def unload(self) -> OfflineProviderStatus:
        async with self._inference_lock:
            return await self._unload_when_idle()

    async def _unload_when_idle(self) -> OfflineProviderStatus:
        async with self._lifecycle_lock:
            if self._config.model_path is None:
                self._state = OfflineModelState.UNCONFIGURED
                return self.status()
            if not self._backend.loaded:
                self._state = OfflineModelState.UNLOADED
                self._last_error = None
                return self.status()
            self._state = OfflineModelState.UNLOADING
            try:
                await self._backend.unload()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._state = OfflineModelState.ERROR
                self._last_error = f"offline model unload failed: {error}"
                raise OfflineUnloadError(self._last_error) from error
            self._state = OfflineModelState.UNLOADED
            self._last_error = None
            logger.info(
                "offline model unloaded",
                extra={
                    "provider_id": self.metadata.provider_id,
                    "model": self.metadata.model,
                },
            )
            return self.status()

    async def _ensure_loaded(self) -> None:
        if self._config.model_path is None:
            raise OfflineConfigurationError(
                "offline model path is not configured"
            )
        async with self._lifecycle_lock:
            if self.loaded:
                return
            self._state = OfflineModelState.LOADING
            self._last_error = None
            try:
                await self._backend.load(self._config)
            except asyncio.CancelledError:
                self._state = OfflineModelState.UNLOADED
                raise
            except Exception as error:
                self._state = OfflineModelState.ERROR
                self._last_error = f"offline model initialization failed: {error}"
                raise OfflineInitializationError(self._last_error) from error
            if not self._backend.loaded:
                self._state = OfflineModelState.ERROR
                self._last_error = "offline backend did not enter loaded state"
                raise OfflineInitializationError(self._last_error)
            self._state = OfflineModelState.LOADED
            logger.info(
                "offline model loaded",
                extra={
                    "provider_id": self.metadata.provider_id,
                    "model": self.metadata.model,
                },
            )

    def _log_failure(
        self,
        request_id: str,
        timing: ProviderTiming,
        stage: str,
    ) -> None:
        logger.warning(
            "offline inference failed",
            extra={
                "provider_id": self.metadata.provider_id,
                "model": self.metadata.model,
                "request_id": request_id,
                "duration_ms": timing.duration_ms,
                "stage": stage,
            },
        )


class LlamaCppServerBackend:
    """Lazy child-process adapter for llama-server's OpenAI-compatible API."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._client: LanInferenceProvider | None = None
        self._config: OfflineInferenceConfig | None = None

    @property
    def loaded(self) -> bool:
        return (
            self._process is not None
            and self._process.returncode is None
            and self._client is not None
        )

    async def load(self, config: OfflineInferenceConfig) -> None:
        if self.loaded:
            return
        if config.model_path is None:
            raise OfflineConfigurationError("offline model path is not configured")
        if not config.model_path.is_file():
            raise OfflineInitializationError(
                f"offline model file does not exist: {config.model_path}"
            )
        executable = shutil.which(config.executable)
        if executable is None:
            raise OfflineInitializationError(
                f"llama-server executable is unavailable: {config.executable}"
            )
        port = config.port or _available_loopback_port()
        command = [
            executable,
            "-m",
            str(config.model_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            *config.extra_args,
        ]
        self._config = config
        self._process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._client = LanInferenceProvider(
            LanInferenceConfig(
                base_url=f"http://127.0.0.1:{port}",
                model=config.model,
                timeout_seconds=config.request_timeout_seconds,
            )
        )
        deadline = asyncio.get_running_loop().time() + config.startup_timeout_seconds
        try:
            while True:
                if self._process.returncode is not None:
                    raise OfflineInitializationError(
                        "llama-server exited before becoming ready "
                        f"with code {self._process.returncode}"
                    )
                health = await self._client.health()
                if health.status is ProviderHealthStatus.HEALTHY:
                    return
                if asyncio.get_running_loop().time() >= deadline:
                    raise OfflineInitializationError(
                        "llama-server did not become ready before startup timeout"
                    )
                await asyncio.sleep(0.1)
        except BaseException:
            await self._stop_process(config.shutdown_timeout_seconds)
            raise

    async def infer(self, request: InferenceRequest) -> OfflineBackendResult:
        if not self.loaded or self._client is None:
            raise OfflineExecutionError("llama-server is not loaded")
        result = await self._client.infer(request)
        return OfflineBackendResult(
            output=result.output,
            model=result.provider.model or self._config.model,
            metadata=result.metadata,
        )

    async def health(self) -> OfflineBackendHealth:
        if not self.loaded or self._client is None:
            return OfflineBackendHealth(
                status=ProviderHealthStatus.UNAVAILABLE,
                message="llama-server is not loaded",
                details={"loaded": False},
            )
        health = await self._client.health()
        return OfflineBackendHealth(
            status=health.status,
            message=health.message or "llama-server health",
            details=health.details,
        )

    async def unload(self) -> None:
        timeout = (
            self._config.shutdown_timeout_seconds
            if self._config is not None
            else 10.0
        )
        await self._stop_process(timeout)

    async def _stop_process(self, timeout_seconds: float) -> None:
        process = self._process
        self._client = None
        self._process = None
        self._config = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _first_present(
    values: Mapping[str, str],
    *names: str,
) -> str | None:
    for name in names:
        value = values.get(name)
        if value is not None and value.strip():
            return value
    return None


def _float_env(
    values: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    value = values.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error


def _int_env(
    values: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    value = values.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _timing(started_at: datetime, started_counter: float) -> ProviderTiming:
    return ProviderTiming(
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
        duration_ms=(perf_counter() - started_counter) * 1000,
    )
