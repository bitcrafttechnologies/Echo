from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (  # noqa: E402
    ConfigurationChangeClass,
    ConfigurationError,
    ConfigurationReloadStatus,
    Entity,
    HistoryConfig,
    InferenceRequest,
    MockProvider,
    ProviderMode,
    ProviderRouter,
    ProviderSlot,
    RuntimeEventType,
    RuntimeConfigurationManager,
    RuntimeService,
    Signal,
    load_config,
)


class ConfigurationReloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        root = logging.getLogger()
        self._handlers = list(root.handlers)
        self._level = root.level

    def tearDown(self) -> None:
        root = logging.getLogger()
        root.handlers[:] = self._handlers
        root.setLevel(self._level)

    def write_config(
        self,
        directory: str,
        name: str,
        *,
        mode: str = "auto",
        preference: tuple[str, ...] = ("remote", "lan", "offline"),
        log_level: str = "INFO",
        history_limit: int = 10,
        signal_limit: int = 10,
        api_port: int = 8000,
        openrouter_key: str = "test-key",
    ) -> Path:
        preference_toml = ", ".join(f'"{item}"' for item in preference)
        path = Path(directory) / name
        path.write_text(
            f"""
[logging]
level = "{log_level}"
[history]
signals = {signal_limit}
tasks = 10
actions = 10
logs = 10
errors = 10
[providers]
mode = "{mode}"
preference = [{preference_toml}]
history_limit = {history_limit}
[providers.openrouter]
enabled = true
api_key = "{openrouter_key}"
[providers.lan]
enabled = true
base_url = "http://lan.test:8080"
[providers.offline]
enabled = true
model_path = "model.gguf"
[api]
port = {api_port}
""",
            encoding="utf-8",
        )
        return path

    def router(self) -> ProviderRouter:
        return ProviderRouter(
            remote=MockProvider(provider_id="remote", responses=["remote"] * 5),
            lan=MockProvider(provider_id="lan", responses=["lan"] * 5),
            offline=MockProvider(provider_id="offline", responses=["offline"] * 5),
            history_limit=10,
        )

    async def test_live_safe_reload_applies_mode_order_and_history_atomically(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            active_path = self.write_config(directory, "active.toml")
            candidate_path = self.write_config(
                directory,
                "candidate.toml",
                mode="lan",
                preference=("offline", "lan", "remote"),
                log_level="ERROR",
                history_limit=2,
                signal_limit=2,
            )
            active = load_config(active_path, environ={})
            runtime = active.create_runtime([Entity("bit")])
            router = self.router()
            manager = RuntimeConfigurationManager(
                active,
                runtime,
                provider_router=router,
                config_path=candidate_path,
            )

            for index in range(3):
                await runtime.emit(Signal(type=f"signal.{index}"))
                await router.infer(InferenceRequest(prompt=str(index)))

            service = RuntimeService(
                runtime,
                provider_router=router,
                configuration_manager=manager,
            )
            with patch.dict(os.environ, {}, clear=True):
                result = service.reload_configuration()

        self.assertEqual(result.status, ConfigurationReloadStatus.APPLIED)
        self.assertTrue(result.applied)
        self.assertEqual(router.mode, ProviderMode.LAN)
        self.assertEqual(
            router.preference,
            (ProviderSlot.OFFLINE, ProviderSlot.LAN, ProviderSlot.REMOTE),
        )
        self.assertEqual(router.history_limit, 2)
        self.assertEqual(len(router.recent_inferences()), 2)
        self.assertEqual(runtime.signal_history.max_size, 2)
        self.assertEqual(len(runtime.signal_history), 2)
        self.assertEqual(logging.getLogger().level, logging.ERROR)
        self.assertIs(manager.active_config.providers.mode, ProviderMode.LAN)
        self.assertTrue(
            all(
                change.classification is ConfigurationChangeClass.LIVE_SAFE
                for change in result.changes
            )
        )
        audit = runtime.latest_logs(
            event_type=RuntimeEventType.CONFIGURATION_RELOAD
        )[0]
        self.assertEqual(audit.metadata["outcome"], "applied")
        self.assertTrue(audit.metadata["applied"])

    async def test_preference_changes_the_next_automatic_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            active_path = self.write_config(directory, "active.toml")
            candidate_path = self.write_config(
                directory,
                "candidate.toml",
                preference=("offline", "lan", "remote"),
            )
            active = load_config(active_path, environ={})
            runtime = active.create_runtime()
            router = self.router()
            manager = RuntimeConfigurationManager(
                active, runtime, provider_router=router
            )
            result = manager.reload(candidate_path, environ={})
            inference = await router.infer(InferenceRequest(prompt="route"))

        self.assertTrue(result.applied)
        self.assertEqual(inference.provider.provider_id, "offline")

    def test_no_change_reload_reconciles_transient_live_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            active_path = self.write_config(directory, "active.toml")
            active = load_config(active_path, environ={})
            runtime = active.create_runtime()
            router = self.router()
            manager = RuntimeConfigurationManager(
                active, runtime, provider_router=router
            )
            router.set_mode(ProviderMode.LAN)

            result = manager.reload_config(active, source=active_path)

        self.assertEqual(result.status, ConfigurationReloadStatus.NO_CHANGE)
        self.assertTrue(result.applied)
        self.assertIs(router.mode, ProviderMode.AUTO)
        self.assertEqual(result.changes, ())

    def test_restart_required_change_rejects_the_whole_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            active_path = self.write_config(directory, "active.toml")
            candidate_path = self.write_config(
                directory,
                "candidate.toml",
                log_level="ERROR",
                signal_limit=2,
                api_port=9000,
            )
            active = load_config(active_path, environ={})
            active.configure_logging()
            runtime = active.create_runtime()
            router = self.router()
            manager = RuntimeConfigurationManager(
                active, runtime, provider_router=router
            )

            result = manager.reload(candidate_path, environ={})

        self.assertEqual(
            result.status, ConfigurationReloadStatus.RESTART_REQUIRED
        )
        self.assertFalse(result.applied)
        self.assertEqual(runtime.signal_history.max_size, 10)
        self.assertEqual(logging.getLogger().level, logging.INFO)
        self.assertIs(manager.active_config, active)
        self.assertEqual(
            [change.path for change in result.restart_required_changes],
            ["api.port"],
        )
        audit = runtime.latest_logs(
            event_type=RuntimeEventType.CONFIGURATION_RELOAD
        )[0]
        self.assertEqual(audit.metadata["outcome"], "restart_required")

    def test_invalid_file_leaves_active_state_and_audits_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            active_path = self.write_config(directory, "active.toml")
            invalid_path = Path(directory) / "invalid.toml"
            invalid_path.write_text("[history]\nsignals = 0\n", encoding="utf-8")
            active = load_config(active_path, environ={})
            runtime = active.create_runtime()
            router = self.router()
            manager = RuntimeConfigurationManager(
                active, runtime, provider_router=router
            )

            with self.assertRaises(ConfigurationError):
                manager.reload(invalid_path, environ={})

        self.assertIs(manager.active_config, active)
        self.assertEqual(runtime.signal_history.max_size, 10)
        self.assertEqual(router.mode, ProviderMode.AUTO)
        audit = runtime.latest_logs(
            event_type=RuntimeEventType.CONFIGURATION_RELOAD
        )[0]
        self.assertEqual(audit.metadata["outcome"], "invalid")
        self.assertEqual(audit.metadata["issues"][0]["path"], "history.signals")

    def test_restart_report_redacts_changed_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            active_path = self.write_config(directory, "active.toml")
            candidate_path = self.write_config(
                directory,
                "candidate.toml",
                openrouter_key="new-super-secret",
            )
            active = load_config(active_path, environ={})
            runtime = active.create_runtime()
            manager = RuntimeConfigurationManager(
                active, runtime, provider_router=self.router()
            )

            result = manager.reload(candidate_path, environ={})

        serialized = str(result.to_dict())
        self.assertFalse(result.applied)
        self.assertNotIn("new-super-secret", serialized)
        self.assertIn("<redacted>", serialized)

    def test_provider_live_setting_needs_an_attached_router(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            active_path = self.write_config(directory, "active.toml")
            candidate_path = self.write_config(
                directory,
                "candidate.toml",
                history_limit=2,
            )
            active = load_config(active_path, environ={})
            runtime = active.create_runtime()
            manager = RuntimeConfigurationManager(active, runtime)

            result = manager.reload(candidate_path, environ={})

        self.assertFalse(result.applied)
        change = result.restart_required_changes[0]
        self.assertEqual(change.path, "providers.history_limit")
        self.assertEqual(change.reason, "no live ProviderRouter is attached")

    def test_inspection_hides_secrets_and_labels_editability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            active_path = self.write_config(directory, "active.toml")
            active = load_config(active_path, environ={})
            manager = RuntimeConfigurationManager(
                active,
                active.create_runtime(),
                provider_router=self.router(),
                config_path=active_path,
            )

            inspection = manager.inspect()

        serialized = str(inspection.to_dict())
        self.assertNotIn("test-key", serialized)
        fields = {field.path: field for field in inspection.fields}
        secret = fields["providers.openrouter.api_key"]
        self.assertTrue(secret.secret)
        self.assertTrue(secret.configured)
        self.assertIsNone(secret.value)
        self.assertFalse(secret.editable)
        self.assertEqual(
            fields["providers.mode"].classification,
            ConfigurationChangeClass.LIVE_SAFE,
        )
        self.assertTrue(fields["providers.mode"].editable)
        self.assertFalse(fields["api.port"].editable)

    def test_control_updates_only_valid_live_safe_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            active_path = self.write_config(directory, "active.toml")
            active = load_config(active_path, environ={})
            runtime = active.create_runtime()
            router = self.router()
            manager = RuntimeConfigurationManager(
                active, runtime, provider_router=router
            )

            result = manager.update(
                {"providers.mode": "lan", "history.signals": 4}
            )

            self.assertTrue(result.applied)
            self.assertIs(router.mode, ProviderMode.LAN)
            self.assertEqual(runtime.signal_history.max_size, 4)

            with self.assertRaises(ConfigurationError) as invalid:
                manager.update({"history.signals": 0})
            self.assertEqual(invalid.exception.issues[0].path, "history.signals")
            self.assertEqual(runtime.signal_history.max_size, 4)

            with self.assertRaises(ConfigurationError) as restart:
                manager.update({"api.port": 9000})
            self.assertIn("requires restart", restart.exception.issues[0].message)
            self.assertEqual(manager.active_config.api.port, 8000)


if __name__ == "__main__":
    unittest.main()
