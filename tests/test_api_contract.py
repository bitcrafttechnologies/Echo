from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echo import Entity, Runtime, RuntimeService, RuntimeServiceProtocol


class RuntimeApiContractExampleTests(unittest.TestCase):
    def test_runtime_service_implements_documented_protocol(self) -> None:
        service = RuntimeService(Runtime([Entity("bit")]))

        self.assertIsInstance(service, RuntimeServiceProtocol)

    def test_documented_runtime_api_example_executes(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository / "src")

        completed = subprocess.run(
            [sys.executable, "examples/runtime_api_contract.py"],
            cwd=repository,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        result = json.loads(completed.stdout)

        self.assertEqual(result["status"], "idle")
        self.assertEqual(result["action"], "acknowledge")
        self.assertEqual(result["state"], {"mode": "ready"})
        self.assertEqual(result["attention"], "developer ping")
        self.assertEqual(result["first_event"], "signal.received")


if __name__ == "__main__":
    unittest.main()
