# Architecture Decisions

## ADR-001: Standard-library Python kernel

Status: accepted

Phase 1 uses Python 3.11+ and the standard library. Dataclasses and `asyncio`
cover the required typed data and event scheduling without a runtime framework.

## ADR-002: Source layout without speculative packages

Status: accepted

The project uses `src/echo/core` and creates only modules required by Phase 1.
Provider, Medulla, API, state, CLI, and configuration packages will appear only
when their phases require them.

## ADR-003: Standard-library test runner

Status: accepted

Tests use `unittest`, including `IsolatedAsyncioTestCase`, so the clean kernel
can be verified without installing development dependencies.

## ADR-004: Awaited emission is the Phase 1 runtime boundary

Status: accepted

`Runtime.emit` schedules and processes through the emitted Signal before it
returns. This makes tests and callers deterministic without introducing a
background service lifecycle. Handlers may emit nested Signals; dispatch stays
structured within the awaiting call.

## ADR-005: Actions are observable intent, not external execution

Status: accepted

Phase 1 records Actions with Entity and Task attribution. Hardware, network,
and tool execution remain behind the future Medulla boundary.
