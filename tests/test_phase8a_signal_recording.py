from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sys
from tempfile import TemporaryDirectory
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    RECORDING_FORMAT,
    RECORDING_FORMAT_VERSION,
    JsonLinesSignalRecorder,
    RecorderStateError,
    RecordingFormatError,
    RecordingSerializationError,
    RuntimeLinkage,
    Signal,
    read_recorded_session,
)


UTC = timezone.utc


class Phase8ASignalRecordingTests(unittest.TestCase):
    def test_json_lines_session_is_versioned_inspectable_and_round_trips(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "session.echo.jsonl")
            started_at = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
            signal_at = datetime(2026, 9, 9, 12, 0, 1, tzinfo=UTC)
            recorded_at = datetime(2026, 9, 9, 12, 0, 2, tzinfo=UTC)
            ended_at = datetime(2026, 9, 9, 12, 1, tzinfo=UTC)
            signal = Signal(
                id="signal-1",
                type="user.message",
                source="console",
                timestamp=signal_at,
                payload={"text": "Hello, Bit 👋", "parts": [1, True, None]},
                metadata={"trace_id": "trace-1"},
            )
            linkage = RuntimeLinkage(
                runtime_id="runtime-1",
                entity_ids=("bit",),
                task_ids=("task-1",),
                action_ids=("action-1",),
            )

            recorder = JsonLinesSignalRecorder(
                path,
                runtime_id="runtime-1",
                session_id="session-1",
                metadata={"label": "manual test"},
            )
            recorder.start(timestamp=started_at)
            recorder.record_signal(signal, recorded_at=recorded_at, linkage=linkage)
            recorder.stop(timestamp=ended_at, reason="requested")

            with open(path, encoding="utf-8") as stream:
                lines = stream.readlines()
            self.assertEqual(len(lines), 3)
            self.assertIn("Hello, Bit 👋", lines[1])
            objects = [json.loads(line) for line in lines]
            self.assertTrue(
                all(item["format"] == RECORDING_FORMAT for item in objects)
            )
            self.assertTrue(
                all(item["version"] == RECORDING_FORMAT_VERSION for item in objects)
            )
            self.assertEqual(
                [item["record_type"] for item in objects],
                ["session.started", "signal", "session.ended"],
            )

            restored = read_recorded_session(path)
            self.assertTrue(restored.completed)
            self.assertEqual(restored.start.runtime_id, "runtime-1")
            self.assertEqual(restored.start.metadata, {"label": "manual test"})
            self.assertEqual(restored.signals[0].signal_id, signal.id)
            self.assertEqual(restored.signals[0].timestamp, signal_at)
            self.assertEqual(restored.signals[0].payload, signal.payload)
            self.assertEqual(restored.signals[0].metadata, signal.metadata)
            self.assertEqual(restored.signals[0].linkage, linkage)
            self.assertEqual(restored.end.signal_count, 1)
            self.assertEqual(restored.end.reason, "requested")

    def test_unsafe_payload_is_rejected_without_writing_a_partial_signal(self) -> None:
        class Unsafe:
            pass

        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "session.jsonl")
            recorder = JsonLinesSignalRecorder(path, durable=False)
            recorder.start()

            with self.assertRaisesRegex(
                RecordingSerializationError, "signal.payload.unsafe"
            ):
                recorder.record_signal(
                    Signal(type="unsafe", payload={"unsafe": Unsafe()})
                )
            recorder.stop()

            session = read_recorded_session(path)
            self.assertEqual(session.signals, ())
            self.assertEqual(session.end.signal_count, 0)

    def test_non_finite_nested_values_and_non_string_keys_are_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            first_path = os.path.join(directory, "nan.jsonl")
            recorder = JsonLinesSignalRecorder(first_path, durable=False)
            recorder.start()
            with self.assertRaisesRegex(RecordingSerializationError, "finite"):
                recorder.record_signal(Signal(type="bad", payload={"value": float("nan")}))
            recorder.stop()

            with self.assertRaisesRegex(RecordingSerializationError, "keys must be strings"):
                JsonLinesSignalRecorder(
                    os.path.join(directory, "keys.jsonl"),
                    metadata={1: "not portable"},
                )

    def test_partial_session_remains_readable_after_complete_lines(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "active.jsonl")
            recorder = JsonLinesSignalRecorder(path, durable=False)
            recorder.start()
            recorder.record_signal(Signal(type="battery.low", source="battery"))

            session = read_recorded_session(path)
            self.assertFalse(session.completed)
            self.assertEqual(len(session.signals), 1)

            recorder.stop()

    def test_context_manager_closes_session_and_existing_file_is_not_overwritten(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "session.jsonl")
            with JsonLinesSignalRecorder(path, durable=False) as recorder:
                recorder.record_signal(Signal(type="one"))

            self.assertTrue(read_recorded_session(path).completed)
            with self.assertRaises(FileExistsError):
                JsonLinesSignalRecorder(path, durable=False).start()

    def test_lifecycle_and_corrupt_format_fail_explicitly(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "session.jsonl")
            recorder = JsonLinesSignalRecorder(path, durable=False)
            with self.assertRaises(RecorderStateError):
                recorder.record_signal(Signal(type="early"))
            recorder.start()
            with self.assertRaises(RecorderStateError):
                recorder.start()
            recorder.stop()
            with self.assertRaises(RecorderStateError):
                recorder.stop()

            corrupt = os.path.join(directory, "future.jsonl")
            with open(corrupt, "w", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "format": RECORDING_FORMAT,
                            "version": 999,
                            "record_type": "session.started",
                            "session_id": "future",
                        }
                    )
                    + "\n"
                )
            with self.assertRaisesRegex(RecordingFormatError, "version"):
                read_recorded_session(corrupt)


if __name__ == "__main__":
    unittest.main()
