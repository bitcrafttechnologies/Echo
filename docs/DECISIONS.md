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

## ADR-010: The Phase 3 application contract is explicit and structural

Status: accepted

`RuntimeServiceProtocol` and `docs/RUNTIME_API.md` define the public control and
inspection boundary. Stable request, result, error, command, and subscription
types are exported from `echo`. Runtime queues, histories, broker internals,
retention structures, mutable compatibility lists, and private coordination
methods are not public API. Transport adapters own any HTTP, WebSocket, or CLI
encoding and depend inward on the structural service protocol.

## ADR-011: Signals influence character through a bounded service operation

Status: accepted

Phase 3 composes normalized internal state, immutable drive baselines, bounded
drive activation, and attention candidates into Entity. A retained Signal may
change control state only through an explicit, atomically validated
`apply_signal_influence` request. Drives contribute to attention scoring;
attention candidates never directly create or authorize Actions.

## ADR-012: Phase 4 relationships are Entity-owned inspection state

Status: accepted

Each Entity composes a per-subject `RelationshipStore`. Construction copies
the supplied relationship values and every public service/HTTP view is
detached, preventing Console inspection from mutating character. Phase 4 adds
social-state inspection but no inference-owned updates, automatic learning, or
durable storage.

## ADR-013: Console Chat is a Signal source

Status: accepted

The Console sends text as a `UserMessage` through the ordinary Signal HTTP
operation. It renders responses only from normal Actions associated with that
Signal. There is no chat-specific runtime method or response shortcut.

## ADR-014: TOML is the Phase 6A application configuration boundary

Status: accepted

Echo uses immutable dataclasses as one typed application schema and Python
3.11's standard-library `tomllib` as its initial file format. An allowlist maps
deployment environment variables onto schema fields, with environment values
taking precedence and credentials excluded from representations. Parsing,
unknown-key checks, type checks, and cross-field checks report aggregated
dotted-path issues before Runtime or provider construction. Provider-local
environment constructors remain compatibility APIs; new application startup
uses `load_config()` and its factories. Live reload is a separate Phase 6
decision because it requires lifecycle and management-plane semantics.

## ADR-015: Configuration reload is explicit and transactional

Status: accepted

Phase 6B reload is invoked only through `RuntimeConfigurationManager` and the
shared Runtime service operation. A fully validated candidate is diffed against
the active immutable schema and every changed dotted path is classified as
live-safe or restart-required. Logging, routing policy, and bounded retention
may change in place. Construction, startup, credentials, API binding, and
Console connectivity require restart. If one restart-required field changes,
the entire candidate is rejected without applying its live-safe fields. Every
outcome emits a secret-safe structured audit event.

## ADR-016: Configuration inspection is field-oriented and secret-safe

Status: accepted

Phase 6C exposes effective settings as dotted field descriptors rather than
serializing `EchoConfig` directly. Every field is labeled live-editable or
restart-required. Credential values are always null and disclose only whether
they are configured. Control requests may name only live-editable fields and
must rebuild and validate a complete typed candidate before Phase 6B applies
it. Browser and terminal clients share this service contract.

## ADR-017: Echo decides memory retention and consolidation

Status: accepted

Importance is represented as normalized evidence dimensions with a deterministic
score and explicit threshold. Consolidation input is only a proposal. Echo
validates repeated retained episodic evidence, confidence, target scope, and
contradictions before creating semantic, preference, or relationship memory.
Accepted and rejected decisions are auditable; providers never receive a direct
character mutation path.
