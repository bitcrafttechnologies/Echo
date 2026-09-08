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

## ADR-006: Echo owns persistent Entity continuity

Status: accepted

Identity, traits, internal state, drives, relationships, memories, preferences,
goals, and embodiment awareness are Echo-owned data. Inference providers are
replaceable cognitive resources that receive selected context and return
untrusted proposals. They cannot directly mutate persistent character or
authorize embodied Actions.

Character is delivered vertically through Phases 2–10 rather than as an
isolated subsystem added after inference. Bit is the first reference
configuration under `entities/bit/`; generic runtime code must not contain
Bit-specific assumptions. The full decision and consequences are documented in
`CHARACTER_ARCHITECTURE.md`.

## ADR-007: RuntimeService is the transport-independent control boundary

Status: accepted

CLI, tests, and future network adapters control Echo through `RuntimeService`.
The service uses typed domain requests, detached results, explicit state-write
allowlists, and stable domain errors. It contains no HTTP or FastAPI concepts.
The Runtime remains responsible for lifecycle-sensitive operations such as live
Task cancellation and for retaining readable logs independently of its external
sink.

## ADR-008: Developer commands use closed schemas and explicit dispatch

Status: accepted

Developer operations are immutable command dataclasses dispatched through
explicit type branches to `RuntimeService`. A small text parser may create
those same schemas for CLI use; Console code can construct them directly.
Parsing is limited to tokenization and JSON data. Command input cannot resolve
arbitrary functions, import modules, invoke a shell, or execute Python.

## ADR-009: Live events use isolated bounded subscriber queues

Status: accepted

Runtime lifecycle observations are synchronously classified and offered with
`put_nowait()` to private bounded queues. Subscribers are never awaited by the
Runtime, cannot share mutable event metadata, and select an explicit
drop-oldest or drop-newest overflow policy with drop accounting. Consumer or
subscription failure cannot enter Runtime control flow. The broker remains
in-process and transport-neutral; WebSockets are deferred to a later adapter.
