from __future__ import annotations

import asyncio
import os
import sys
from tempfile import TemporaryDirectory
import threading
from time import monotonic
import unittest
from unittest.mock import AsyncMock, patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    DeveloperCommandDispatcher,
    EmitSignalRequest,
    Entity,
    JsonLinesSignalRecorder,
    RecordingConflictError,
    RecordingStartCommand,
    RecordingState,
    RecordingStatusCommand,
    RecordingStopCommand,
    Runtime,
    RuntimeEventType,
    RuntimeService,
    Signal,
    StartRecordingRequest,
    parse_developer_command,
    read_recorded_session,
)
from echo.tui.client import HttpEchoClient


class RuntimeRecordingTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_records_all_runtime_signals_and_linkage(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "nested.echo.jsonl")
            bit = Entity("bit")
            runtime = Runtime([bit])
            service = RuntimeService(runtime)

            @bit.on("parent")
            async def parent(signal: Signal):
                await bit.emit(Signal(type="child", source="entity"))
                return await bit.action("respond", parent_id=signal.id)

            started = await service.start_recording(
                StartRecordingRequest(
                    path=path,
                    metadata={"scenario": "nested", "developer": "test"},
                    durable=False,
                )
            )
            self.assertEqual(started.state, RecordingState.RECORDING)
            self.assertTrue(started.active)
            self.assertEqual(started.runtime_id, runtime.id)
            self.assertEqual(started.path, path)

            parent_signal = Signal(type="parent", source="test")
            await service.emit_signal(EmitSignalRequest(signal=parent_signal))
            stopped = await service.stop_recording()

            self.assertEqual(stopped.state, RecordingState.STOPPED)
            self.assertFalse(stopped.active)
            self.assertEqual(stopped.signals_enqueued, 2)
            self.assertEqual(stopped.signals_written, 2)
            self.assertEqual(stopped.signals_dropped, 0)
            self.assertIsNone(stopped.last_error)

            session = read_recorded_session(path)
            self.assertTrue(session.completed)
            self.assertEqual(
                session.start.metadata,
                {"scenario": "nested", "developer": "test"},
            )
            self.assertEqual(session.start.runtime_id, runtime.id)
            self.assertEqual(
                {record.signal_type for record in session.signals},
                {"parent", "child"},
            )
            parent_record = next(
                record for record in session.signals if record.signal_type == "parent"
            )
            assert parent_record.linkage is not None
            self.assertEqual(parent_record.linkage.runtime_id, runtime.id)
            self.assertEqual(parent_record.linkage.entity_ids, ("bit",))
            self.assertEqual(len(parent_record.linkage.task_ids), 1)
            self.assertEqual(len(parent_record.linkage.action_ids), 1)

    async def test_slow_durable_write_does_not_block_signal_processing(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "slow.echo.jsonl")
            runtime = Runtime()
            service = RuntimeService(runtime)
            write_started = threading.Event()
            release_write = threading.Event()
            original = JsonLinesSignalRecorder.record_signal

            def slow_write(writer, signal, **kwargs):
                write_started.set()
                release_write.wait(timeout=2)
                return original(writer, signal, **kwargs)

            with patch.object(JsonLinesSignalRecorder, "record_signal", slow_write):
                await service.start_recording(
                    StartRecordingRequest(path=path, durable=False)
                )
                started = monotonic()
                result = await service.emit_signal(
                    EmitSignalRequest(signal=Signal(type="fast"))
                )
                elapsed = monotonic() - started

                self.assertEqual(result.routing_result.status, "unhandled")
                self.assertLess(elapsed, 0.25)
                await asyncio.to_thread(write_started.wait, 1)
                self.assertEqual(service.get_recording_status().signals_written, 0)
                release_write.set()
                stopped = await service.stop_recording()

            self.assertEqual(stopped.signals_written, 1)

    async def test_failed_background_write_surfaces_error_without_killing_echo(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "failure.echo.jsonl")
            runtime = Runtime()
            service = RuntimeService(runtime)

            def fail_write(writer, signal, **kwargs):
                raise OSError("disk unavailable")

            with patch.object(JsonLinesSignalRecorder, "record_signal", fail_write):
                await service.start_recording(
                    StartRecordingRequest(path=path, durable=False)
                )
                emitted = await service.emit_signal(
                    EmitSignalRequest(signal=Signal(type="survives"))
                )
                for _ in range(100):
                    if service.get_recording_status().state is RecordingState.FAILED:
                        break
                    await asyncio.sleep(0.001)

                failed = service.get_recording_status()
                self.assertEqual(failed.state, RecordingState.FAILED)
                self.assertEqual(failed.last_error["type"], "OSError")
                self.assertEqual(emitted.routing_result.status, "unhandled")
                self.assertTrue(runtime.running)
                self.assertEqual(runtime.get_signal(emitted.id).id, emitted.id)
                errors = runtime.latest_logs(event_type=RuntimeEventType.ERROR)
                self.assertTrue(
                    any(
                        event.metadata.get("operation")
                        == "signal_recording.write"
                        for event in errors
                    )
                )
                stopped = await service.stop_recording()

            self.assertEqual(stopped.state, RecordingState.FAILED)
            self.assertTrue(read_recorded_session(path).completed)
            replacement_path = os.path.join(directory, "replacement.jsonl")
            replacement = await service.start_recording(
                StartRecordingRequest(path=replacement_path, durable=False)
            )
            self.assertEqual(replacement.state, RecordingState.RECORDING)
            await service.stop_recording()

    async def test_full_queue_drops_without_backpressuring_runtime(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "bounded.echo.jsonl")
            runtime = Runtime()
            service = RuntimeService(runtime, recording_queue_capacity=1)
            write_started = threading.Event()
            release_write = threading.Event()
            original = JsonLinesSignalRecorder.record_signal

            def slow_write(writer, signal, **kwargs):
                write_started.set()
                release_write.wait(timeout=2)
                return original(writer, signal, **kwargs)

            with patch.object(JsonLinesSignalRecorder, "record_signal", slow_write):
                await service.start_recording(
                    StartRecordingRequest(path=path, durable=False)
                )
                await service.emit_signal(
                    EmitSignalRequest(signal=Signal(type="writing"))
                )
                await asyncio.to_thread(write_started.wait, 1)
                await service.emit_signal(
                    EmitSignalRequest(signal=Signal(type="queued"))
                )
                await service.emit_signal(
                    EmitSignalRequest(signal=Signal(type="dropped"))
                )

                status = service.get_recording_status()
                self.assertEqual(status.signals_dropped, 1)
                self.assertTrue(runtime.running)
                self.assertTrue(
                    any(
                        event.metadata.get("operation")
                        == "signal_recording.queue"
                        for event in runtime.latest_logs(
                            event_type=RuntimeEventType.ERROR
                        )
                    )
                )
                release_write.set()
                stopped = await service.stop_recording()

            self.assertEqual(stopped.signals_written, 2)

    async def test_recording_lifecycle_conflicts_are_explicit(self) -> None:
        with TemporaryDirectory() as directory:
            service = RuntimeService(Runtime())
            with self.assertRaises(RecordingConflictError):
                await service.stop_recording()

            first = os.path.join(directory, "first.jsonl")
            await service.start_recording(
                StartRecordingRequest(path=first, durable=False)
            )
            with self.assertRaises(RecordingConflictError):
                await service.start_recording(
                    StartRecordingRequest(
                        path=os.path.join(directory, "second.jsonl"),
                        durable=False,
                    )
                )
            await service.stop_recording()

    async def test_developer_commands_start_stop_and_report_status(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "command session.jsonl")
            service = RuntimeService(Runtime())
            commands = DeveloperCommandDispatcher(service)

            parsed = parse_developer_command(
                f"record start '{path}' '{{\"label\":\"cli\"}}'"
            )
            self.assertIsInstance(parsed, RecordingStartCommand)
            started = await commands.execute(parsed)
            self.assertEqual(started.command.value, "recording.start")
            self.assertEqual(started.data["metadata"], {"label": "cli"})

            status = await commands.execute(RecordingStatusCommand())
            self.assertTrue(status.data["active"])
            stopped = await commands.execute(RecordingStopCommand())
            self.assertEqual(stopped.data["state"], "stopped")

    async def test_one_shot_http_client_maps_record_commands_to_public_api(self) -> None:
        client = HttpEchoClient("http://echo.test")
        client._request = AsyncMock(return_value={"state": "recording"})

        result = await client.execute(
            "record start '/tmp/developer session.jsonl' '{\"label\":\"cli\"}'"
        )

        self.assertEqual(result["command"], "recording.start")
        client._request.assert_awaited_once_with(
            "/runtime/recording",
            method="POST",
            body={
                "path": "/tmp/developer session.jsonl",
                "metadata": {"label": "cli"},
            },
        )


@unittest.skipUnless(
    __import__("importlib").util.find_spec("fastapi")
    and __import__("importlib").util.find_spec("httpx"),
    "FastAPI test dependencies are optional",
)
class RuntimeRecordingHttpTests(unittest.TestCase):
    def test_http_start_status_stop_and_metadata(self) -> None:
        from fastapi.testclient import TestClient

        from echo.adapters.fastapi import create_app

        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "http.jsonl")
            service = RuntimeService(Runtime())
            with TestClient(create_app(service)) as client:
                idle = client.get("/runtime/recording")
                self.assertEqual(idle.status_code, 200)
                self.assertEqual(idle.json()["state"], "idle")

                started = client.post(
                    "/runtime/recording",
                    json={
                        "path": path,
                        "metadata": {"caller": "http"},
                        "durable": False,
                    },
                )
                self.assertEqual(started.status_code, 201)
                self.assertEqual(started.json()["metadata"], {"caller": "http"})

                emitted = client.post("/signals", json={"type": "http.signal"})
                self.assertEqual(emitted.status_code, 201)
                active = client.get("/runtime/recording").json()
                self.assertTrue(active["active"])
                self.assertEqual(active["signals_enqueued"], 1)

                stopped = client.delete("/runtime/recording")
                self.assertEqual(stopped.status_code, 200)
                self.assertEqual(stopped.json()["signals_written"], 1)

            session = read_recorded_session(path)
            self.assertEqual(session.start.metadata, {"caller": "http"})
            self.assertEqual(session.signals[0].signal_type, "http.signal")


if __name__ == "__main__":
    unittest.main()
