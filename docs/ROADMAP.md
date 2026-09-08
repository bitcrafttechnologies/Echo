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
- Added typed log records, a replaceable sink interface, and chronological
  in-memory storage.
- Instrumented Signal routing, Task and Action lifecycle, Entity state changes,
  Runtime lifecycle, and handler errors.
- Added safe Signal snapshots, routing results, latest and filtered queries,
  and predictable oldest-first eviction.
- Added reference-backed Task lifecycle snapshots and Action creation/execution
  history with independently configurable bounds.

Later Phase 2 and roadmap work require separate authorization.

## Deferred

Remaining observability infrastructure, runtime APIs, Console, model providers,
configuration, persistence, replay, Medulla transports, edge ML, robotics, and
advanced task behavior remain deferred to the phases defined in the plan.

## Persistent character vertical track

- Phase 2: persistent identity, trait snapshots, self-model, and audit terms.
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
