"""tmux owns workspace layout; Echo owns the commands shown inside it."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Callable, Sequence


class TmuxUnavailableError(RuntimeError):
    pass


@dataclass(slots=True)
class TmuxSessionManager:
    session_name: str = "echo-core"
    api_url: str = "http://127.0.0.1:8000"
    project_root: Path = field(default_factory=Path.cwd)
    config_path: Path | None = None
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run

    def _tmux(self) -> str:
        executable = shutil.which("tmux")
        if not executable:
            raise TmuxUnavailableError("tmux is required for the Echo workspace; install tmux or use echoc console --no-tmux")
        return executable

    def _view_command(self, surface: str) -> str:
        arguments = [sys.executable, "-m", "echo"]
        if self.config_path is not None:
            arguments.extend(["--config", str(self.config_path)])
        arguments.extend(
            ["console", "--no-tmux", "--surface", surface, "--api-url", self.api_url]
        )
        return shlex.join(arguments)

    def commands(self, tmux: str = "tmux") -> list[list[str]]:
        commands = [[tmux, "new-session", "-d", "-s", self.session_name, "-n", "dashboard", self._view_command("overview")]]
        for name, surface in (("chat", "chat"), ("signals", "signals"), ("tasks", "tasks"), ("entity", "entity"), ("providers", "providers"), ("configuration", "configuration"), ("logs", "logs")):
            commands.append([tmux, "new-window", "-t", self.session_name, "-n", name, self._view_command(surface)])
        entity_root = self.project_root / "entities"
        editor = shutil.which("nvim") or shutil.which("vi") or "vi"
        commands.append([tmux, "new-window", "-t", self.session_name, "-n", "files", shlex.join([editor, str(entity_root)])])
        commands.append([tmux, "new-window", "-t", self.session_name, "-n", "shell"])
        for index, name in enumerate(("dashboard", "chat", "signals", "tasks", "entity", "providers", "configuration", "logs"), 1):
            commands.append([tmux, "bind-key", "-n", f"M-{index}", "select-window", "-t", f"{self.session_name}:{name}"])
        commands.append([tmux, "select-window", "-t", f"{self.session_name}:dashboard"])
        return commands

    def launch(self, *, attach: bool = True) -> None:
        tmux = self._tmux()
        exists = self.runner([tmux, "has-session", "-t", self.session_name], capture_output=True, text=True).returncode == 0
        if not exists:
            for command in self.commands(tmux):
                self.runner(command, check=True, text=True)
        if attach:
            operation = "switch-client" if os.environ.get("TMUX") else "attach-session"
            self.runner([tmux, operation, "-t", self.session_name], check=True, text=True)
