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

Phase 3 stops after Phase 3B pending separate authorization for later
subphases. FastAPI and the CLI executable are not part of Phase 3B.

## Deferred

Remaining runtime APIs, Console, model providers,
configuration, persistence, replay, Medulla transports, edge ML, robotics, and
advanced task behavior remain deferred to the phases defined in the plan.

## Persistent character vertical track

- Phase 2: persistent identity, trait snapshots, self-model, and audit terms —
  completed in memory; durable persistence remains deferred.
- Phase 3: internal state, drives, Signal influence, and attention candidates.
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
