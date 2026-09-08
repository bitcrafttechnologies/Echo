# Architecture

`Echo_Plan.md` is the architectural source of truth. The persistent character
amendment is detailed in `CHARACTER_ARCHITECTURE.md`. This document records the
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

`Runtime.emit` is the Phase 1 execution boundary. It places a Signal in the
Scheduler and processes priority-ordered work through that Signal before
returning. Each matching handler receives its own Task. A handler may update its
Entity state, return Actions, call `Entity.action`, or emit another Signal.

The Runtime retains completed Task and Action records for inspection. An
Entity's `active_tasks` contains only currently executing handler Tasks.

Phase 2A adds direct structured instrumentation to this path. A Runtime writes
typed records through a small `LogSink` interface and uses an
`InMemoryLogSink` by default. Records contain a timestamp, event type,
applicable Entity/Signal/Task/Action IDs, and metadata. Sink failures are
isolated from runtime control flow. This is instrumentation, not a second event
system.

Phase 2B adds a bounded `SignalHistory` owned by each Runtime. It snapshots
Signal transport fields before handler execution and records the routing result
afterward, including matched Entities and resulting Tasks. Queries operate on
the in-memory snapshots by recency, ID, type, and source. Capacity is explicit,
and insertion order determines oldest-first eviction. There is no persistence
or replay behavior in this component.

## Boundaries

The implemented kernel contains no LLM, provider routing, persistence, web API,
Console, ROS, or Medulla transport. Actions are structured intent records;
execution against the outside world belongs to the later Medulla boundary.
The Phase 2A `action.executed` observation identifies execution at the Runtime
intent boundary, not an external side effect.

The implementation favors dataclasses, `asyncio`, explicit method calls, and
composition. Decorators are only registration helpers.

The amendment adds provider-independent character value types under
`echo.entity`, but does not yet attach them to `echo.core.Entity`, persist them,
or route them through inference. Bit's reference configuration is outside the
generic package under `entities/bit/`. This preserves one public Entity actor
while reserving explicit ownership boundaries for Phase 2 and later.

Handler matching accepts either a Signal type string or a Signal subclass.
Class matching uses normal Python `isinstance` behavior, so a base Signal
handler can observe every Signal while a typed handler remains specific.

## Dependency direction

Core primitives do not depend on optional infrastructure. `Entity` owns a
handler registry and can be attached to a `Runtime`; `Runtime` owns a
`Scheduler` and registered entities. Optional systems added later must depend
on this kernel rather than redefine it.

Character state follows the same direction: identity, traits, self-model,
internal state, drives, relationships, and memory belong to Echo. Providers
receive compact context and return untrusted proposals. Behavior policy, not a
model, authorizes Actions. Identity remains independent of provider and body.
