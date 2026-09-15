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

            preserved_database = root / "preserved.sqlite3"
            seed_continuity = subprocess.run(
                [
                    str(python),
                    "-c",
                    textwrap.dedent(
                        """
                        from echo import MemoryService, SQLiteMemoryRepository, user_statement_candidates
                        from echo.entity.memory_store import DurableMemoryType, MemoryCandidate, MemorySourceType

                        repository = SQLiteMemoryRepository(__import__('sys').argv[1])
                        service = MemoryService('bit', repository)
                        service.commit(user_statement_candidates('My name is Tucker.', signal_id='install-1')[0])
                        service.commit(MemoryCandidate(
                            content='Conversation: user discussed orbital gardening',
                            memory_type=DurableMemoryType.EPISODIC,
                            source_type=MemorySourceType.DIRECT_EXPERIENCE,
                            source_refs=('signal:install-2',),
                            confidence=1.0,
                            importance=0.6,
                            canonical_key='conversation.install-2',
                        ), trusted_provenance=True)
                        repository.close()
                        """
                    ),
                    str(preserved_database),
                ],
                env=clean_environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(seed_continuity.returncode, 0, seed_continuity.stderr)
            subprocess.run(
                [str(python), "-m", "pip", "uninstall", "-y", "echo-runtime"],
                env=clean_environment,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
                env=clean_environment,
                check=True,
                capture_output=True,
                text=True,
            )
            verify_continuity = subprocess.run(
                [
                    str(python),
                    "-c",
                    textwrap.dedent(
                        """
                        from echo import MemoryService, SQLiteMemoryRepository
                        repository = SQLiteMemoryRepository(__import__('sys').argv[1])
                        service = MemoryService('bit', repository)
                        assert service.query('What is my name?')[0].canonical_key == 'user.preferred_name'
                        assert any(record.memory_type.value == 'episodic' for record in service.query('What did we discuss recently?'))
                        repository.close()
                        """
                    ),
                    str(preserved_database),
                ],
                env=clean_environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(verify_continuity.returncode, 0, verify_continuity.stderr)

            fresh_database = root / "fresh.sqlite3"
            verify_fresh = subprocess.run(
                [
                    str(python),
                    "-c",
                    "from echo import MemoryService, SQLiteMemoryRepository; "
                    "r=SQLiteMemoryRepository(__import__('sys').argv[1]); "
                    "assert MemoryService('bit', r).list() == (); r.close()",
                    str(fresh_database),
                ],
                env=clean_environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(verify_fresh.returncode, 0, verify_fresh.stderr)

            scripts = environment / ("Scripts" if os.name == "nt" else "bin")
            node_cli = scripts / ("medulla-node.exe" if os.name == "nt" else "medulla-node")
            node_help = subprocess.run(
                [str(node_cli), "--help"],
                env=clean_environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(node_help.returncode, 0, node_help.stderr)
            self.assertIn("usage: medulla-node", node_help.stdout)

            for executable, usage in (
                ("macbook-medulla-node", "usage: macbook-medulla-node"),
                ("web-medulla-node", "usage: web-medulla-node"),
            ):
                script = scripts / (f"{executable}.exe" if os.name == "nt" else executable)
                result = subprocess.run([str(script), "--help"], env=clean_environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(usage, result.stdout)

            node_project = root / "node-consumer"
            node_project.mkdir()
            for arguments in (("init",), ("validate",), ("manifest",)):
                node_command = subprocess.run(
                    [str(node_cli), *arguments],
                    cwd=node_project,
                    env=clean_environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(node_command.returncode, 0, node_command.stderr)
            self.assertIn("Node: Workshop Pi", node_command.stdout)
            self.assertIn("Protocol: medulla/1", node_command.stdout)

            boundary = subprocess.run(
                [
                    str(python),
                    "-c",
                    "import sys, medulla_node; "
                    "import macbook_medulla_node, web_medulla_node; "
                    "assert not any(n == 'echo' or n.startswith('echo.') for n in sys.modules)",
                ],
                env=clean_environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(boundary.returncode, 0, boundary.stderr)

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
