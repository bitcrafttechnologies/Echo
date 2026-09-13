from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import textwrap
import unittest


class InstalledPackageTests(unittest.TestCase):
    def test_wheel_runs_from_a_clean_downstream_project(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            clean_environment = os.environ.copy()
            clean_environment.pop("PYTHONPATH", None)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    ".",
                    "--no-deps",
                    "--no-build-isolation",
                    "--wheel-dir",
                    str(wheelhouse),
                ],
                cwd=repository,
                env=clean_environment,
                check=True,
                capture_output=True,
                text=True,
            )
            wheel = next(wheelhouse.glob("echo_runtime-*.whl"))
            environment = root / "venv"
            subprocess.run(
                [sys.executable, "-m", "venv", str(environment)],
                env=clean_environment,
                check=True,
                capture_output=True,
                text=True,
            )
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run(
                [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
                env=clean_environment,
                check=True,
                capture_output=True,
                text=True,
            )

            project = root / "consumer"
            entity = project / "entities" / "echo"
            (entity / "prompts").mkdir(parents=True)
            (entity / "identity.yaml").write_text(
                """schema_version: 1
entity:
  id: echo
  name: Echo
  type: demo
  presentation: text
identity:
  worldview: Test the boundary
  core_values:
    - clarity
""",
                encoding="utf-8",
            )
            (entity / "traits.yaml").write_text(
                "schema_version: 1\ntraits:\n  curiosity: 0.8\n", encoding="utf-8"
            )
            (entity / "drives.yaml").write_text(
                "schema_version: 1\ndrives:\n  learning: 0.7\n", encoding="utf-8"
            )
            (entity / "prompts" / "character.md").write_text(
                "Speak as Echo.", encoding="utf-8"
            )
            (project / "main.py").write_text(
                textwrap.dedent(
                    """
                    import asyncio
                    from echo import ActionDispatchState, EchoApplication, LocalQueueTransport, Signal

                    async def main():
                        transport = LocalQueueTransport()
                        app = EchoApplication.from_entity_directory("entities/echo", [transport])
                        received = asyncio.Event()

                        @app.entity.on("project.ping")
                        async def ping(signal):
                            app.entity.state["value"] = signal.payload["value"]
                            received.set()

                        async with app:
                            await transport.publish_signal(Signal(type="project.ping", payload={"value": 42}))
                            await asyncio.wait_for(received.wait(), 1)
                            action = await app.entity.action("project.pong", value=42)
                            result = await app.dispatch(action)
                            outgoing = await transport.receive_action()
                            assert result.state is ActionDispatchState.ACCEPTED
                            assert outgoing.parameters == {"value": 42}
                            assert app.entity.state["value"] == 42

                    asyncio.run(main())
                    """
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [str(python), "main.py"],
                cwd=project,
                env=clean_environment,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
