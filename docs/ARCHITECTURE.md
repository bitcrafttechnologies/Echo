# Architecture

`Echo_Plan.md` is the architectural source of truth. This document records the
implemented boundary so that code and roadmap can be compared quickly.

## Kernel model

Echo is one asynchronous, event-driven runtime centered on four primitives:

1. An `Entity` is the persistent intelligent actor and owns identity, state,
   active tasks, and handlers.
2. A `Signal` says that something happened and carries source, time, payload,
   and metadata.
3. A `Task` tracks an ongoing unit of work and its lifecycle.
4. An `Action` records something an Entity intends to do.

The Phase 1 data flow is:

```text
Signal -> Scheduler -> Runtime -> Entity handler -> Task -> Action
```

The scheduler and handler registry are small kernel mechanisms, not additional
domain layers. The runtime coordinates them and keeps observable in-memory
records of processed signals, tasks, and actions.

## Boundaries

Phase 1 contains no LLM, provider routing, persistence, web API, Console, ROS,
or Medulla transport. Actions are structured intent records; execution against
the outside world belongs to the later Medulla boundary.

The implementation favors dataclasses, `asyncio`, explicit method calls, and
composition. Decorators are only registration helpers.

## Dependency direction

Core primitives do not depend on optional infrastructure. `Entity` owns a
handler registry and can be attached to a `Runtime`; `Runtime` owns a
`Scheduler` and registered entities. Optional systems added later must depend
on this kernel rather than redefine it.

