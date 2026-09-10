from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    JsonLinesSignalRecorder,
    ReplayMode,
    ReplayOperationError,
    ReplayState,
    ReplayTiming,
    Runtime,
    RuntimeService,
    RuntimeSignalReplay,
    Signal,
    StartReplayRequest,
    parse_developer_command,
    read_recorded_session,
)
from echo.adapters.fastapi import create_app


BASE_TIME = datetime(2025, 1, 1, tzinfo=timezone.utc)


def make_timed_recording(path: str, offsets: tuple[float, ...]) -> None:
    recorder = JsonLinesSignalRecorder(path, durable=False)
    recorder.start(timestamp=BASE_TIME)
    for index, offset in enumerate(offsets):
        recorder.record_signal(
            Signal(
                id=f"original-{index}",
                type="timed",
                timestamp=BASE_TIME + timedelta(seconds=offset),
                payload={"index": index},
            )
        )
    recorder.stop(timestamp=BASE_TIME + timedelta(seconds=max(offsets, default=0)))


class ReplayTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_realtime_and_accelerated_preserve_relative_timeline(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "timed.jsonl")
            make_timed_recording(path, (0, 2, 5))
            session = read_recorded_session(path)

            async def captured_run(timing: ReplayTiming, multiplier=None):
                delays: list[float] = []

                async def capture_wait(awaitable, *, timeout):
                    awaitable.close()
                    delays.append(timeout)
                    raise TimeoutError

                replay = RuntimeSignalReplay(
                    Runtime(),
                    path,
                    session,
                    mode=ReplayMode.SEQUENTIAL,
                    timing=timing,
                    multiplier=multiplier,
                )
                with patch(
                    "echo.runtime_replay.asyncio.wait_for",
                    side_effect=capture_wait,
                ):
                    status = await replay.run()
                self.assertEqual(status.state, ReplayState.COMPLETED)
                return delays

            realtime = await captured_run(ReplayTiming.REALTIME)
            accelerated = await captured_run(ReplayTiming.ACCELERATED, 4)

            self.assertAlmostEqual(realtime[0], 2, delta=0.05)
            self.assertAlmostEqual(realtime[1], 5, delta=0.05)
            self.assertAlmostEqual(accelerated[0], 0.5, delta=0.05)
            self.assertAlmostEqual(accelerated[1], 1.25, delta=0.05)

    async def test_timed_replay_can_be_cancelled_during_wait(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "cancel.jsonl")
            make_timed_recording(path, (0, 30))
            runtime = Runtime()
            service = RuntimeService(runtime)
            started = await service.start_replay(
                StartReplayRequest(
                    path=path,
                    mode="sequential",
                    timing="realtime",
                )
            )
            self.assertEqual(started.state, ReplayState.RUNNING)
            self.assertEqual(started.timing, ReplayTiming.REALTIME)
            self.assertEqual(len(runtime.signals), 1)

            requested = await service.cancel_replay(started.replay_id)
            self.assertTrue(requested.cancellation_requested)
            for _ in range(100):
                final = service.get_replay_status(started.replay_id)
                if final.state is ReplayState.CANCELLED:
                    break
                await asyncio.sleep(0.001)

            self.assertEqual(final.state, ReplayState.CANCELLED)
            self.assertEqual(final.next_index, 1)
            self.assertEqual(final.remaining_signals, 1)
            self.assertIsNotNone(final.completed_at)
            self.assertEqual(len(runtime.signals), 1)

    async def test_immediate_and_manual_status_report_timing(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "modes.jsonl")
            make_timed_recording(path, (0, 10))
            immediate_service = RuntimeService(Runtime())
            immediate = await immediate_service.start_replay(
                StartReplayRequest(path=path, mode="sequential")
            )
            self.assertEqual(immediate.state, ReplayState.COMPLETED)
            self.assertEqual(immediate.timing, ReplayTiming.IMMEDIATE)

            manual_service = RuntimeService(Runtime())
            manual = await manual_service.start_replay(
                StartReplayRequest(
                    path=path,
                    mode="sequential",
                    timing="manual_step",
                )
            )
            self.assertEqual(manual.state, ReplayState.READY)
            self.assertEqual(manual.timing, ReplayTiming.MANUAL_STEP)
            stepped = await manual_service.replay_next_step(manual.replay_id)
            self.assertEqual(stepped.next_index, 1)
            cancelled = await manual_service.cancel_replay(manual.replay_id)
            self.assertEqual(cancelled.state, ReplayState.CANCELLED)
            self.assertEqual(cancelled.remaining_signals, 1)

    async def test_acceleration_multiplier_is_validated_and_exposed(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "accelerated.jsonl")
            make_timed_recording(path, (0, 10))
            service = RuntimeService(Runtime())
            with self.assertRaises(ReplayOperationError):
                await service.start_replay(
                    StartReplayRequest(
                        path=path,
                        mode="sequential",
                        timing="accelerated",
                        multiplier=0,
                    )
                )
            status = await service.start_replay(
                StartReplayRequest(
                    path=path,
                    mode="sequential",
                    timing="accelerated",
                    multiplier=1000,
                )
            )
            self.assertEqual(status.multiplier, 1000.0)
            self.assertEqual(status.timing, ReplayTiming.ACCELERATED)
            await service.cancel_replay(status.replay_id)

    def test_command_grammar_exposes_timing_and_cancellation(self) -> None:
        realtime = parse_developer_command("replay session signals.jsonl realtime")
        accelerated = parse_developer_command(
            "replay session signals.jsonl accelerated 8"
        )
        manual = parse_developer_command("replay step signals.jsonl")
        cancelled = parse_developer_command("replay cancel replay-1")

        self.assertEqual(realtime.timing, ReplayTiming.REALTIME)
        self.assertEqual(accelerated.timing, ReplayTiming.ACCELERATED)
        self.assertEqual(accelerated.multiplier, 8.0)
        self.assertEqual(manual.timing, ReplayTiming.MANUAL_STEP)
        self.assertEqual(cancelled.replay_id, "replay-1")


class ReplayTimingHttpTests(unittest.TestCase):
    def test_http_exposes_timing_status_and_cancellation(self) -> None:
        with TemporaryDirectory() as directory:
            path = os.path.join(directory, "http-timing.jsonl")
            make_timed_recording(path, (0, 30))
            with TestClient(create_app(RuntimeService(Runtime()))) as client:
                started = client.post(
                    "/runtime/replays",
                    json={
                        "path": path,
                        "mode": "sequential",
                        "timing": "accelerated",
                        "multiplier": 2,
                        "safety_policy": "record_only",
                    },
                )
                self.assertEqual(started.status_code, 201)
                replay_id = started.json()["replay_id"]
                self.assertEqual(started.json()["timing"], "accelerated")
                self.assertEqual(started.json()["multiplier"], 2.0)

                cancelled = client.delete(f"/runtime/replays/{replay_id}")
                status = client.get(f"/runtime/replays/{replay_id}")

            self.assertEqual(cancelled.status_code, 200)
            self.assertEqual(cancelled.json()["state"], "cancelled")
            self.assertEqual(status.json()["state"], "cancelled")
