# Current State

## Current implementation

The kernel contains Signal, Entity, handler registration, Task lifecycle state,
and structured Action intent records.

## Completed

- Phase 0 repository initialization and cleanup.
- Phase 1A Signal implementation and unit tests.
- Phase 1B Entity and handler registry implementation and unit tests.
- Phase 1C Task and Action implementation and unit tests.

## In progress

- Phase 1D Scheduler and Runtime.

## Known issues

- Handlers are registered but not dispatched until the Runtime phase.
- State is in memory; persistence is deferred.
- Actions are recorded intent and have no external executor yet.

## Architecture decisions

- Task lifecycle transitions are explicit and reject invalid state changes.
- Parent-child relationships store stable task IDs instead of object graphs.
- Actions carry parameters plus optional Entity and Task attribution.

## Next task

Implement priority scheduling and unified asynchronous dispatch.

## Important files

- `Echo_Plan.md`
- `src/echo/core/task.py`
- `src/echo/core/action.py`
- `tests/test_task_action.py`
