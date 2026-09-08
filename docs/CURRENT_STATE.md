# Current State

## Current implementation

Echo Phase 1 is a minimal, standard-library Python kernel.

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
Signal has been processed. The Runtime keeps in-memory lists of processed
Signals, Tasks, and Actions for direct inspection.

Entities own an ID, mutable state, currently active Tasks, and their handler
registry. An Entity registered with a Runtime can create Actions and emit nested
Signals through simple high-level methods.

Signals include type, source, timezone-aware timestamp, payload, and metadata.
They serialize to dictionaries and JSON. Domain code can use small typed Signal
subclasses while preserving the same transport shape.

Tasks implement pending, running, paused, blocked, completed, failed, and
cancelled states. Their timestamps, owner, priority, context, parent/child IDs,
result, and error remain observable.

Actions are structured intent records with type, parameters, creation time, and
optional Entity and Task attribution. Phase 1 records them but does not execute
them against external systems.

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

## In progress

Nothing. The character amendment is documented and scaffolded; runtime
integration remains deferred to the amended Phase 2 and later roadmap.

## Known issues

- Entity state and runtime history are in memory only.
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

These are roadmap deferrals, not missing Phase 1 acceptance criteria.

## Architecture decisions

- `Echo_Plan.md` remains the architectural source of truth.
- Python 3.11+ and the standard library are sufficient for Phase 1.
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
- Runtime history is observable but not presented as durable logging.
- Requested character base modules are concrete, tested value types rather than
  empty interfaces; all other future packages remain unscaffolded.
- Echo owns Entity continuity; providers return untrusted cognitive proposals.
- Identity is independent of inference provider and embodiment.
- Bit-specific seeds remain outside the generic framework package.

## Next task

Begin amended Phase 2 only when explicitly authorized: add observability while
composing persistent identity, traits, and self-model into the Entity without
folding provider concerns into the kernel.

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
- `tests/test_signal.py` — Signal unit tests.
- `tests/test_entity.py` — Entity and registry unit tests.
- `tests/test_task_action.py` — Task and Action unit tests.
- `tests/test_scheduler_runtime.py` — Scheduler and Runtime unit tests.
- `tests/test_integration.py` — public-API integration tests.
- `docs/ARCHITECTURE.md` — implemented architecture boundary.
- `docs/CHARACTER_ARCHITECTURE.md` — persistent character design and phased
  acceptance criteria.
- `docs/ROADMAP.md` — phase status and deferrals.
- `docs/DECISIONS.md` — accepted architecture decisions.
- `docs/CURRENT_STATE.md` — concise handoff record.
- `src/echo/entity/` — provider-independent character base value types.
- `entities/bit/` — Bit reference configuration and prompts.
- `prompts/` — generic model-boundary contracts.
