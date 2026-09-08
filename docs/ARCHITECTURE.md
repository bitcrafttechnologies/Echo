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

Phase 2C adds independently bounded Task and Action histories. `TaskHistory`
keeps references to the Task objects already owned by the Runtime, so status
transitions stay current without a duplicate mutable lifecycle model; reads
produce isolated snapshots. `ActionHistory` keeps the Action object plus only
the lifecycle data absent from that primitive: execution time, status,
Signal association, result, and error. The Runtime's compatibility lists use
the same bounds as their histories.

Phase 2D exposes this observability through `Runtime.inspect()` and its
`snapshot()` alias. The result is a detached JSON-safe dictionary covering
runtime status and uptime, Entity state, active and queued work, recent
activity and errors, scheduler state, and handler registrations. Scheduler and
registry components provide read-only summaries; inspection never consumes a
Signal or exposes their implementation objects. The API has no HTTP, database,
or UI dependency.

Phase 2E verifies the complete observability path as one causal chain:

```text
Signal ID
  -> routing record
  -> Task ID and lifecycle
  -> Action ID and execution record
  -> Entity state change
  -> structured logs + bounded histories
  -> Runtime inspection snapshot
```

Each record links through the applicable Signal, Task, Action, and Entity IDs.
The Runtime remains the single instrumentation point; Entity Action collection
does not emit a second copy when the same Action is returned by a handler.
Compatibility lists retain the original kernel objects within the same bounds
as their history APIs, while inspection returns detached data only.

The Phase 2 character slice is also composed into the public `Entity`:
immutable `EntityIdentity`, immutable `TraitProfile`, and mutable `SelfModel`
with enforced Entity-ID agreement. Identity and traits have no public mutation
setter. `CharacterMutationAuditRecord` supplies proposed/accepted/rejected,
target, evidence, reason, and before/after vocabulary without creating a
mutation service or persistence store. Runtime inspection includes this
character slice separately from ordinary mutable Entity state.

Phase 3A adds `RuntimeService` as the supported application boundary above the
kernel. It accepts domain objects and typed request/query records rather than
HTTP concepts. It returns detached Entity and state results plus existing safe
history snapshots, and reports invalid operations through a structured domain
error hierarchy. Per-Entity state mutations require an explicit key allowlist.
Live Task cancellation is coordinated by the Runtime and completes the Task's
lifecycle before returning. A bounded Runtime-owned log read history keeps API
log access independent of external sink capabilities.

Phase 3B adds a structured developer-command layer above `RuntimeService`:

```text
typed command object or minimal text
  -> closed command schema
  -> explicit DeveloperCommandDispatcher branch
  -> RuntimeService operation
  -> structured CommandResult or command error
```

This preserves two presentation paths without duplicating runtime behavior:
Console code can construct command objects directly, while a future CLI can
use or extend the small text parser. Text parsing is limited to `shlex`
tokenization and JSON-object decoding. Command names never resolve dynamically
to Python callables, and there is no `eval`, `exec`, import, or shell path.

## Boundaries

The implemented kernel, service, and command layer contain no LLM, provider
routing, persistence, web API, FastAPI, Console, ROS, or Medulla transport.
Actions are structured intent records; execution against the outside world
belongs to the later Medulla boundary.
The Phase 2A `action.executed` observation identifies execution at the Runtime
intent boundary, not an external side effect.

The implementation favors dataclasses, `asyncio`, explicit method calls, and
composition. Decorators are only registration helpers.

The amendment adds provider-independent character value types under
`echo.entity`. Phase 2 attaches identity, traits, and self-model to
`echo.core.Entity`; it does not persist them or route them through inference.
Bit's reference configuration remains outside the generic package under
`entities/bit/`. This preserves one public Entity actor while reserving later
character slices for their roadmap phases.

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
