# Roadmap

The authoritative roadmap is `Echo_Plan.md`, with character details in
`CHARACTER_ARCHITECTURE.md`. Work is delivered incrementally. The requested
character amendment establishes only base types, configuration, and prompts;
runtime integration still follows the phases below.

## Phase 0 — repository cleanup and architecture docs

- Completed on branch `0.1`.
- Established the Python package and test layout.
- Recorded architecture, decisions, roadmap, and current state.
- Excluded local model artifacts and generated files from version control.

## Phase 1 — kernel

- Phase 1A (`0.1.1`): Signal — completed.
- Phase 1B (`0.1.2`): Entity and handler registry — completed.
- Phase 1C (`0.1.3`): Task and Action — completed.
- Phase 1D (`0.1.4`): Scheduler and Runtime — completed.
- Phase 1E (`0.1.5`): integration tests — completed.

Phase 1 stops here.

## Phase 2 — observability

- Phase 2A (`0.2.1`): structured runtime logging — completed.
- Phase 2B (`0.2.2`): bounded in-memory Signal history — completed.
- Phase 2C (`0.2.3`): bounded Task and Action history — completed.
- Phase 2D (`0.2.4`): runtime snapshot and inspection — completed.
- Phase 2E (`0.2.5`): observability integration and cleanup — completed.
- Added typed log records, a replaceable sink interface, and chronological
  in-memory storage.
- Instrumented Signal routing, Task and Action lifecycle, Entity state changes,
  Runtime lifecycle, and handler errors.
- Added safe Signal snapshots, routing results, latest and filtered queries,
  and predictable oldest-first eviction.
- Added reference-backed Task lifecycle snapshots and Action creation/execution
  history with independently configurable bounds.
- Added a detached JSON-safe view of runtime status, uptime, Entities, active
  work, queues, recent activity, errors, scheduler state, and handlers.
- Composed Phase 2 identity, traits, and self-model into the public Entity and
  added character mutation audit vocabulary.
- Verified one Signal-to-state causal chain across routing, Task, Action, logs,
  histories, and runtime inspection with exact ID linkage and no duplicate
  lifecycle events.

Phase 2 stops here. Phase 3 requires separate authorization.

## Phase 3 — runtime API

- Phase 3A (`0.3.1`): internal runtime service API — completed.
- Added a transport-agnostic `RuntimeService` for status, Entities, Signals,
  Tasks, Actions, Entity state, allowlisted state updates, and logs.
- Added structured request/query/result objects and clean domain errors.
- Added deterministic cancellation of live handler Tasks.
- Exercised every service operation against a live Runtime.
- Phase 3B (`0.3.2`): structured developer commands — completed.
- Added typed command schemas and an explicit dispatcher over `RuntimeService`.
- Added a minimal `shlex` and JSON text grammar suitable for a future CLI while
  keeping direct command objects suitable for a future Console.
- Added structured command errors and arbitrary-Python execution rejection.
- Phase 3C (`0.3.3`): runtime event subscriptions — completed.
- Added ordered event envelopes for Signal, Task, Action, state, Runtime, log,
  and error activity.
- Added transport-neutral per-subscriber bounded queues, category filters,
  explicit overflow policies, and drop accounting.
- Verified ordered delivery and isolation of slow and failed consumers.
- Phase 3D (`0.3.4`): runtime API contract and Phase 3 character completion —
  completed.
- Documented stable service operations, commands, subscriptions, results,
  errors, and public/private boundaries in `RUNTIME_API.md`.
- Added `RuntimeServiceProtocol` and an executable contract example.
- Composed internal state, drives, explicit Signal influence, and attention
  candidates into Entity without automatic Actions.

Phase 3 stops after Phase 3D. Phase 4 requires separate authorization.
WebSockets, FastAPI, the Console, and the CLI executable are not part of Phase
3D.

## Phase 4 — Echo Console

- Phase 4A (`0.4.1`): FastAPI adapter — completed.
- Added an optional FastAPI dependency and an app factory around
  `RuntimeServiceProtocol`.
- Added HTTP health, Runtime status, Entity, Signal, Task, Action, state, and
  log endpoints with structured service-error responses.
- Verified endpoint responses and that Echo Core still runs when FastAPI is
  unavailable.
- Phase 4B (`0.4.2`): WebSocket event streaming — completed.
- Added a `/events` endpoint backed exclusively by Phase 3 bounded event
  subscriptions, with category and backpressure query options.
- Verified structured Signal, Task, and Action delivery; disconnect cleanup;
  reconnect behavior; filtering; and slow-client isolation.
- Phase 4C (`0.4.3`): initial Echo Console frontend shell — completed.
- Added a lightweight SvelteKit interface with Runtime status, API and event
  connection state, responsive navigation, a main content area, and recent
  live activity.
- Connected through the Phase 4A HTTP and Phase 4B WebSocket APIs with
  retry/offline behavior and intentionally deferred inspector placeholders.
- Phase 4D (`0.4.4`): Signal Inspector UI — completed.
- Added pauseable live Signal arrivals, retained history, type/source filters,
  and a structured selection detail view.
- Added Signal identity, payload, metadata, routing outcome, and related
  Task/Action IDs using existing HTTP and WebSocket APIs; replay remains
  deferred.

Phase 4 stops after Phase 4D. Remaining inspectors, Chat behavior, relationship
integration, and later Phase 4 subphases require separate authorization.

## Deferred

Remaining runtime APIs, Console, model providers,
configuration, persistence, replay, Medulla transports, edge ML, robotics, and
advanced task behavior remain deferred to the phases defined in the plan.

## Persistent character vertical track

- Phase 2: persistent identity, trait snapshots, self-model, and audit terms —
  completed in memory; durable persistence remains deferred.
- Phase 3: internal state, drives, Signal influence, and attention candidates —
  completed in memory; durable persistence remains deferred.
- Phase 4: relationship models and per-person social context.
- Phase 5: distinct working, episodic, semantic, preference, and relationship
  memories.
- Phase 6: retention scoring, consolidation, and learned preferences.
- Phase 7: compact, provider-neutral Character Context Builder and retrieval.
- Phase 8: proposed intentions, behavior arbitration, and autonomous curiosity.
- Phase 9: evidence-based, bounded, auditable character evolution.
- Phase 10 and later: embodiment signals constrain state, goals, and behavior.

Provider and embodiment continuity tests are acceptance criteria throughout,
not a final integration exercise.
