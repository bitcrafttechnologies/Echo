# Current State

## Current implementation

Echo Phase 4C consists of a minimal, standard-library Python kernel, an
optional FastAPI HTTP and WebSocket adapter, and a separate SvelteKit
development console shell. The documented, transport-agnostic runtime
contract, structured developer commands, and live event subscriptions remain
the only control boundary used by the transport and presentation layers. Phase
3 character state, drives, Signal influence, and attention candidates are
implemented in memory.

The `0.1-amendment/character_plan` branch also records the persistent character
architecture and adds its provider-independent base vocabulary. Phase 2 now
composes identity, traits, and self-model into the Entity. Phase 3 now composes
internal state, drive baselines and activation, and attention candidates.
Relationships, Bit configuration loading, and prompts remain scaffolding for
their later roadmap phases.

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
- `EntityIdentity`, `TraitProfile`, `TraitEvidence`, and `SelfModel`
- `CharacterMutationAuditRecord`, `CharacterMutationTarget`, and
  `CharacterMutationDecision`
- `RuntimeService`, its typed request/query/result objects, and its
  `RuntimeServiceError` domain-error hierarchy
- `RuntimeServiceProtocol`, the stable structural interface for adapters
- `DeveloperCommandDispatcher`, typed command schemas, `CommandResult`, the
  `DeveloperCommandError` hierarchy, and `parse_developer_command`
- `RuntimeEventSubscription`, `RuntimeSubscriptionRequest`,
  `RuntimeSubscriptionEvent`, event categories, backpressure policies, and
  subscription errors
- `InternalState`, `DriveProfile`, `SignalInfluence`, `AttentionProposal`, and
  `AttentionCandidate`

The optional `echo.adapters.fastapi` module exports `create_app()`, providing
HTTP inspection/control and `/events` WebSocket streaming. It is not imported
by the public package root, so Echo Core still imports and runs when FastAPI is
unavailable.

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

`RuntimeService` is the supported programmatic control boundary for CLI,
tests, and network adapters. It exposes runtime status; Entity listing
and inspection; Signal emission, listing, and inspection; Task listing,
inspection, and live cancellation; Action listing and inspection; detached
Entity state reads; explicitly allowlisted state writes; and structured log
queries. Inputs and outputs are typed where that clarifies the contract.
Returned state, metadata, and history values are detached from Runtime-owned
mutable structures. Missing resources, invalid requests, forbidden state
writes, failed Signal processing, and invalid Task cancellation use structured
domain errors with stable codes.

Live Task cancellation targets the executing handler and waits for lifecycle
cleanup, so the returned Task snapshot is already `cancelled`. The Runtime also
retains a bounded internal read history of log events, allowing service log
queries to work independently of the configured external `LogSink`.

`DeveloperCommandDispatcher` maps explicit command dataclasses to
`RuntimeService` calls for runtime status, Entity inspection, Signal listing,
inspection and injection, Task listing, inspection and cancellation, Action
listing, Entity state reads and allowlisted writes, and logs. Direct command
objects are suitable for a future Console; a minimal text parser provides the
same schemas for a future CLI. Results and failures have structured `to_dict()`
representations. The parser uses `shlex` for tokenization and `json.loads` for
object values. It has no dynamic imports, `eval`, `exec`, shell invocation, or
arbitrary Python execution path.

`RuntimeService.subscribe_events()` creates an isolated subscription to future
Runtime activity. Each structured log event is classified as Signal receipt,
Signal routing, Task lifecycle, Action lifecycle, state change, Runtime change,
or error. The `logs` category is an all-event selector. Publications receive a
global sequence number and are copied into a private bounded `asyncio.Queue`
for each subscriber. Runtime publication uses only `put_nowait`: subscribers
are never awaited. Queue overflow uses an explicit drop-oldest or drop-newest
policy and exposes a dropped-event count. A closed, failed, or slow consumer
cannot stop Runtime execution or affect another subscriber. The mechanism has
no socket, WebSocket, HTTP, or framework dependency.

The public Phase 3 contract is documented in `docs/RUNTIME_API.md`. It defines
service operations, request and response conventions, stable error codes,
developer command mappings and text grammar, event subscription schemas, and
the boundary between public types and private Runtime implementation details.
The executable `examples/runtime_api_contract.py` example is run by the test
suite.

An Entity now owns detached normalized `InternalState` views, immutable
`DriveProfile` baselines, read-only drive activation views, and bounded
`AttentionCandidate` history. `RuntimeService.apply_signal_influence()` is the
explicit mutation path: it requires a retained Signal, validates all dimensions
atomically, applies bounded deltas, lets active drives contribute to attention
scoring, and publishes the change through logs and subscriptions. It never
creates a Task, goal, intention, or Action.

`create_app(runtime_service)` exposes health, Runtime status, Entity list and
inspection, Signal emission/list/inspection, Task list/inspection/cancellation,
Action list/inspection, Entity state reads and allowlisted writes, and log
queries. Routes translate inputs into existing service dataclasses and
serialize service results; they do not access Runtime internals. Domain errors
retain their stable codes and details in structured HTTP error responses.

Each `/events` client receives a private subscription created through
`RuntimeService.subscribe_events()`. Events use the existing structured Phase 3
envelope. Repeated `category` query parameters provide basic filtering, while
`max_queue_size` and `backpressure` configure the existing bounded queue.
Socket writes consume the queue in a separate coroutine from Runtime event
publication, so a slow client cannot block Echo. A concurrent receive loop
detects disconnects; guaranteed cleanup closes and removes the subscription.
Reconnects create fresh future-only subscriptions.

The `console/` SvelteKit application provides a responsive Runtime header,
navigation sidebar, main workspace, separate API and event-stream connection
states, and a small recent-event view. Initial navigation contains Overview,
Signals, Tasks, Entity, Logs, and Chat; only the shell and Overview pulse are
implemented in Phase 4C. The browser client polls `/runtime/status`, connects
to `/events`, retries with bounded delay, and remains navigable while Echo is
offline. Local development uses a Vite proxy by default, preserving the
FastAPI adapter as the network boundary.

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

The public `Entity` now composes immutable identity, an immutable trait
snapshot, and an embodiment-independent self-model. Entity IDs must agree
across all three. Identity and traits cannot be replaced through public
setters; changing embodiment state does not change Entity identity. Structured
mutation-audit vocabulary records target, decision, source, evidence, reason,
before/after values, metadata, and time, but Phase 2 adds no mutation service or
audit store.

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
- Phase 2E (`0.2.5`): end-to-end observability integration and cleanup.
- Phase 2E: exact causal-ID verification across Signal routing, Task lifecycle,
  Action lifecycle, state logs, histories, and runtime inspection.
- Phase 2E: identity, traits, and self-model composed into the public Entity
  with Entity-ID invariants and mutation audit vocabulary.
- Phase 2E: exact event-count coverage confirms Action collection does not
  duplicate creation/execution logging.
- Phase 3 base branch (`0.3`) established from completed Phase 2.
- Phase 3A (`0.3.1`): transport-agnostic `RuntimeService` control boundary.
- Phase 3A: detached Entity, Signal, Task, Action, state, status, and log reads.
- Phase 3A: structured Signal emission, allowlisted state updates, and live
  Task cancellation.
- Phase 3A: clean domain failures and live-runtime coverage of every operation.
- Phase 3B (`0.3.2`): typed developer commands resolved exclusively through
  `RuntimeService`.
- Phase 3B: minimal CLI-oriented text grammar plus direct Console-oriented
  command objects.
- Phase 3B: structured parse, validation, unknown-command, and execution errors.
- Phase 3B: valid, malformed, live cancellation, and arbitrary-Python rejection
  tests.
- Phase 3C (`0.3.3`): transport-neutral live Runtime event subscriptions.
- Phase 3C: ordered, detached event envelopes and category filtering.
- Phase 3C: isolated bounded queues with explicit backpressure policy and drop
  accounting.
- Phase 3C: ordered consumer, category, slow-subscriber, failed-consumer, and
  invalid-subscription coverage.
- Phase 3D (`0.3.4`): documented and stabilized runtime API contract.
- Phase 3D: public `RuntimeServiceProtocol`, response/error conventions,
  developer command mapping, and event subscription schemas.
- Phase 3D: executable contract example verified by the test suite.
- Phase 3D: internal state, drive activation, Signal influence, and attention
  candidates composed into Entity and exposed through `RuntimeService`.
- Phase 3D: atomic influence validation, bounded control changes, drive-weighted
  attention, observability, and no-automatic-Action coverage.
- Phase 4 base branch (`0.4`) established from completed Phase 3D.
- Phase 4A (`0.4.1`): optional FastAPI adapter over `RuntimeServiceProtocol`.
- Phase 4A: health, Runtime, Entity, Signal, Task, Action, state, and log HTTP
  endpoints.
- Phase 4A: structured service-error-to-HTTP mapping and endpoint response
  coverage.
- Phase 4A: subprocess verification that Echo Core runs while FastAPI imports
  are blocked.
- Phase 4B (`0.4.2`): `/events` WebSocket streaming backed by Phase 3 event
  subscriptions.
- Phase 4B: structured live Signal, Task, Action, state, Runtime, log, and error
  envelopes with optional category filtering.
- Phase 4B: clean disconnect removal, future-only reconnect behavior, bounded
  slow-client backpressure, and runtime-isolation coverage.
- Phase 4C (`0.4.3`): initial responsive SvelteKit Echo Console shell.
- Phase 4C: Runtime status header, Overview workspace, API/WebSocket connection
  indicators, and Overview/Signals/Tasks/Entity/Logs/Chat navigation.
- Phase 4C: HTTP status polling, WebSocket event consumption, reconnect/backoff,
  explicit offline state, client-helper tests, type checks, and production
  build verification.

## In progress

Nothing. Phase 4 is complete through Phase 4C.

## Known issues

- Entity state, runtime histories, and structured logs are in memory only.
- Signal replay storage is not implemented.
- Actions have no Medulla executor or transport.
- There is no long-running process host or CLI executable.
- Console inspectors beyond the Overview shell and Chat behavior are not yet
  implemented.
- There are no provider, model, memory, ROS, or robotics integrations.
- Bit YAML configuration is not yet loaded into `echo.core.Entity`.
- There is no character persistence, relationship integration, context builder,
  consolidation service, behavior policy, or character mutation audit store.
- Scheduler `periodic` is a priority class, not a recurring timer facility.
- Signal payloads must already contain JSON-compatible values for `to_json`.

These are roadmap deferrals, not missing Phase 4C acceptance criteria.

## Architecture decisions

- `Echo_Plan.md` remains the architectural source of truth.
- Python 3.11+ and the standard library are sufficient for Echo Core; FastAPI
  is isolated in an optional adapter dependency.
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
- `RuntimeService` is the stable control boundary; CLI and future network
  adapters depend on it instead of reaching into Runtime-owned structures.
- Developer commands are closed, typed schemas with explicit dispatch. Text is
  only a presentation adapter and cannot select arbitrary callables.
- Runtime events fan out through isolated bounded queues; publication never
  awaits subscribers, and overflow behavior is explicit and observable.
- `RuntimeServiceProtocol` plus `docs/RUNTIME_API.md` define the stable
  application contract; Runtime coordination and storage stay private.
- The FastAPI app is created around `RuntimeServiceProtocol`; routes translate
  transport shapes only and never read or mutate Runtime internals.
- Each WebSocket owns one bounded service subscription. Disconnect cleanup
  closes and removes it, while socket latency remains outside Runtime
  publication flow.
- The SvelteKit console is a separate optional development surface. It depends
  outward on the HTTP/WebSocket adapter; Echo Core has no Node or browser
  dependency.
- Signals may affect internal state and drive activation only through explicit,
  validated influence requests linked to retained Signal IDs.
- Drives influence attention candidate scoring but never authorize Actions.
- State writes are denied unless the service is configured with an explicit
  per-Entity key allowlist.
- Runtime-owned log read history is independent of external sink behavior.
- Entity owns immutable identity and trait snapshots plus its self-model;
  construction rejects cross-Entity character state.
- Mutation audit types are vocabulary only; approval policy, mutation services,
  durable audit storage, and provider integration remain later work.
- Requested character base modules are concrete, tested value types rather than
  empty interfaces; all other future packages remain unscaffolded.
- Echo owns Entity continuity; providers return untrusted cognitive proposals.
- Identity is independent of inference provider and embodiment.
- Bit-specific seeds remain outside the generic framework package.

## Next task

Stop after Phase 4C. Do not begin Phase 4D without explicit authorization.
Detailed Console inspectors, Chat behavior, the CLI executable, providers,
persistence, replay, relationships, and behavior policy remain deferred.

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
- `src/echo/runtime_service.py` — transport-agnostic runtime service API,
  request/result types, and domain errors.
- `src/echo/developer_commands.py` — typed developer commands, explicit service
  dispatcher, minimal text parser, and command errors.
- `src/echo/runtime_events.py` — event categories, subscription envelopes,
  bounded subscriber queues, and non-blocking broker.
- `src/echo/adapters/fastapi.py` — optional HTTP adapter and app factory.
- `console/src/routes/+page.svelte` — initial Echo Console layout, navigation,
  connection lifecycle, and live activity view.
- `console/src/lib/echo-client.ts` — HTTP status client and WebSocket URL helpers.
- `console/vite.config.ts` — local HTTP and WebSocket development proxy.
- `src/echo/entity/attention.py` — attention proposal and retained candidate
  schemas.
- `src/echo/entity/influence.py` — bounded Signal influence schema.
- `src/echo/core/runtime_log.py` — structured events and log sink interface.
- `src/echo/core/signal_history.py` — bounded Signal snapshots and queries.
- `src/echo/core/task_history.py` — reference-backed Task lifecycle history.
- `src/echo/core/action_history.py` — bounded Action lifecycle history.
- `src/echo/core/inspection.py` — JSON-safe inspection conversion.
- `src/echo/entity/audit.py` — character mutation audit vocabulary.
- `tests/test_signal.py` — Signal unit tests.
- `tests/test_entity.py` — Entity and registry unit tests.
- `tests/test_task_action.py` — Task and Action unit tests.
- `tests/test_scheduler_runtime.py` — Scheduler and Runtime unit tests.
- `tests/test_integration.py` — public-API integration tests.
- `tests/test_runtime_logging.py` — Phase 2A observability coverage.
- `tests/test_signal_history.py` — Phase 2B Signal history coverage.
- `tests/test_task_action_history.py` — Phase 2C history coverage.
- `tests/test_runtime_inspection.py` — Phase 2D snapshot coverage.
- `tests/test_phase2_observability.py` — Phase 2 acceptance and character tests.
- `tests/test_runtime_service.py` — every Phase 3A operation against a live
  Runtime plus domain-error behavior.
- `tests/test_developer_commands.py` — Phase 3B command dispatch, grammar,
  validation, cancellation, and arbitrary-execution rejection.
- `tests/test_runtime_events.py` — Phase 3C ordering, filtering, backpressure,
  failure isolation, and subscription validation.
- `tests/test_phase3_character.py` — Phase 3 character composition, influence,
  attention, atomicity, and observability.
- `tests/test_api_contract.py` — executes the documented contract example.
- `tests/test_fastapi_adapter.py` — HTTP response/error mapping, WebSocket
  streaming lifecycle, slow-client behavior, and optional dependency isolation
  coverage.
- `console/src/lib/echo-client.test.ts` — frontend API, event URL, and display
  helper coverage.
- `docs/RUNTIME_API.md` — stable Phase 3 service, command, subscription, and
  response/error contract.
- `docs/HTTP_API.md` — Phase 4A HTTP and Phase 4B WebSocket contracts.
- `examples/runtime_api_contract.py` — executable documented contract example.
- `docs/ARCHITECTURE.md` — implemented architecture boundary.
- `docs/CHARACTER_ARCHITECTURE.md` — persistent character design and phased
  acceptance criteria.
- `docs/ROADMAP.md` — phase status and deferrals.
- `docs/DECISIONS.md` — accepted architecture decisions.
- `docs/CURRENT_STATE.md` — concise handoff record.
- `src/echo/entity/` — provider-independent character base value types.
- `entities/bit/` — Bit reference configuration and prompts.
- `prompts/` — generic model-boundary contracts.
