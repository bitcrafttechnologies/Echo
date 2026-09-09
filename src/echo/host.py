"""Configured Echo Core process host used by the root start script."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from echo.config import ConfigurationError, DEFAULT_CONFIG_PATH, EchoConfig, load_config
from echo.config_reload import RuntimeConfigurationManager
from echo.core.entity import Entity
from echo.core.runtime import Runtime
from echo.core.signal import Signal
from echo.providers import InferenceRequest, ProviderRouter
from echo.runtime_service import RuntimeService


def selected_config_path(path: str | Path | None = None) -> Path | None:
    if path is not None:
        return Path(path)
    if os.environ.get("ECHO_CONFIG"):
        return Path(os.environ["ECHO_CONFIG"])
    if DEFAULT_CONFIG_PATH.is_file():
        return DEFAULT_CONFIG_PATH
    return None


def register_user_message_handler(
    entity: Entity,
    provider_router: ProviderRouter,
) -> None:
    """Route Console chat Signals through inference and record the reply."""

    @entity.on("UserMessage")
    async def respond_to_user(signal: Signal):
        text = signal.payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("UserMessage payload.text must be a non-empty string")
        result = await provider_router.infer(
            InferenceRequest(
                prompt=text.strip(),
                metadata={
                    "entity_id": entity.id,
                    "signal_id": signal.id,
                    "source": signal.source,
                },
            )
        )
        return await entity.action(
            "EchoResponse",
            text=result.output,
            request_id=result.request_id,
            provider=result.provider.to_dict(),
            timing=result.timing.to_dict(),
        )


def build_host(
    path: str | Path | None = None,
) -> tuple[Any, EchoConfig, Runtime, RuntimeService]:
    """Build one configured Runtime and its HTTP management application."""

    from echo.adapters.fastapi import create_app

    config_path = selected_config_path(path)
    config = load_config(config_path)
    config.configure_logging()
    if not config.api.enabled:
        raise RuntimeError("Echo API is disabled by configuration")
    entity = Entity("bit")
    router = config.create_provider_router()
    register_user_message_handler(entity, router)
    runtime = config.create_runtime([entity])
    manager = RuntimeConfigurationManager(
        config,
        runtime,
        provider_router=router,
        config_path=config_path,
    )
    service = RuntimeService(
        runtime,
        provider_router=router,
        configuration_manager=manager,
    )
    return create_app(service), config, runtime, service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start Echo Core and its API")
    parser.add_argument("--config", help="existing TOML configuration path")
    arguments = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError as error:
        raise SystemExit("uvicorn is required; run ./install.sh") from error

    try:
        app, config, runtime, _service = build_host(arguments.config)
    except (ConfigurationError, RuntimeError) as error:
        parser.exit(1, f"Echo could not start: {error}\n")
    try:
        uvicorn.run(app, host=config.api.host, port=config.api.port)
    finally:
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
