from __future__ import annotations

import os
from pathlib import Path
import logging
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (  # noqa: E402
    ConfigurationError,
    EchoConfig,
    ProviderMode,
    ProviderSlot,
    load_config,
)
from echo.tui.tmux import TmuxSessionManager  # noqa: E402


class EchoConfigurationTests(unittest.TestCase):
    def write_config(self, directory: str, content: str) -> Path:
        path = Path(directory) / "echo.toml"
        path.write_text(content, encoding="utf-8")
        return path

    def load_without_default_file(self, environ: dict[str, str]) -> EchoConfig:
        with tempfile.TemporaryDirectory() as directory:
            missing_default = Path(directory) / "absent.toml"
            with patch("echo.config.DEFAULT_CONFIG_PATH", missing_default):
                return load_config(environ=environ)

    def test_defaults_are_typed_and_build_an_unconfigured_runtime(self) -> None:
        config = self.load_without_default_file({})

        self.assertIsInstance(config, EchoConfig)
        self.assertEqual(config.providers.mode, ProviderMode.AUTO)
        self.assertEqual(config.history.signals, 1000)
        self.assertTrue(config.persistence.enabled)
        self.assertEqual(config.persistence.database_path.name, "echo.sqlite3")
        self.assertEqual(config.console.api_url, "http://127.0.0.1:8000")

        runtime = config.create_runtime()
        router = config.create_provider_router()
        self.assertTrue(runtime.running)
        self.assertEqual(runtime.signal_history.max_size, 1000)
        self.assertEqual(router.status().configured_providers, ())

    def test_toml_loads_every_section_and_environment_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(
                directory,
                """
[runtime]
auto_start = false
[logging]
level = "warning"
format = "json"
[history]
signals = 11
tasks = 12
actions = 13
logs = 14
errors = 15
[persistence]
enabled = true
database_path = "data/echo.sqlite3"
[providers]
mode = "lan"
preference = ["lan", "remote", "offline"]
history_limit = 16
[providers.openrouter]
enabled = true
api_key = "file-secret"
model = "file-model"
[providers.lan]
enabled = true
base_url = "http://lan.test:8080"
model = "lan-model"
[providers.offline]
enabled = true
model_path = "model.gguf"
extra_args = ["--ctx-size", "2048"]
[api]
host = "0.0.0.0"
port = 9000
[console]
api_url = "http://api.test:9000/"
request_timeout_seconds = 4.5
poll_interval_seconds = 2
session_name = "echo-test"
""",
            )
            config = load_config(
                path,
                environ={
                    "OPENROUTER_API_KEY": "environment-secret",
                    "OPENROUTER_MODEL": "environment-model",
                    "ECHO_PROVIDER_MODE": "auto",
                    "ECHO_PROVIDER_PREFERENCE": "offline,lan,remote",
                    "ECHO_API_PORT": "9100",
                },
            )

        self.assertFalse(config.runtime.auto_start)
        self.assertEqual(config.logging.level, "WARNING")
        self.assertEqual(config.history.errors, 15)
        self.assertEqual(
            config.persistence.database_path,
            (Path(directory) / "data" / "echo.sqlite3").resolve(),
        )
        self.assertEqual(config.providers.mode, ProviderMode.AUTO)
        self.assertEqual(
            config.providers.preference,
            (ProviderSlot.OFFLINE, ProviderSlot.LAN, ProviderSlot.REMOTE),
        )
        self.assertEqual(config.providers.openrouter.api_key, "environment-secret")
        self.assertEqual(config.providers.openrouter.model, "environment-model")
        self.assertEqual(config.providers.offline.extra_args, ("--ctx-size", "2048"))
        self.assertEqual(
            config.providers.offline.model_path,
            (Path(directory) / "model.gguf").resolve(),
        )
        self.assertEqual(config.api.port, 9100)
        self.assertEqual(config.console.api_url, "http://api.test:9000")
        self.assertNotIn("environment-secret", repr(config))

        runtime = config.create_runtime()
        router = config.create_provider_router()
        self.assertFalse(runtime.running)
        self.assertEqual(runtime.signal_history.max_size, 11)
        self.assertEqual(
            set(router.status().to_dict()["configured_providers"]),
            {"remote", "lan", "offline"},
        )

    def test_validation_collects_useful_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(
                directory,
                """
surprise = true
[logging]
level = "verbose"
[history]
signals = 0
[providers]
mode = "nearest"
[providers.openrouter]
enabled = true
base_url = "router.invalid"
[providers.lan]
enabled = true
[providers.offline]
enabled = true
port = 70000
[api]
port = "eight thousand"
[console]
api_url = "localhost:8000"
""",
            )
            with self.assertRaises(ConfigurationError) as raised:
                load_config(path, environ={})

        error = raised.exception
        paths = {issue.path for issue in error.issues}
        self.assertTrue(
            {
                "surprise",
                "logging.level",
                "history.signals",
                "providers.mode",
                "providers.openrouter.api_key",
                "providers.openrouter.base_url",
                "providers.lan.base_url",
                "providers.offline.model_path",
                "providers.offline.port",
                "api.port",
                "console.api_url",
            }.issubset(paths)
        )
        self.assertIn(str(path), str(error))
        self.assertEqual(error.to_dict()["code"], "invalid_configuration")

    def test_environment_values_are_typed_and_validated(self) -> None:
        with self.assertRaises(ConfigurationError) as raised:
            self.load_without_default_file(
                environ={
                    "ECHO_RUNTIME_AUTO_START": "perhaps",
                    "ECHO_HISTORY_TASKS": "many",
                    "ECHO_API_PORT": "70000",
                }
            )

        paths = {issue.path for issue in raised.exception.issues}
        self.assertEqual(
            paths,
            {"runtime.auto_start", "history.tasks", "api.port"},
        )

    def test_environment_override_does_not_hide_invalid_table_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, 'runtime = "invalid"')
            with self.assertRaises(ConfigurationError) as raised:
                load_config(path, environ={"ECHO_RUNTIME_AUTO_START": "true"})

        self.assertIn("runtime", {issue.path for issue in raised.exception.issues})

    def test_explicit_missing_and_non_toml_files_fail_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.toml"
            with self.assertRaisesRegex(ConfigurationError, "does not exist"):
                load_config(missing, environ={})
            yaml_path = Path(directory) / "echo.yaml"
            yaml_path.write_text("runtime: {}", encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, r"\.toml extension"):
                load_config(yaml_path, environ={})

    def test_logging_configuration_applies_level_and_json_format(self) -> None:
        config = self.load_without_default_file(
            {"ECHO_LOG_LEVEL": "error", "ECHO_LOG_FORMAT": "json"}
        )
        root = logging.getLogger()
        previous_handlers = list(root.handlers)
        previous_level = root.level
        try:
            config.configure_logging()
            self.assertEqual(root.level, 40)
            rendered = root.handlers[0].formatter.format(
                logging.LogRecord(
                    "echo.test", 40, __file__, 1, "failure %s", ("detail",), None
                )
            )
            self.assertIn('"level": "ERROR"', rendered)
            self.assertIn('"message": "failure detail"', rendered)
        finally:
            root.handlers[:] = previous_handlers
            root.setLevel(previous_level)

    def test_explicit_config_path_is_propagated_to_tmux_views(self) -> None:
        config_path = Path("/tmp/echo-phase-6a.toml")
        manager = TmuxSessionManager(config_path=config_path)

        dashboard_command = manager.commands()[0][-1]

        self.assertIn(f"--config {config_path}", dashboard_command)


if __name__ == "__main__":
    unittest.main()
