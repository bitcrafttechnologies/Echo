"""Configured Echo Core process host used by the root start script."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import os
from pathlib import Path
from typing import Any

from echo.config import ConfigurationError, DEFAULT_CONFIG_PATH, EchoConfig, load_config
from echo.config_reload import RuntimeConfigurationManager
from echo.core.entity import Entity
from echo.core.runtime import Runtime
from echo.core.signal import Signal
from echo.entity.context import CharacterContextBuilder, CharacterContextRequest
from echo.providers import InferenceRequest, ProviderRouter
from echo.restart import GracefulRestartCoordinator, RestartTarget
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

    context_builder = CharacterContextBuilder()

    @entity.on("UserMessage")
    async def respond_to_user(signal: Signal):
        text = signal.payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("UserMessage payload.text must be a non-empty string")
        subject_id = signal.metadata.get("subject_id")
        if not isinstance(subject_id, str):
            subject_id = None
        environment = signal.metadata.get("environment", {})
        if not isinstance(environment, Mapping):
            environment = {}
        character_context = context_builder.build(
            entity,
            CharacterContextRequest(
                situation=text.strip(),
                subject_id=subject_id,
                environment=environment,
            ),
        )
        result = await provider_router.infer(
            InferenceRequest(
                prompt=text.strip(),
                context=character_context.to_dict(),
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


def _clone_entity_generation(entity: Entity) -> Entity:
    """Reconstruct one Entity while retaining provider-independent character."""

    return entity.copy_for_restart()


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
    coordinator: GracefulRestartCoordinator

    def build_fresh_generation() -> RestartTarget:
        current_entity = coordinator.runtime.get_entity("bit")
        if current_entity is None:
            raise RuntimeError("Bit is missing from the current Runtime")
        current_manager = next(
            (
                resource
                for resource in coordinator.resources
                if isinstance(resource, RuntimeConfigurationManager)
            ),
            manager,
        )
        generation_config = current_manager.active_config
        fresh_entity = _clone_entity_generation(current_entity)
        fresh_router = generation_config.create_provider_router()
        register_user_message_handler(fresh_entity, fresh_router)
        fresh_runtime = generation_config.create_runtime([fresh_entity])
        fresh_manager = RuntimeConfigurationManager(
            generation_config,
            fresh_runtime,
            provider_router=fresh_router,
            config_path=config_path,
        )
        return RestartTarget(
            runtime=fresh_runtime,
            resources=(fresh_router, fresh_manager),
        )

    coordinator = GracefulRestartCoordinator(
        runtime,
        build_fresh_generation,
        resources=(router, manager),
    )
    service = RuntimeService(
        runtime,
        provider_router=router,
        configuration_manager=manager,
        restart_coordinator=coordinator,
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
        app, config, runtime, service = build_host(arguments.config)
    except (ConfigurationError, RuntimeError) as error:
        parser.exit(1, f"Echo could not start: {error}\n")
    try:
        uvicorn.run(app, host=config.api.host, port=config.api.port)
    finally:
        runtime.stop()
        if service.runtime is not runtime:
            service.runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
