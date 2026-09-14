"""Shared command runner for concrete standalone node distributions."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
import sys

from medulla_node.cli import render_manifest, render_status
from medulla_node.config import NodeConfigurationError
from medulla_node.runtime import MedullaNodeRuntime


RuntimeFactory = Callable[[Path], MedullaNodeRuntime]


def package_main(
    argv: Sequence[str] | None,
    *,
    program: str,
    description: str,
    template: str,
    factory: RuntimeFactory,
) -> int:
    parser = argparse.ArgumentParser(prog=program, description=description)
    commands = parser.add_subparsers(dest="command")
    for name, help_text in (
        ("init", "create a minimal node configuration"),
        ("validate", "validate configuration and adapter policy offline"),
        ("manifest", "render the exact advertised manifest offline"),
        ("status", "show configured runtime status"),
        ("run", "connect the standalone node to Echo"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("path", nargs="?", type=Path, default=Path(f"{program}.yaml"))
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 0
    try:
        if arguments.command == "init":
            if arguments.path.exists():
                raise NodeConfigurationError("file already exists", source=arguments.path)
            arguments.path.write_text(template, encoding="utf-8")
            print(f"Created {arguments.path}")
            return 0
        runtime = factory(arguments.path)
        manifest = runtime.manifest()
        if arguments.command == "validate":
            print(f"Valid: {arguments.path}\nProtocol: {manifest.protocol}")
            return 0
        if arguments.command == "manifest":
            print(render_manifest(manifest))
            return 0
        if arguments.command == "status":
            print(render_status(runtime))
            return 0
        if runtime.config.echo is None:
            raise NodeConfigurationError("echo endpoint is required to run", source=arguments.path)
        try:
            asyncio.run(_run(runtime))
        except KeyboardInterrupt:
            pass
        return 0
    except (NodeConfigurationError, OSError, TypeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


async def _run(runtime: MedullaNodeRuntime) -> None:
    await runtime.start()
    try:
        await asyncio.Event().wait()
    finally:
        await runtime.stop()


__all__ = ["package_main"]
