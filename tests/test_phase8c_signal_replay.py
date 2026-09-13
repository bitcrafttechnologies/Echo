from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    DeveloperCommandDispatcher,
    Entity,
    JsonLinesSignalRecorder,
    InvalidRequestError,
    REPLAY_METADATA_KEY,
    ReplayConflictError,
    ReplayMode,
    ReplayOperationError,
    ReplaySafetyPolicy,
    ReplayStartCommand,
    ReplayState,
    Runtime,
    RuntimeService,
    Signal,
    StartReplayRequest,
    parse_developer_command,
)
from echo.adapters.fastapi import create_app


ORIGINAL_TIME = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def make_recording(path: str, *signals: Signal) -> None:
    recorder = JsonLinesSignalRecorder(
        path, runtime_id="recording-runtime", durable=False
    )
    recorder.start(timestamp=ORIGINAL_TIME - timedelta(seconds=1))
    for offset, signal in enumerate(signals):
        recorder.record_signal(
            signal, recorded_at=ORIGINAL_TIME + timedelta(milliseconds=offset)
        )
    recorder.stop(timestamp=ORIGINAL_TIME + timedelta(seconds=1))


class SignalReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_signal_uses_runtime_emit_and_preserves_origin_metadata(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "one.jsonl")
            original = Signal(
                id="original-id",
                type="observed",
                source="sensor",
                timestamp=ORIGINAL_TIME,
                payload={"value": 7},
                metadata={"human": "inspectable", REPLAY_METADATA_KEY: {"old": True}},
            )
            make_recording(path, original)
            received: list[Signal] = []
            entity = Entity("bit")

            @entity.on("observed")
            async def observe(signal: Signal) -> None:
                received.append(signal)

            runtime = Runtime([entity])
            before = datetime.now(timezone.utc)
            with patch.object(runtime, "emit", wraps=runtime.emit) as emit:
                status = await RuntimeService(runtime).start_replay(
                    StartReplayRequest(
                        path=path,
                        mode=ReplayMode.SIGNAL,
                        signal_id=original.id,
                        safety_policy=ReplaySafetyPolicy.RECORD_ONLY,
                    )
                )
                emit.assert_awaited_once()

            self.assertEqual(status.state, ReplayState.COMPLETED)
            self.assertEqual(len(received), 1)
            replayed = received[0]
            self.assertNotEqual(replayed.id, original.id)
            self.assertGreaterEqual(replayed.timestamp, before)
            self.assertEqual(replayed.payload, original.payload)
            self.assertEqual(replayed.source, original.source)
            self.assertEqual(replayed.metadata["human"], "inspectable")
            marker = replayed.metadata[REPLAY_METADATA_KEY]
            self.assertTrue(marker["replayed"])
            self.assertEqual(marker["original_signal_id"], original.id)
            self.assertEqual(marker["original_timestamp"], ORIGINAL_TIME.isoformat())
            self.assertEqual(marker["safety_policy"], "record_only")
            self.assertIsNotNone(runtime.get_signal(replayed.id))

    async def test_sequential_replay_preserves_file_order(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "sequence.jsonl")
            originals = tuple(
                Signal(
                    id=f"original-{index}",
                    type="ordered",
                    source="test",
                    timestamp=ORIGINAL_TIME + timedelta(seconds=index),
                    payload={"index": index},
                )
                for index in range(3)
            )
            make_recording(path, *originals)
            seen: list[int] = []
            entity = Entity("bit")

            @entity.on("ordered")
            async def ordered(signal: Signal) -> None:
                seen.append(signal.payload["index"])

            service = RuntimeService(Runtime([entity]))
            status = await service.start_replay(
                StartReplayRequest(path=path, mode="sequential")
            )

            self.assertEqual(seen, [0, 1, 2])
            self.assertEqual(status.state, ReplayState.COMPLETED)
            self.assertEqual(status.next_index, 3)
            self.assertEqual(
                [item.original_signal_id for item in status.replayed_signals],
                [signal.id for signal in originals],
            )

    async def test_step_replay_emits_exactly_one_signal_per_step(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "steps.jsonl")
            make_recording(
                path,
                Signal(id="a", type="step", timestamp=ORIGINAL_TIME),
                Signal(id="b", type="step", timestamp=ORIGINAL_TIME),
            )
            runtime = Runtime()
            service = RuntimeService(runtime)
            ready = await service.start_replay(
                StartReplayRequest(path=path, mode="step")
            )
            self.assertEqual(ready.state, ReplayState.READY)
            self.assertEqual(len(runtime.signals), 0)

            first = await service.replay_next_step(ready.replay_id)
            self.assertEqual(first.state, ReplayState.RUNNING)
            self.assertEqual(first.next_index, 1)
            self.assertEqual(len(runtime.signals), 1)

            second = await service.replay_next_step(ready.replay_id)
            self.assertEqual(second.state, ReplayState.COMPLETED)
            self.assertEqual(len(runtime.signals), 2)
            self.assertEqual(
                service.get_replay_status(ready.replay_id), second
            )
            with self.assertRaises(ReplayConflictError):
                await service.replay_next_step(ready.replay_id)

    async def test_replay_actions_are_recorded_intents_under_safe_policy(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "action.jsonl")
            make_recording(
                path,
                Signal(
                    id="unsafe-source", type="request", timestamp=ORIGINAL_TIME
                ),
            )
            entity = Entity("bit")

            @entity.on("request")
            async def request(signal: Signal):
                return await entity.action("external.send", target="outside")

            runtime = Runtime([entity])
            status = await RuntimeService(runtime).start_replay(
                StartReplayRequest(path=path, mode="sequential")
            )

            self.assertEqual(status.safety_policy, ReplaySafetyPolicy.RECORD_ONLY)
            self.assertEqual(len(runtime.actions), 1)
            self.assertEqual(runtime.actions[0].type, "external.send")
            history = runtime.action_history.get(runtime.actions[0].id)
            self.assertIsNotNone(history)
            # "executed" is Echo's current in-memory handler lifecycle marker;
            # there is no external Action executor in the Runtime.
            self.assertEqual(history.status.value, "executed")

    async def test_unsafe_policy_is_rejected_and_handler_failure_is_recoverable(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "failure.jsonl")
            make_recording(
                path, Signal(id="failure", type="fails", timestamp=ORIGINAL_TIME)
            )
            entity = Entity("bit")

            @entity.on("fails")
            async def fails(signal: Signal) -> None:
                raise RuntimeError("handler failed during replay")

            runtime = Runtime([entity])
            service = RuntimeService(runtime)
            with self.assertRaises(InvalidRequestError):
                await service.start_replay(
                    StartReplayRequest(
                        path=path,
                        mode="sequential",
                        safety_policy="allow_external",
                    )
                )

            with self.assertRaises(ReplayOperationError) as raised:
                await service.start_replay(
                    StartReplayRequest(path=path, mode="sequential")
                )
            failed = raised.exception.details["replay"]
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(failed["error"]["type"], "RuntimeError")
            self.assertTrue(runtime.running)

    async def test_developer_commands_cover_all_replay_modes(self) -> None:
        signal = parse_developer_command("replay signal session.jsonl signal-1")
        self.assertIsInstance(signal, ReplayStartCommand)
        self.assertEqual(signal.mode, ReplayMode.SIGNAL)
        self.assertEqual(
            parse_developer_command("replay session session.jsonl").mode,
            ReplayMode.SEQUENTIAL,
        )
        self.assertEqual(
            parse_developer_command("replay step session.jsonl").mode,
            ReplayMode.STEP,
        )

        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "commands.jsonl")
            make_recording(
                path, Signal(id="one", type="command", timestamp=ORIGINAL_TIME)
            )
            dispatcher = DeveloperCommandDispatcher(RuntimeService(Runtime()))
            started = await dispatcher.execute_text(f"replay step {path}")
            replay_id = started.data["replay_id"]
            advanced = await dispatcher.execute_text(f"replay next {replay_id}")
            inspected = await dispatcher.execute_text(f"replay status {replay_id}")
            self.assertEqual(advanced.data["state"], "completed")
            self.assertEqual(inspected.data["replay_id"], replay_id)


class SignalReplayHttpTests(unittest.TestCase):
    def test_http_starts_steps_and_inspects_replay(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "http.jsonl")
            make_recording(
                path,
                Signal(id="http-one", type="http", timestamp=ORIGINAL_TIME),
            )
            client = TestClient(create_app(RuntimeService(Runtime())))

            response = client.post(
                "/runtime/replays",
                json={"path": path, "mode": "step", "safety_policy": "record_only"},
            )
            self.assertEqual(response.status_code, 201)
            replay_id = response.json()["replay_id"]
            stepped = client.post(f"/runtime/replays/{replay_id}/step")
            inspected = client.get(f"/runtime/replays/{replay_id}")
            self.assertEqual(stepped.status_code, 200)
            self.assertEqual(stepped.json()["state"], "completed")
            self.assertEqual(inspected.json()["replay_id"], replay_id)
