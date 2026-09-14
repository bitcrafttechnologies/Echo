"""Configured Echo Core process host used by the root start script."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from echo.config import ConfigurationError, DEFAULT_CONFIG_PATH, EchoConfig, load_config
from echo.config_reload import RuntimeConfigurationManager
from echo.core.entity import Entity
from echo.core.runtime import Runtime
from echo.core.signal import Signal
from echo.entity.context import CharacterContextBuilder, CharacterContextRequest
from echo.entity.config import EntitySeedError, load_entity_seed
from echo.entity.memory import WorkingMemory
from echo.entity.memory_store import MemoryService, user_statement_candidates
from echo.providers import InferenceRequest, ProviderRouter
from echo.restart import GracefulRestartCoordinator, RestartTarget
from echo.runtime_service import RuntimeService
from echo.state import SQLiteMemoryRepository, SQLiteStateStore


logger = logging.getLogger(__name__)


def _entity_with_persistence(seed: Any, config: EchoConfig) -> tuple[Entity, tuple[object, ...]]:
    if not config.persistence.enabled:
        return seed.create_entity(), ()
    database = config.persistence.database_path
    database.parent.mkdir(parents=True, exist_ok=True)
    state_store = SQLiteStateStore(database)
    try:
        memory_repository = SQLiteMemoryRepository(database)
    except Exception:
        state_store.close()
        raise
    entity = seed.create_entity(
        state_store=state_store,
        memory_service=MemoryService(seed.identity.entity_id, memory_repository),
    )
    return entity, (state_store, memory_repository)


def selected_config_path(path: str | Path | None = None) -> Path | None:
    if path is not None:
        return Path(path)
    if os.environ.get("ECHO_CONFIG"):
        return Path(os.environ["ECHO_CONFIG"])
    if DEFAULT_CONFIG_PATH.is_file():
        return DEFAULT_CONFIG_PATH
    return None


def selected_entity_seed_path(
    entity_id: str,
    *,
    config_path: str | Path | None = None,
) -> Path:
    """Resolve project-level Entity seeds from source or an installed host."""

    candidates: list[Path] = []
    configured_root = os.environ.get("ECHO_ENTITY_ROOT")
    if configured_root:
        candidates.append(Path(configured_root) / entity_id)
    if config_path is not None:
        candidates.append(
            Path(config_path).resolve().parent / "entities" / entity_id
        )
    candidates.extend(
        (
            Path.cwd() / "entities" / entity_id,
            Path(__file__).resolve().parents[2] / "entities" / entity_id,
        )
    )
    checked: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in checked:
            continue
        checked.append(resolved)
        if resolved.is_dir():
            return resolved
    locations = ", ".join(str(candidate) for candidate in checked)
    raise EntitySeedError(
        f"cannot locate Entity seed '{entity_id}'; checked: {locations}"
    )


def register_user_message_handler(
    entity: Entity,
    provider_router: ProviderRouter,
    *,
    character_guidance: str | None = None,
    node_host: Any | None = None,
) -> None:
    """Route Console chat Signals through inference and record the reply."""

    context_builder = CharacterContextBuilder()
    conversation_focus = "conversation"
    last_medulla_invocations: list[dict[str, Any]] = []

    @entity.on("UserMessage")
    async def respond_to_user(signal: Signal):
        nonlocal conversation_focus, last_medulla_invocations
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
        medulla_inventory: dict[str, Any] | None = None
        medulla_invocations: list[dict[str, Any]] = []
        inventory_requested = False
        reuse_previous = False
        if node_host is not None:
            medulla_inventory = _medulla_inventory(node_host)
            inventory_requested = _requests_capability_inventory(
                text.strip(), conversation_focus
            )
            reuse_previous = _requests_raw_medulla_result(text.strip())
            planned_actions = () if inventory_requested or reuse_previous else _medulla_actions_for_prompt(
                text.strip(), signal.metadata, inventory=medulla_inventory
            )
            if reuse_previous:
                medulla_invocations = [dict(item) for item in last_medulla_invocations]
            for action_type, parameters in planned_actions:
                invocation_id = str(uuid4())
                capability = next(
                    (
                        item
                        for item in medulla_inventory["capabilities"]
                        if item["name"] == action_type and item["available"]
                    ),
                    None,
                )
                action = await entity.begin_action(action_type, **parameters)
                try:
                    result = await node_host.execute_capability(action)
                    evidence = _medulla_invocation_evidence(
                        invocation_id, action, result, capability=capability
                    )
                    medulla_invocations.append(evidence)
                    if entity.runtime is not None:
                        if evidence["status"] == "completed":
                            entity.runtime.complete_action(
                                action.id, result=evidence
                            )
                        else:
                            entity.runtime.fail_action(
                                action.id, str(evidence["error"])
                            )
                except Exception as error:
                    evidence = {
                        "invocation_id": invocation_id,
                        "action_id": action.id,
                        "capability_id": capability["capability_id"] if capability else None,
                        "capability": action.type,
                        "provider_id": capability["provider_id"] if capability else None,
                        "node_id": capability["node_id"] if capability else None,
                        "status": "failed",
                        "result": None,
                        "error": str(error),
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                    }
                    medulla_invocations.append(evidence)
                    if entity.runtime is not None:
                        entity.runtime.fail_action(action.id, error)
            if planned_actions:
                last_medulla_invocations = [dict(item) for item in medulla_invocations]
                conversation_focus = (
                    planned_actions[0][0]
                    if len(planned_actions) == 1
                    else "medulla.invocations"
                )
            elif inventory_requested:
                conversation_focus = "medulla.capability_inventory"
            elif reuse_previous:
                conversation_focus = "medulla.raw_results"
            else:
                conversation_focus = "conversation"
        inference_context = character_context.to_dict()
        if medulla_inventory is not None:
            inference_context["medulla"] = {
                "inventory": medulla_inventory,
                "inventory_requested": inventory_requested,
                "invocations": medulla_invocations,
                "results_reused": _requests_raw_medulla_result(text.strip()),
                "conversation_focus": conversation_focus,
            }
            # Compatibility for existing providers while the structured
            # Medulla cognition contract becomes the authoritative shape.
            inference_context["medulla_observations"] = [
                {
                    "capability": item["capability"],
                    **(
                        {"result": item["result"]}
                        if item.get("status") == "completed"
                        else {"error": item.get("error")}
                    ),
                }
                for item in medulla_invocations
            ]
        result = await provider_router.infer(
            InferenceRequest(
                prompt=text.strip(),
                instructions=(
                    (character_guidance or "")
                    + "\n\nMemory truthfulness: Durable memories and their provenance "
                    "are authoritative for the Entity's personal history. Never describe "
                    "model/background knowledge as a remembered personal experience. "
                    "If no direct-experience memory supports an event, say that the "
                    "Entity does not have that lived experience.\n\n"
                    "Medulla grounding: The medulla.inventory object is the authoritative "
                    "live capability state. Distinguish registration, provider connection, "
                    "and invocation status. The medulla.invocations records are the only "
                    "evidence that an external operation completed. Never say an operation "
                    "was checked, searched, fetched, read, measured, or invoked unless a "
                    "relevant completed record is present. A failed invocation does not mean "
                    "the capability is absent. When results_reused is true, return those "
                    "records without claiming a new invocation. Preapproved web domains are "
                    "an access-policy list, not a definition of the searchable web."
                ).strip(),
                context=inference_context,
                metadata={
                    "entity_id": entity.id,
                    "signal_id": signal.id,
                    "source": signal.source,
                },
            )
        )
        grounded_output = _ground_external_action_claims(
            result.output,
            inventory=medulla_inventory,
            inventory_requested=inventory_requested,
            invocations=medulla_invocations,
            results_reused=reuse_previous,
        )
        grounded_output = _ground_model_action_envelope(
            grounded_output, medulla_invocations
        )
        if inventory_requested and medulla_inventory is not None:
            grounded_output = _render_medulla_inventory(medulla_inventory)
        elif reuse_previous:
            grounded_output = _render_raw_medulla_results(medulla_invocations)
        elif medulla_inventory is not None:
            requested_capabilities = _requested_capabilities(text.strip())
            if requested_capabilities and not medulla_invocations:
                grounded_output = _render_unavailable_capabilities(
                    requested_capabilities, medulla_inventory
                )
        action = await entity.action(
            "EchoResponse",
            text=grounded_output,
            request_id=result.request_id,
            provider=result.provider.to_dict(),
            timing=result.timing.to_dict(),
            medulla_invocations=medulla_invocations,
        )
        memory_metadata: dict[str, Any] = {
            "signal_id": signal.id,
            "action_id": action.id,
        }
        if subject_id is not None:
            memory_metadata["subject_id"] = subject_id
        entity.remember(
            WorkingMemory(
                content={
                    "event": "conversation_turn",
                    "user_message": text.strip(),
                    "bit_response": grounded_output,
                },
                source="echo.chat",
                metadata=memory_metadata,
            )
        )
        try:
            if entity.memory_service.durable:
                for candidate in user_statement_candidates(
                    text,
                    signal_id=signal.id,
                    action_id=action.id,
                    subject_id=subject_id,
                ):
                    entity.commit_memory_candidate(candidate)
        except Exception as error:
            logger.error(
                "durable memory commit failed",
                extra={"entity_id": entity.id, "signal_id": signal.id},
                exc_info=True,
            )
            if entity.runtime is not None:
                entity.runtime.report_error(
                    "memory.commit",
                    error,
                    entity_id=entity.id,
                    signal_id=signal.id,
                    action_id=action.id,
                )
        return action


def _medulla_actions_for_prompt(
    text: str,
    metadata: Mapping[str, Any],
    *,
    inventory: Mapping[str, Any] | None = None,
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Conservative read-only context gathering for an already authorized node."""

    lowered = text.lower()
    actions: list[tuple[str, dict[str, Any]]] = []
    available = {
        item.get("name")
        for item in (inventory or {}).get("capabilities", ())
        if item.get("available") is True
    }
    keywords = (
        ("battery", "battery.status"),
        ("system health", "system.health"),
        ("date", "system.datetime"),
        ("time", "system.datetime"),
        ("location", "location.current"),
        ("weather", "weather.current"),
    )
    for keyword, capability in keywords:
        if (
            re.search(rf"\b{re.escape(keyword)}\b", lowered)
            and capability in available
            and not any(item[0] == capability for item in actions)
        ):
            actions.append((capability, {}))
    if "filesystem.list" in available and re.search(
        r"\b(?:what|which|list|show)\b.*\b(?:files|folders|directories)\b|\bfiles\s+can\s+you\s+see\b",
        lowered,
    ):
        root = next(
            (
                item
                for item in (inventory or {}).get("resources", ())
                if item.get("provider_connected")
                and str(item.get("id", "")).startswith("filesystem.root.")
            ),
            None,
        )
        if root is not None:
            actions.append(("filesystem.list", {"resource_id": root["id"]}))
    urls = re.findall(r"https://[^\s<>\]\[\)\(\"']+", text)
    for url in urls[:2]:
        if "web.fetch" in available:
            actions.append(("web.fetch", {"url": url.rstrip(".,;:")}))
    if "web.search" in available and any(word in lowered for word in ("search", "research", "look up")) and not urls:
        query = metadata.get("search_query", text)
        if isinstance(query, str) and query.strip():
            actions.append(("web.search", {"query": query.strip()}))
    file_path = metadata.get("file_path")
    if not isinstance(file_path, str):
        quoted_path = re.search(r"\b(?:read|open|inspect)\s+(?:the\s+file\s+)?[\"'](/[^\"']+)[\"']", text, re.I)
        plain_path = re.search(r"\b(?:read|open|inspect)\s+(?:the\s+file\s+)?(/\S+)", text, re.I)
        matched_path = quoted_path or plain_path
        file_path = matched_path.group(1) if matched_path else None
    if isinstance(file_path, str) and file_path:
        if "filesystem.read" in available:
            actions.append(("filesystem.read", {"path": file_path}))
    return tuple(actions[:6])


def _medulla_inventory(node_host: Any) -> dict[str, Any]:
    """Build a detached live cognition view from the authoritative registry."""

    capabilities: list[dict[str, Any]] = []
    for capability in node_host.registry.inspect():
        provider_id = capability.provider.provider_id
        health = node_host.registry.health(provider_id)
        status = node_host.status(provider_id)
        negotiation = getattr(status, "negotiation_state", None)
        reachability = getattr(status, "reachability", None)
        provider_state = getattr(getattr(health, "state", None), "value", "unknown")
        availability = capability.availability.state.value
        connected = getattr(negotiation, "value", negotiation) == "active" and getattr(
            reachability, "value", reachability
        ) != "unreachable"
        capabilities.append(
            {
                "capability_id": capability.capability_id,
                "name": capability.name,
                "description": capability.description,
                "registered": True,
                "availability": availability,
                "available": (
                    availability in {"available", "degraded"}
                    and provider_state != "unavailable"
                    and connected
                ),
                "provider_id": provider_id,
                "provider_display_name": capability.provider.display_name,
                "provider_health": provider_state,
                "node_id": provider_id,
                "node_connected": connected,
                "node_state": getattr(negotiation, "value", negotiation),
            }
        )
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "medulla.registry",
        "capabilities": capabilities,
        "resources": list(node_host.inspect_resources()),
    }


def _medulla_invocation_evidence(
    invocation_id: str, action: Any, result: Any, *, capability: Mapping[str, Any] | None
) -> dict[str, Any]:
    state = getattr(result.state, "value", str(result.state))
    error = getattr(result, "error", None)
    return {
        "invocation_id": invocation_id,
        "action_id": result.action_id,
        "capability_id": result.capability_id or (capability or {}).get("capability_id"),
        "capability": action.type,
        "provider_id": result.provider_id,
        "node_id": result.node_id,
        "status": state,
        "result": dict(result.result) if state == "completed" else None,
        "error": error.to_dict() if error is not None else None,
        "completed_at": result.completed_at.isoformat(),
    }


def _requests_capability_inventory(text: str, current_focus: str) -> bool:
    lowered = text.lower().strip()
    direct = any(
        phrase in lowered
        for phrase in (
            "what capabilities",
            "which capabilities",
            "capabilities do you",
            "medulla capabilities",
            "what else can you see",
            "what else are you seeing",
            "what access do you",
        )
    )
    repeated = lowered.rstrip(" .!?") in {"check again", "what else", "how about now"}
    return direct or (repeated and current_focus == "medulla.capability_inventory")


def _requests_raw_medulla_result(text: str) -> bool:
    lowered = text.lower()
    return "raw result" in lowered or "raw medulla" in lowered


def _requested_capabilities(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    requested: list[str] = []
    for keyword, capability in (
        ("battery", "battery.status"),
        ("system health", "system.health"),
        ("date", "system.datetime"),
        ("time", "system.datetime"),
        ("location", "location.current"),
        ("weather", "weather.current"),
    ):
        if re.search(rf"\b{re.escape(keyword)}\b", lowered) and capability not in requested:
            requested.append(capability)
    if any(word in lowered for word in ("search", "research", "look up")):
        requested.append("web.search")
    if re.search(r"\b(?:files|folders|directories)\b", lowered):
        requested.append("filesystem.list")
    return tuple(requested)


def _render_medulla_inventory(inventory: Mapping[str, Any]) -> str:
    capabilities = inventory.get("capabilities", ())
    if not capabilities:
        return "Medulla currently reports no registered capabilities from approved providers."
    lines = ["Medulla's current capability inventory is:"]
    for item in capabilities:
        provider = item.get("provider_display_name") or item["provider_id"]
        if item.get("available"):
            state = "available; provider connected"
        elif item.get("node_connected"):
            state = f"registered; provider connected; capability {item['availability']}"
        else:
            state = f"registered; provider disconnected; capability {item['availability']}"
        lines.append(f"- {item['name']} via {provider}: {state}")
    lines.append(f"Observed from medulla.registry at {inventory['observed_at']}.")
    return "\n".join(lines)


def _render_unavailable_capabilities(
    requested: tuple[str, ...], inventory: Mapping[str, Any]
) -> str:
    registered = {
        item["name"]: item for item in inventory.get("capabilities", ())
    }
    details: list[str] = []
    for name in requested:
        item = registered.get(name)
        if item is None:
            details.append(f"{name} is not registered")
        elif not item.get("node_connected"):
            details.append(f"{name} is registered, but its provider is disconnected")
        else:
            details.append(
                f"{name} is registered and its provider is connected, but the capability is {item['availability']}"
            )
    return "I couldn't invoke an applicable Medulla capability: " + "; ".join(details) + "."


def _render_raw_medulla_results(invocations: list[dict[str, Any]]) -> str:
    if not invocations:
        return "There is no relevant Medulla invocation result in the current conversation focus."
    return json.dumps(invocations, indent=2, sort_keys=True)


def _ground_model_action_envelope(
    output: str, invocations: list[dict[str, Any]]
) -> str:
    """Never expose a model's internal capability-request envelope as a reply."""

    if not re.search(r'["\\]?request[_\\]?actions["\\]?', output, re.I):
        return output
    completed = [item for item in invocations if item.get("status") == "completed"]
    if completed:
        return json.dumps(completed, indent=2, sort_keys=True)
    return (
        "I did not execute that proposed Medulla action, so I can't report an "
        "external result from it."
    )


def _ground_external_action_claims(
    output: str,
    *,
    inventory: Mapping[str, Any] | None,
    inventory_requested: bool,
    invocations: list[dict[str, Any]],
    results_reused: bool,
) -> str:
    """Block first-person completion claims that lack matching runtime evidence."""

    claim_patterns = {
        "web": r"\b(?:i\s+(?:searched|queried|looked up|did another pass|am firing off)|i['’]m\s+firing off|the search\s+did land)",
        "read": r"\bi\s+(?:fetched|read)\b",
        "battery": r"\bi\s+(?:measured|checked)\s+(?:the\s+|your\s+|my\s+)?battery\b",
        "generic": r"\bi(?:'ve| have)?\s+(?:checked|invoked|queried)\b",
    }
    claims = {
        kind for kind, pattern in claim_patterns.items() if re.search(pattern, output, re.I)
    }
    if not claims:
        return output
    completed = {
        item["capability"] for item in invocations if item.get("status") == "completed"
    }
    unsupported = set()
    if "web" in claims and not completed.intersection({"web.search", "web.fetch"}):
        unsupported.add("web operation")
    if "read" in claims and not completed.intersection({"web.fetch", "filesystem.read"}):
        unsupported.add("read operation")
    if "battery" in claims and "battery.status" not in completed:
        unsupported.add("battery read")
    if "generic" in claims and not completed and not inventory_requested:
        unsupported.add("external operation")
    if results_reused and re.search(r"\b(?:just now|again|another pass)\b", output, re.I):
        unsupported.add("new external operation")
    if not unsupported:
        return output
    available = [
        item["name"]
        for item in (inventory or {}).get("capabilities", ())
        if item.get("available")
    ]
    suffix = f" Available capabilities: {', '.join(available)}." if available else ""
    return (
        "I don't have runtime evidence that the claimed "
        + ", ".join(sorted(unsupported))
        + " completed, so I can't truthfully report that it did."
        + suffix
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
    bit_directory = selected_entity_seed_path("bit", config_path=config_path)
    seed = load_entity_seed(bit_directory)
    entity, persistence_resources = _entity_with_persistence(seed, config)
    runtime = config.create_runtime([entity])
    router = config.create_provider_router()
    node_host = None
    if config.medulla.enabled:
        from echo.medulla import EchoNodeWebSocketHost

        node_host = EchoNodeWebSocketHost(
            config.medulla.endpoint,
            signal_target=runtime,
            approval_mode=config.discovery.approval_mode,
        )
    register_user_message_handler(
        entity,
        router,
        character_guidance=seed.character_guidance,
        node_host=node_host,
    )
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
        fresh_entity, fresh_persistence = _entity_with_persistence(
            seed, generation_config
        )
        fresh_router = generation_config.create_provider_router()
        register_user_message_handler(
            fresh_entity,
            fresh_router,
            character_guidance=seed.character_guidance,
        )
        fresh_runtime = generation_config.create_runtime([fresh_entity])
        fresh_manager = RuntimeConfigurationManager(
            generation_config,
            fresh_runtime,
            provider_router=fresh_router,
            config_path=config_path,
        )
        return RestartTarget(
            runtime=fresh_runtime,
            resources=(fresh_router, fresh_manager, *fresh_persistence),
        )

    coordinator = GracefulRestartCoordinator(
        runtime,
        build_fresh_generation,
        resources=(router, manager, *persistence_resources),
    )
    service = RuntimeService(
        runtime,
        provider_router=router,
        configuration_manager=manager,
        restart_coordinator=coordinator,
        node_host=node_host,
    )
    lifespan = None
    if node_host is not None:
        @asynccontextmanager
        async def medulla_lifespan(_app: Any):
            await node_host.start()
            try:
                yield
            finally:
                await node_host.stop()

        lifespan = medulla_lifespan
    app = create_app(service, lifespan=lifespan)
    return app, config, runtime, service


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
    except (ConfigurationError, EntitySeedError, RuntimeError) as error:
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
