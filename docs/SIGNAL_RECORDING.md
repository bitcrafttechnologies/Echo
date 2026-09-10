# Signal Recording Format

Phase 8A defines recording format version 1. It deliberately does not define
Signal injection, timing playback, CLI commands, or Console controls.

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
