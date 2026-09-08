# Current State

## Current implementation

The Phase 1 kernel primitives are connected by an async priority Scheduler and
Runtime. Awaiting emission dispatches handlers as Tasks and records Actions.

## Completed

- Phase 0 repository initialization and cleanup.
- Phase 1A Signal implementation and unit tests.
- Phase 1B Entity and handler registry implementation and unit tests.
- Phase 1C Task and Action implementation and unit tests.
- Phase 1D Scheduler and Runtime implementation and unit tests.

## In progress

- Phase 1E end-to-end integration tests and final Phase 1 verification.

## Known issues

- State is in memory; persistence is deferred.
- Actions are recorded intent and have no external executor yet.
- There is no long-running process host; awaited emission is the Phase 1 API.

## Architecture decisions

- Scheduler priority is stable FIFO within each priority class.
- Each Entity handler invocation owns one observable Task record.
- Nested Entity emission remains in the same structured dispatch call.

## Next task

Add black-box kernel integration coverage and verify the complete Phase 1 API.

## Important files

- `Echo_Plan.md`
- `src/echo/core/scheduler.py`
- `src/echo/core/runtime.py`
- `tests/test_scheduler_runtime.py`
