# Current State

## Current implementation

Echo Phase 2D is a minimal, standard-library Python kernel with structured
runtime observability, bounded histories, and unified runtime inspection.

The `0.1-amendment/character_plan` branch also records the persistent character
architecture and adds its provider-independent base vocabulary. These types,
Bit configuration, and prompts are scaffolding for the amended roadmap; they
are deliberately not wired into the completed Phase 1 runtime.

The public package exports:

- `Signal`
- `Entity`
- `HandlerRegistry`
- `Task` and `TaskStatus`
- `Action`
- `Scheduler` and `SignalPriority`
- `Runtime`
- `RuntimeLogEvent`, `RuntimeEventType`, `LogSink`, and `InMemoryLogSink`
- `SignalHistory`, `SignalHistoryEntry`, and `SignalRoutingResult`
- `TaskHistory` and `TaskHistoryEntry`
- `ActionHistory`, `ActionHistoryEntry`, and `ActionStatus`

The working runtime path is:

```text
Signal
  -> priority Scheduler
  -> Runtime dispatch
  -> matching Entity handlers
  -> one Task per handler
  -> zero or more Actions
```

`Runtime.emit` is awaited and deterministic. It returns after the emitted
Signal has been processed. The Runtime keeps bounded compatibility lists of
recent Signal, Task, and Action objects alongside their read APIs.

`SignalHistory` defaults to 1,000 entries and can be configured through
`Runtime(signal_history_size=...)`. It stores safe snapshots of each Signal's
ID, type, source, timestamp, payload, and metadata before routing, then attaches
the routing outcome, matched Entity IDs, attempted handler count, Task IDs and
statuses, and any processing error. Queries return newest entries first and
support limits, ID lookup, and filtering by type and source. Unknown or evicted
IDs return `None`; the oldest entry is evicted first when capacity is exceeded.

`TaskHistory` retains bounded references to the live Task objects rather than
duplicating their mutable state. Queries materialize safe read snapshots with
identity, name, owner, status, priority, lifecycle timestamps, parent/children,
associated Signal, result, and error. Completed and failed Tasks remain
inspectable until predictable oldest-first eviction.

`ActionHistory` records Action creation and execution at the Runtime intent
boundary. Its snapshots include creation and execution times, lifecycle status,
parameters, associated Entity/Task/Signal IDs, and result or error. Task and
Action capacities default to 1,000 and are independently configurable through
`task_history_size` and `action_history_size`.

`Runtime.inspect()` (also available as `Runtime.snapshot()`) returns a detached,
JSON-safe dictionary containing runtime status and uptime, Entity IDs and state,
active Tasks, queued and recent Signals, recent Actions and errors, scheduler
status, and a per-Entity handler registry summary. Status distinguishes idle,
active, and stopped runtimes. Opaque values are represented safely, cyclic data
is bounded, and inspection never consumes queued work or mutates runtime state.

Every Runtime uses a replaceable `LogSink` and defaults to an
`InMemoryLogSink`. Structured, timestamped events cover Signal receipt and
routing; Task creation and status changes; Action creation and runtime
execution; Entity state changes; Runtime start and stop; and handler errors.
Events carry the applicable Entity, Signal, Task, and Action IDs plus structured
metadata. In-memory events can be filtered and are returned chronologically.
Logging failures are isolated from runtime execution.

Entities own an ID, mutable state, currently active Tasks, and their handler
registry. An Entity registered with a Runtime can create Actions and emit nested
Signals through simple high-level methods.

Signals include a stable ID, type, source, timezone-aware timestamp, payload,
and metadata. They serialize to dictionaries and JSON. Domain code can use
small typed Signal subclasses while preserving the same transport shape.

Tasks implement pending, running, paused, blocked, completed, failed, and
cancelled states. Their timestamps, owner, priority, context, parent/child IDs,
result, and error remain observable.

Actions are structured intent records with type, parameters, creation time, and
optional Entity and Task attribution. Phase 1 records them but does not execute
them against external systems.

The `action.executed` log event describes execution at the Runtime's structured
intent boundary. It does not claim an external side effect; external Action
execution remains deferred to Medulla.

## Completed

- Phase 0 (`0.1`): repository initialization and cleanup.
- Phase 0: Python `src` package and standard-library test setup.
- Phase 0: architecture, roadmap, decisions, and status documents.
- Phase 1A (`0.1.1`): Signal validation and serialization.
- Phase 1A: typed Signal subclass coverage.
- Phase 1B (`0.1.2`): Entity identity and copied initial state.
- Phase 1B: explicit ordered handler registry.
- Phase 1B: string and Signal-class handler matching.
- Phase 1B: decorator registration without hidden control flow.
- Phase 1C (`0.1.3`): Task lifecycle and invalid-transition checks.
- Phase 1C: Task parent/child identity relationships.
- Phase 1C: structured Action records.
- Phase 1D (`0.1.4`): stable async priority Scheduler.
- Phase 1D: Runtime Entity registration and lookup.
- Phase 1D: handler dispatch, Task creation, and Action collection.
- Phase 1D: failure recording and propagation.
- Phase 1D: nested Signal emission.
- Phase 1E (`0.1.5`): public-API integration coverage.
- Phase 1E: typed battery signal end-to-end scenario.
- Phase 1E: one Signal dispatching independently to multiple Entities.
- Character amendment: ownership invariants, vertical phase plan, base value
  types, Bit seed configuration, and provider-boundary prompts.
- Phase 2 base branch (`0.2`) established from the completed character
  amendment.
- Phase 2A (`0.2.1`): typed runtime log records and a replaceable sink.
- Phase 2A: default chronological in-memory storage and filtered queries.
- Phase 2A: Signal, Task, Action, Entity state, Runtime lifecycle, and error
  instrumentation with related IDs.
- Phase 2A: logging failure isolation and observability tests.
- Phase 2B (`0.2.2`): bounded in-memory Signal history and safe snapshots.
- Phase 2B: latest, ID, type, and source queries with predictable oldest-first
  eviction.
- Phase 2B: structured routing results for handled, unhandled, failed, and
  cancelled processing.
- Phase 2C (`0.2.3`): bounded Task and Action lifecycle histories.
- Phase 2C: reference-backed Task inspection across live, completed, and failed
  states without duplicating mutable Task state.
- Phase 2C: Action creation/execution timestamps, status, associations, result,
  and error inspection.
- Phase 2C: clean latest, ID, and filtered queries with oldest-first eviction.
- Phase 2D (`0.2.4`): unified, framework-independent runtime inspection API.
- Phase 2D: idle, active, and stopped status with monotonic accumulated uptime.
- Phase 2D: JSON-safe Entity, Task, Signal, Action, error, scheduler, and handler
  summaries without exposing internal queue or registry objects.

## In progress

Nothing. Phase 2D is complete. Later Phase 2 work has not begun.

## Known issues

- Entity state, runtime histories, and structured logs are in memory only.
- Signal replay storage is not implemented.
- Actions have no Medulla executor or transport.
- There is no long-running process host, CLI, HTTP API, or Console.
- There are no provider, model, memory, ROS, or robotics integrations.
- Character base types are not yet composed into `echo.core.Entity` or loaded
  from the Bit YAML configuration.
- There is no character persistence, context builder, consolidation service,
  behavior policy, or character mutation audit store yet.
- Scheduler `periodic` is a priority class, not a recurring timer facility.
- Signal payloads must already contain JSON-compatible values for `to_json`.

These are roadmap deferrals, not missing Phase 2D acceptance criteria.

## Architecture decisions

- `Echo_Plan.md` remains the architectural source of truth.
- Python 3.11+ and the standard library are sufficient through Phase 2D.
- Dataclasses represent kernel records; no schema framework is required yet.
- `unittest` verifies the project without downloaded test dependencies.
- The Entity is the public actor abstraction.
- Decorators only register handlers; the Runtime owns dispatch.
- Handler registrations accept event names or Signal subclasses.
- Scheduler ordering is priority first and FIFO within equal priority.
- Awaited emission is the Phase 1 runtime boundary.
- One handler invocation maps to one Task.
- Handler exceptions mark Tasks failed and propagate to the emitter.
- Actions are recorded intent until Medulla exists.
- Runtime logging depends only on a small synchronous sink interface.
- Sink failures never alter Runtime control flow.
- Signal history owns copied snapshots, is bounded independently per Runtime,
  and evicts in processing order.
- Task history references live Tasks and creates snapshots only when read.
- Action history adds lifecycle annotations without changing the Action intent
  primitive or introducing an external executor.
- Runtime inspection is a pure read operation that returns detached JSON-safe
  data and remains independent of transport and presentation frameworks.
- Requested character base modules are concrete, tested value types rather than
  empty interfaces; all other future packages remain unscaffolded.
- Echo owns Entity continuity; providers return untrusted cognitive proposals.
- Identity is independent of inference provider and embodiment.
- Bit-specific seeds remain outside the generic framework package.

## Next task

Begin the next Phase 2 subphase only when explicitly authorized. Phase 2D
intentionally stops after runtime snapshot/inspection; persistence and replay
remain deferred.

## Important files

- `Echo_Plan.md` — authoritative specification.
- `README.md` — setup and minimal Phase 1 example.
- `pyproject.toml` — package metadata and Python requirement.
- `src/echo/__init__.py` — public kernel API.
- `src/echo/core/signal.py` — Signal representation and serialization.
- `src/echo/core/entity.py` — Entity public abstraction.
- `src/echo/core/handlers.py` — explicit handler registration.
- `src/echo/core/task.py` — Task data and lifecycle transitions.
- `src/echo/core/action.py` — structured Action intent.
- `src/echo/core/scheduler.py` — stable priority scheduling.
- `src/echo/core/runtime.py` — dispatch and coordination.
- `src/echo/core/runtime_log.py` — structured events and log sink interface.
- `src/echo/core/signal_history.py` — bounded Signal snapshots and queries.
- `src/echo/core/task_history.py` — reference-backed Task lifecycle history.
- `src/echo/core/action_history.py` — bounded Action lifecycle history.
- `src/echo/core/inspection.py` — JSON-safe inspection conversion.
- `tests/test_signal.py` — Signal unit tests.
- `tests/test_entity.py` — Entity and registry unit tests.
- `tests/test_task_action.py` — Task and Action unit tests.
- `tests/test_scheduler_runtime.py` — Scheduler and Runtime unit tests.
- `tests/test_integration.py` — public-API integration tests.
- `tests/test_runtime_logging.py` — Phase 2A observability coverage.
- `tests/test_signal_history.py` — Phase 2B Signal history coverage.
- `tests/test_task_action_history.py` — Phase 2C history coverage.
- `tests/test_runtime_inspection.py` — Phase 2D snapshot coverage.
- `docs/ARCHITECTURE.md` — implemented architecture boundary.
- `docs/CHARACTER_ARCHITECTURE.md` — persistent character design and phased
  acceptance criteria.
- `docs/ROADMAP.md` — phase status and deferrals.
- `docs/DECISIONS.md` — accepted architecture decisions.
- `docs/CURRENT_STATE.md` — concise handoff record.
- `src/echo/entity/` — provider-independent character base value types.
- `entities/bit/` — Bit reference configuration and prompts.
- `prompts/` — generic model-boundary contracts.
