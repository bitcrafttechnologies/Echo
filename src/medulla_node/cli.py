"""Offline command-line tools for standalone Medulla Node configuration."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
import json
from pathlib import Path
import sys

from medulla_node.config import DEFAULT_CONFIG_PATH, NodeConfigurationError, load_node_config
from medulla_node.manifest import build_node_manifest
from medulla_node.runtime import MedullaNodeRuntime
from medulla_node.adapters import DevelopmentAdapter, GpioAdapter
from medulla_protocol import MedullaNodeManifest
from medulla_protocol.version import MEDULLA_NODE_RELEASE


MINIMAL_CONFIG = """node:
  id: workshop-pi
  name: Workshop Pi
  type: edge

provider:
  id: workshop-pi

transport:
  type: websocket
  address: ws://127.0.0.1:8765/medulla

metadata:
  implementation: medulla-node
  version: 0.9

# Echo is explicit; no discovery is performed.
discovery:
  approval_mode: manual

# echo:
#   transport: websocket
#   endpoint: ws://192.168.1.50:8765

capabilities: []
signals: []
resources: []

connection_requirements: {}

health:
  supported: true
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="medulla-node",
        description="Describe and validate a standalone Medulla Node offline.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {MEDULLA_NODE_RELEASE}")
    commands = parser.add_subparsers(dest="command")

    init = commands.add_parser("init", help="create a minimal declarative configuration")
    init.add_argument("path", nargs="?", type=Path, default=DEFAULT_CONFIG_PATH)

    validate = commands.add_parser("validate", help="validate configuration without connecting")
    validate.add_argument("path", nargs="?", type=Path, default=DEFAULT_CONFIG_PATH)

    manifest = commands.add_parser("manifest", help="render the manifest without advertising it")
    manifest.add_argument("path", nargs="?", type=Path, default=DEFAULT_CONFIG_PATH)
    manifest.add_argument("--json", action="store_true", help="render the exact JSON object")
    run = commands.add_parser("run", help="run the configured node transport")
    run.add_argument("path", nargs="?", type=Path, default=DEFAULT_CONFIG_PATH)
    run.add_argument(
        "--development",
        action="store_true",
        help="load the deterministic DevelopmentAdapter",
    )
    for command in (run, commands.add_parser("status", help="show configured node and runtime status")):
        command.add_argument(
            "--gpio-output",
            action="append",
            type=int,
            default=[],
            metavar="BCM_PIN",
            help="load a Raspberry Pi GPIO output pin",
        )
        command.add_argument(
            "--gpio-input",
            action="append",
            type=int,
            default=[],
            metavar="BCM_PIN",
            help="load a Raspberry Pi GPIO input pin",
        )
    status = next(
        item for item in commands.choices.values() if item.prog.endswith(" status")
    )
    status.add_argument("path", nargs="?", type=Path, default=DEFAULT_CONFIG_PATH)
    status.add_argument("--development", action="store_true")
    return parser


def _initialize(path: Path) -> None:
    if path.exists():
        raise NodeConfigurationError("file already exists", source=path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(MINIMAL_CONFIG, encoding="utf-8")


def render_manifest(manifest: MedullaNodeManifest) -> str:
    """Render a human-readable view derived only from the advertised object."""

    metadata = manifest.node.metadata
    lines = [
        f"Node: {manifest.node.display_name or manifest.node.node_id}",
        f"Node ID: {manifest.node.node_id}",
        f"Provider: {manifest.provider.provider_id}",
        f"Protocol: {manifest.protocol}",
        f"Implementation: {metadata.get('implementation', '-')}",
        f"Implementation version: {metadata.get('version', '-')}",
        "",
        "Transport endpoints:",
    ]
    lines.extend(
        f"  - {item.endpoint_id}: {item.kind.value} {item.address}"
        for item in manifest.endpoints
    )
    lines.extend(["", "Capabilities:"])
    lines.extend(
        (f"  - {item.name}: {item.description}" for item in manifest.capabilities),
    )
    if not manifest.capabilities:
        lines.append("  (none)")
    lines.extend(["", "Signals:"])
    lines.extend((f"  - {item.name}" for item in manifest.signals))
    if not manifest.signals:
        lines.append("  (none)")
    lines.extend(["", "Resources:"])
    lines.extend((f"  - {item.resource_id}: {item.description}" for item in manifest.resources))
    if not manifest.resources:
        lines.append("  (none)")
    requirements = manifest.connection_requirements
    lines.extend(["", "Connection requirements:"])
    if requirements.empty:
        lines.append("  (none)")
    else:
        if requirements.identity is not None:
            scopes = ", ".join(item.value for item in requirements.identity.scopes) or "(none)"
            lines.append(f"  Identity scopes: {scopes}")
        if requirements.metadata is not None:
            lines.append(f"  Metadata keys: {', '.join(requirements.metadata.keys) or '(none)'}")
        lines.extend(
            f"  Credential: credential.{item.credential_id} ({item.type}, {'required' if item.required else 'optional'})"
            for item in requirements.credentials
        )
        lines.extend(f"  Permission: {item}" for item in requirements.permissions)
        lines.extend(f"  Protocol feature: {item}" for item in requirements.protocol_features)
        lines.extend(f"  Entity capability: {item}" for item in requirements.entity_capabilities)
        if requirements.session is not None:
            lines.append(f"  Session features: {', '.join(requirements.session.features) or '(none)'}")
    health_supported = manifest.health.details.get("supported", False)
    lines.extend([
        "",
        f"Health support: {'yes' if health_supported else 'no'}",
        f"Health state: {manifest.health.state.value}",
    ])
    return "\n".join(lines)


def _configured_runtime(
    path: Path,
    *,
    development: bool,
    gpio_outputs: Sequence[int],
    gpio_inputs: Sequence[int],
) -> MedullaNodeRuntime:
    config = load_node_config(path)
    runtime = MedullaNodeRuntime(config)
    if development:
        runtime.register_adapter(DevelopmentAdapter(config.provider))
    if gpio_outputs or gpio_inputs:
        runtime.register_adapter(
            GpioAdapter(
                config.provider,
                output_pins=tuple(gpio_outputs),
                input_pins=tuple(gpio_inputs),
            )
        )
    return runtime


async def _run_node(
    path: Path,
    *,
    development: bool,
    gpio_outputs: Sequence[int],
    gpio_inputs: Sequence[int],
) -> None:
    runtime = _configured_runtime(
        path,
        development=development,
        gpio_outputs=gpio_outputs,
        gpio_inputs=gpio_inputs,
    )
    if runtime.config.echo is None:
        raise NodeConfigurationError("echo endpoint is required to run", source=path)
    await runtime.start()
    try:
        await asyncio.Event().wait()
    finally:
        await runtime.stop()


def render_status(runtime: MedullaNodeRuntime) -> str:
    manifest = runtime.manifest()
    status = runtime.status()
    echo = runtime.config.echo
    lines = [
        f"Node: {manifest.node.display_name or manifest.node.node_id}",
        f"Node ID: {manifest.node.node_id}",
        f"Lifecycle: {status.lifecycle.value}",
        f"Transport: {manifest.endpoints[0].kind.value} {manifest.endpoints[0].address}",
        f"Echo connection: {echo.endpoint if echo else '(not configured)'}",
        f"Approval mode: {runtime.config.approval_mode.value}",
        f"Loaded adapters: {', '.join(status.adapter_ids) or '(none)'}",
        f"Capabilities: {', '.join(item.name for item in manifest.capabilities) or '(none)'}",
        f"Signals: {', '.join(item.name for item in manifest.signals) or '(none)'}",
        f"Resources: {', '.join(item.resource_id for item in manifest.resources) or '(none)'}",
        f"Health: {manifest.health.state.value}",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 0
    try:
        if arguments.command == "init":
            _initialize(arguments.path)
            print(f"Created {arguments.path}")
            return 0
        if arguments.command == "run":
            try:
                asyncio.run(
                    _run_node(
                        arguments.path,
                        development=arguments.development,
                        gpio_outputs=arguments.gpio_output,
                        gpio_inputs=arguments.gpio_input,
                    )
                )
            except KeyboardInterrupt:
                return 0
            return 0
        if arguments.command == "status":
            runtime = _configured_runtime(
                arguments.path,
                development=arguments.development,
                gpio_outputs=arguments.gpio_output,
                gpio_inputs=arguments.gpio_input,
            )
            print(render_status(runtime))
            return 0
        config = load_node_config(arguments.path)
        manifest = build_node_manifest(config)
        if arguments.command == "validate":
            print(f"Valid: {arguments.path}")
            print(f"Protocol: {manifest.protocol}")
            return 0
        if arguments.json:
            print(json.dumps(manifest.to_dict(), indent=2, sort_keys=True, allow_nan=False))
        else:
            print(render_manifest(manifest))
        return 0
    except (NodeConfigurationError, OSError, TypeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["MINIMAL_CONFIG", "build_parser", "main", "render_manifest", "render_status"]
