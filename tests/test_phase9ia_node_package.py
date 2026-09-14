from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from echo.medulla import MedullaNodeManifest as EchoManifest
from echo.medulla import WireMessage as EchoWireMessage
from medulla_protocol import MedullaNodeManifest, WireMessage


class StandaloneNodePackageTests(unittest.TestCase):
    def test_echo_uses_the_shared_phase_9h_protocol_types(self) -> None:
        self.assertIs(EchoManifest, MedullaNodeManifest)
        self.assertIs(EchoWireMessage, WireMessage)

    def test_importing_node_does_not_import_echo(self) -> None:
        root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(root / "src")
        program = """
import json
import sys
import medulla_node
import medulla_protocol

echo_modules = sorted(
    name for name in sys.modules if name == 'echo' or name.startswith('echo.')
)
print(json.dumps({'echo_modules': echo_modules, 'version': medulla_node.__version__}))
raise SystemExit(bool(echo_modules))
"""
        completed = subprocess.run(
            [sys.executable, "-c", program], cwd=root, env=environment,
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["echo_modules"], [])
        self.assertEqual(result["version"], "0.9-node-0.9")

    def test_cli_help_and_version_are_installable_entrypoint_behavior(self) -> None:
        root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(root / "src")
        help_result = subprocess.run(
            [sys.executable, "-m", "medulla_node.cli", "--help"],
            cwd=root, env=environment, check=False, capture_output=True, text=True,
        )
        version_result = subprocess.run(
            [sys.executable, "-m", "medulla_node.cli", "--version"],
            cwd=root, env=environment, check=False, capture_output=True, text=True,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("usage: medulla-node", help_result.stdout)
        self.assertEqual(version_result.stdout.strip(), "medulla-node 0.9-node-0.9")


if __name__ == "__main__":
    unittest.main()
