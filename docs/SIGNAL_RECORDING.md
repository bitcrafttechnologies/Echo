# Signal Recording Format

Phase 8A defines recording format version 1. Phase 8B records live sessions,
Phase 8C replays their Signals, Phase 8D controls replay timing, and Phase 8E
exposes those controls in Echo Console.

## Container

A recording is a UTF-8 JSON Lines file. Each non-empty line is one complete
JSON object and repeats these envelope fields:

```json
{"format": "echo.signal-session", "version": 1, "record_type": "signal", "session_id": "session-1"}
```

Repeating the format and version makes individual lines self-describing and
allows future readers to reject incompatible data explicitly. Recorders flush
every line and, by default, synchronize it to durable storage. An active or
interrupted recording remains readable without an ending record when all
written lines are complete.

The ordered record types are:

1. exactly one `session.started` record;
2. zero or more `signal` records;
3. optionally one final `session.ended` record.

All records in a file have the same `session_id`.

## Session records

`session.started` contains a timezone-aware `timestamp`, an optional
`runtime_id`, and JSON-object `metadata`. `session.ended` contains a
timezone-aware `timestamp`, the number of Signal records, and a human-readable
`reason`. A missing ending record means the recording was not cleanly closed;
it does not invalidate earlier complete records.

## Signal records

Each `signal` record has a `recorded_at` timestamp and the stable Signal wire
fields:

```json
{
  "format": "echo.signal-session",
  "version": 1,
  "record_type": "signal",
  "session_id": "session-1",
  "recorded_at": "2026-09-09T12:00:02+00:00",
  "signal": {
    "id": "signal-1",
    "type": "battery.low",
    "source": "battery",
    "timestamp": "2026-09-09T12:00:01+00:00",
    "payload": {"percent": 8},
    "metadata": {"sensor": "main"}
  },
  "linkage": {
    "runtime_id": "runtime-1",
    "entity_ids": ["bit"],
    "task_ids": ["task-1"],
    "action_ids": ["action-1"]
  }
}
```

`linkage` is optional. It can retain Runtime, Entity, Task, and Action IDs for
debugging without making those runtime results part of the Signal payload.

## Safety and compatibility

Payloads and metadata accept only JSON primitives, finite numbers, lists, and
objects with string keys. Unsupported Python values, container subclasses,
non-finite floats, and non-string keys fail before a Signal line is written.
The format never uses pickle, imports types, calls object hooks, or stores a
`repr` fallback.

Readers reject an unknown format, version, record type, invalid timestamp,
mixed session IDs, invalid ordering, or an ending count that does not match the
Signal lines. Reading validates data only; it never constructs domain-specific
Python subclasses or submits a Signal to a Runtime.

## Runtime recording

Phase 8B attaches at the Runtime's post-dispatch boundary, so Signals emitted by
developers, transports, and Entity handlers all use the same capture path.
Dispatch makes a non-waiting handoff to a bounded queue. Safe serialization,
file writes, flushing, and storage synchronization run in a worker thread.
Stopping first detaches capture and then drains the queue before writing the
session ending record.

`RecordingStatus` reports `idle`, `starting`, `recording`, `stopping`,
`stopped`, or `failed`, along with Runtime/session IDs, path, format/version,
timestamps, session metadata, enqueued/written/dropped counts, and the latest
error. A full queue drops the newly completed Signal and emits a recoverable
Runtime error rather than delaying dispatch. A background serialization or
write failure detaches capture, marks the session failed, and emits a
recoverable Runtime error; it never changes Signal routing results or stops
Echo.

Runtime service, HTTP, and one-shot `echoc record start|status|stop` operations
control the lifecycle.

## Signal replay

Phase 8C loads a validated recording and supports three modes: one selected
Signal, the complete session in file order, or a step session that injects
exactly one Signal per advance operation. Every injection calls the normal
`Runtime.emit()` entry path, so scheduling, handler Tasks, Action intent
records, histories, logs, and an active recorder behave exactly as they do for
live Signals.

A replay creates a new Signal ID and assigns its current UTC receipt time to
`Signal.timestamp`. It preserves source, type, payload, and ordinary metadata,
then reserves `metadata.echo_replay` for this marker:

```json
{
  "replayed": true,
  "replay_id": "replay-1",
  "recording_session_id": "session-1",
  "original_signal_id": "signal-1",
  "original_timestamp": "2026-09-09T12:00:01+00:00",
  "recorded_at": "2026-09-09T12:00:02+00:00",
  "timing": "realtime",
  "multiplier": null,
  "safety_policy": "record_only"
}
```

The original timestamp and identity therefore remain inspectable without
pretending the replay arrived in the past. A pre-existing `echo_replay` value
in recorded metadata is replaced so stored input cannot forge the runtime
marker.

Phase 8C exposes only the explicit `record_only` replay safety policy. Handlers
may produce Echo Action intent records through the normal Runtime path, but
replay does not call an external Action executor. A future policy that permits
external effects must be added and selected explicitly; none exists in this
phase.

Phase 8D timing is independent of Signal selection. `immediate` adds no delay,
`realtime` retains each Signal's recorded offset from the first Signal,
`accelerated` divides each offset by a required finite positive multiplier,
and `manual_step` emits only on explicit advance. Realtime and accelerated
deadlines use a monotonic clock anchored once at replay start, so system-clock
changes and cumulative per-sleep drift do not alter the intended timeline.
Timed input must have non-decreasing Signal timestamps.

Automatic timed sessions run in the background. Cancellation wakes a pending
delay and stops before the next Signal; if a handler is already running, it is
allowed to finish so Runtime dispatch is never torn down mid-Signal. Status
reports timing, multiplier, progress, cancellation request, terminal state,
and the original-to-runtime Signal mapping.

Service operations are `start_replay`, `replay_next_step`, `cancel_replay`, and
`get_replay_status`. HTTP uses `POST /runtime/replays`,
`POST /runtime/replays/{id}/step`, `DELETE /runtime/replays/{id}`, and
`GET /runtime/replays/{id}`. The matching one-shot commands are:

```text
echoc replay signal <recording-path> <recorded-signal-id>
echoc replay session <recording-path> [immediate|realtime|manual_step]
echoc replay session <recording-path> accelerated <multiplier>
echoc replay step <recording-path>
echoc replay next <replay-id>
echoc replay cancel <replay-id>
echoc replay status <replay-id>
```

## Echo Console controls

Phase 8E adds a replay panel to Signal Inspector. A developer enters a JSON
Lines recording path that is available to the Echo host, chooses immediate,
realtime, accelerated, or manual-step timing, and starts or stops the session.
The panel reports the loaded recording session ID, lifecycle state, completed
and remaining Signal counts, percentage progress, speed, and the enforced
`record_only` Action policy. Manual sessions expose one explicit next-Signal
control.

The current storage boundary is host-local files and does not expose a remote
file-list or upload API, so the Console selects a session by its server-local
path. Starting a replay validates and loads that file through the existing
Runtime API. Signal detail can replay the selected recorded Signal by original
ID. Live and retained replayed Signals carry a visible Replay label, and detail
shows the original Signal ID and timestamp from `metadata.echo_replay`.
