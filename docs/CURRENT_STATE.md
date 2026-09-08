# Current State

## Current implementation

The repository contains the source plan, project documentation, and a typed,
timestamped Signal primitive with dictionary and JSON serialization.

## Completed

- Phase 0 repository initialization and cleanup.
- Phase 1A Signal implementation and unit tests.
- Python packaging metadata and persistent architecture records.

## In progress

- Phase 1B Entity and handler registry.

## Known issues

- Signals are in-memory values; persistence and replay storage are deferred.

## Architecture decisions

- Standard-library dataclasses and JSON represent Phase 1 signal data.
- Domain signals use small typed subclasses without a schema framework.

## Next task

Implement explicit handler registration and the Entity public abstraction.

## Important files

- `Echo_Plan.md`
- `src/echo/core/signal.py`
- `tests/test_signal.py`
