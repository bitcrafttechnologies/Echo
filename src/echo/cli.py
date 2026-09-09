"""Command-line entry point shared by the Echo TUI and one-shot commands."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from echo.config import ConfigurationError, EchoConfig, load_config
from echo.tui.app import EchoTui, render_snapshot
from echo.tui.client import HttpEchoClient
from echo.tui.registry import default_registry
from echo.tui.tmux import TmuxSessionManager, TmuxUnavailableError


def _console_parser(config: EchoConfig) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="echoc console", description="Open the Echo terminal operator interface")
    parser.add_argument("--api-url", default=config.console.api_url, help="Echo management API URL")
    parser.add_argument("--session", default=config.console.session_name, help="tmux session name")
    parser.add_argument("--surface", choices=[item.key for item in default_registry().list()], default="overview")
    parser.add_argument("--entity", help="Entity selected by inspector and Chat")
    parser.add_argument("--no-tmux", action="store_true", help="run one full-screen TUI in this terminal")
    parser.add_argument("--snapshot", action="store_true", help="print one plain-text snapshot and exit")
    return parser


async def _run_console(
    arguments: argparse.Namespace,
    config: EchoConfig,
    *,
    config_path: str | None = None,
) -> int:
    client = HttpEchoClient(
        arguments.api_url,
        timeout_seconds=config.console.request_timeout_seconds,
    )
    app = EchoTui(
        client,
        surface=arguments.surface,
        refresh_seconds=config.console.poll_interval_seconds,
    )
    app.entity_id = arguments.entity
    if arguments.snapshot:
        try:
            data = await app.snapshot()
        except Exception as error:
            print(f"Echo Console offline: {error}", file=sys.stderr)
            return 1
        print(render_snapshot(arguments.surface, data, color=False))
        return 0
    if not arguments.no_tmux:
        manager = TmuxSessionManager(
            session_name=arguments.session,
            api_url=arguments.api_url,
            project_root=Path.cwd(),
            config_path=Path(config_path).resolve() if config_path else None,
        )
        try:
            manager.launch()
        except TmuxUnavailableError as error:
            print(str(error), file=sys.stderr)
            return 2
        return 0
    await app.run()
    return 0


def _config_target(parts: list[str]) -> Path:
    root = Path.cwd()
    if not parts:
        return root
    if parts[0] == "entity" and len(parts) == 2:
        return root / "entities" / parts[1].lower()
    targets = {"core": root, "models": root / "models", "signals": root / "entities"}
    if len(parts) == 1 and parts[0] in targets:
        return targets[parts[0]]
    raise ValueError("usage: echoc config [entity ENTITY|core|models|signals]")


def _open_config(parts: list[str]) -> int:
    try:
        target = _config_target(parts)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    if not target.exists():
        print(f"configuration target does not exist: {target}", file=sys.stderr)
        return 1
    editor = shutil.which("nvim") or shutil.which("vi")
    if not editor:
        print("nvim or vi is required to edit configuration", file=sys.stderr)
        return 2
    print(
        "After saving, run 'echoc config reload' to request explicit "
        "live-safe reload; restart-required changes will be reported.",
        file=sys.stderr,
    )
    return subprocess.run([editor, str(target)], check=False).returncode


async def _one_shot(
    parts: list[str],
    api_url: str,
    *,
    timeout_seconds: float = 3.0,
) -> int:
    client = HttpEchoClient(api_url, timeout_seconds=timeout_seconds)
    if parts in (["status"],):
        parts = ["runtime", "status"]
    if parts in (["entities"], ["entity", "list"]):
        result = (await client.snapshot("entity"))["entities"]
    elif parts == ["signals"]:
        result = (await client.snapshot("signals"))["signals"]
    elif parts == ["tasks"]:
        result = (await client.snapshot("tasks"))["tasks"]
    elif parts == ["logs"]:
        result = (await client.snapshot("logs"))["logs"]
    else:
        # Re-quote argv so JSON object arguments survive the shared shlex parser.
        result = await client.execute(shlex.join(parts))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parts = list(sys.argv[1:] if argv is None else argv)
    config_path: str | None = None
    if parts[:1] == ["--config"] and len(parts) < 2:
        print("usage: echoc --config PATH COMMAND", file=sys.stderr)
        return 2
    if len(parts) >= 2 and parts[0] == "--config":
        config_path, parts = parts[1], parts[2:]
    if parts[:2] == ["config", "validate"]:
        if len(parts) > 3:
            print("usage: echoc config validate [PATH]", file=sys.stderr)
            return 2
        validation_path = parts[2] if len(parts) > 2 else config_path
        try:
            load_config(validation_path)
        except ConfigurationError as error:
            print(error, file=sys.stderr)
            return 2
        print(f"Echo configuration is valid{f': {validation_path}' if validation_path else ''}.")
        return 0
    try:
        config = load_config(config_path)
    except ConfigurationError as error:
        print(error, file=sys.stderr)
        return 2
    config.configure_logging()
    if not parts or parts[0] == "console":
        arguments = _console_parser(config).parse_args(parts[1:] if parts else [])
        return asyncio.run(
            _run_console(arguments, config, config_path=config_path)
        )
    if len(parts) >= 2 and parts[0] == "config" and parts[1] in {
        "inspect",
        "reload",
        "set",
    }:
        try:
            return asyncio.run(
                _one_shot(
                    parts,
                    config.console.api_url,
                    timeout_seconds=config.console.request_timeout_seconds,
                )
            )
        except Exception as error:
            print(f"Echo command failed: {error}", file=sys.stderr)
            return 1
    if parts[0] == "config":
        return _open_config(parts[1:])
    api_url = config.console.api_url
    if len(parts) >= 2 and parts[:1] == ["--api-url"]:
        api_url, parts = parts[1], parts[2:]
    try:
        return asyncio.run(
            _one_shot(
                parts,
                api_url,
                timeout_seconds=config.console.request_timeout_seconds,
            )
        )
    except Exception as error:
        print(f"Echo command failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
