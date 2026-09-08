# Current State

## Current implementation

The kernel contains Signal, Entity, and an explicit handler registry. Entities
own identity, mutable state, and ordered handler registrations.

## Completed

- Phase 0 repository initialization and cleanup.
- Phase 1A Signal implementation and unit tests.
- Phase 1B Entity and handler registry implementation and unit tests.

## In progress

- Phase 1C Task and Action.

## Known issues

- Handlers are registered but not dispatched until the Runtime phase.
- State is in memory; persistence is deferred.

## Architecture decisions

- Standard-library dataclasses and JSON represent Phase 1 signal data.
- Handler decorators are direct registration helpers with no hidden dispatch.
- Both string event names and Signal subclasses are valid handler keys.

## Next task

Implement Task lifecycle values and structured Action records.

## Important files

- `Echo_Plan.md`
- `src/echo/core/entity.py`
- `src/echo/core/handlers.py`
- `tests/test_entity.py`
