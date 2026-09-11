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

## ADR-018: Durable memory uses an Entity-scoped repository and Core policy

Status: accepted

Phase 7E stores semantic knowledge and conservative character development in
dedicated tables in Echo's configured local SQLite database. `MemoryService`
owns candidate validation, provenance enforcement, duplicate merging,
supersession, archival, deletion, and bounded retrieval for exactly one Entity.
Inference providers can propose information but cannot write the repository or
assert direct experience. Authored Entity seed files remain unchanged.

The initial retrieval implementation is deterministic lexical ranking with
importance and recency tie-breakers. This keeps Core local-first and
inspectable; embedding retrieval may be added behind the repository/service
boundary later without becoming the persistence authority.

## ADR-019: Medulla owns protocol I/O behind a transport-neutral contract

Status: accepted

Phase 9A introduces `echo.medulla.Transport` as an asynchronous structural
contract over Echo-native Signals and Actions. Echo Core does not import or
invoke transports. A reusable `BaseTransport` owns the common stopped,
starting, running, stopping, and failed lifecycle; validates and detaches
boundary primitives; translates adapter exceptions into structured errors;
and represents health-probe failures as unavailable health data.

Local status is non-probing while health may perform I/O. Action dispatch
distinguishes accepted, completed, rejected, and failed outcomes without
making behavior decisions. Protocol framing, concrete transports, Runtime
wiring, discovery, capability routing, reconnection, permissions, and node
trust remain deferred. External discovery will register descriptions only and
will never import executable code from a remote system.

## ADR-020: Local queues are bounded, non-blocking reference transport paths

Status: accepted

Phase 9B implements independent inbound Signal and outbound Action queues with
explicit positive capacities. Producers never wait for capacity: each queue
either rejects the newest value with a retryable structured error or drops
exactly one oldest value and reports its ID. Queue depths and overflow counters
are inspectable, and saturation degrades health.

Stopping wakes every pending reader, drains queued work, and a later start
creates a fresh queue generation. This prevents stale Signals or Actions from
crossing restart boundaries. The implementation is a same-event-loop adapter;
it does not add a Runtime pump, Action routing or authorization, discovery,
networking, or cross-thread synchronization.

## ADR-021: Medulla supervision is application composition, not Core execution

Status: accepted

The package-preparation branch adds `MedullaSupervisor` around a structural
inward `SignalTarget`. Runtime satisfies that port with `emit()` but never
imports Medulla. The supervisor owns transport startup, shutdown, receive
pumps, bounded failure observations, aggregate health, and explicit outbound
dispatch. One failed transport or handler delivery does not terminate Core or
another transport.

Action history remains an audit record and is never polled as an execution
queue. A caller must explicitly dispatch an already-authorized Action to a
named or configured-default transport. This preserves the difference between
Echo intent and an external side effect while leaving capability selection,
trust, permissions, retries, and fallback for later Phase 9 work.

## ADR-022: WebSocket availability is transport health, not Echo lifecycle

Status: accepted

Phase 9C uses a client-side connection manager inside `WebSocketTransport`.
Starting the transport starts bounded queues and the reconnect loop even when
the remote endpoint is unavailable. Disconnects therefore degrade transport
health and retain a structured retryable error; they do not fail Echo Runtime.
Queued outbound Actions survive reconnects within the active transport
generation, while stop cancels the manager and discards that generation.

The version-1 `echo.medulla` wire envelope is strict, duplicate-free JSON with
closed envelope fields, known type names, a size limit, and ordinary finite
JSON values only. Canonical Signal validation remains a second boundary before
Core. Authentication is a header-provider injection hook; pairing, trust,
authorization, manifest discovery, routing, and result correlation are not
implied by a successful socket connection.

## ADR-023: MQTT topics are configured transport paths, not discovery

Status: accepted

Phase 9D makes the MQTT broker, client identity, TLS, QoS, and provisional
Entity-scoped topic mapping explicit application configuration. Signal,
Action, and status payloads use the existing safe Medulla wire envelope rather
than introducing MQTT-native domain objects. MQTT remains an optional,
lazy-loaded adapter and does not enter Entity or Core.

Broker unavailability is degraded transport health handled by the adapter's
reconnect loop. It cannot terminate Runtime or another transport. Connected
and graceful-stop status observations are non-retained until a later phase
explicitly defines Last Will and durable presence semantics; this avoids a
stale retained online assertion after an abrupt device or network failure.
Topic availability does not discover, pair, authorize, or trust a capability.

## ADR-024: Serial frames resynchronize before wire-message validation

Status: accepted

Phase 9E places a small dependency-free framing layer below the versioned
Medulla JSON envelope. `0x7E` delimiters and `0x7D` escaping surround a frame
version, unsigned 16-bit payload length, UTF-8 JSON payload, and IEEE CRC-32.
This is implementable on a microcontroller without Echo or Python and lets a
bounded incremental parser recover from boot logs, line noise, truncation, and
corruption before any payload reaches Signal validation.

Serial device availability is adapter health. Port-open, read, and write
failures trigger bounded reconnect behavior and cannot stop Runtime or another
transport. `pyserial` is imported lazily; Entity and Core never depend on the
serial library, frame representation, port configuration, or baud rate.

## ADR-025: Trait evolution consumes evidence, never requested values

Status: accepted

Phase 9 completes its character slice with an Entity-owned trait evolution
service. Providers and other observers may submit immutable, timestamped
evidence in a typed reflection, but the contract has no desired trait value or
requested adjustment. Echo policy alone evaluates repetition, confidence,
recency, contradiction, duplicate use, configured trait membership, and an
optional mutable-trait allowlist. One accepted decision can change a trait by
no more than the configured small bound.

Accepted and rejected outcomes both retain immutable audit records with
evidence IDs, source, reason, and before/after values. Evidence and audit
windows are bounded and copied during graceful Entity reconstruction. This
decision adds neither automatic model reflection nor authority for Medulla to
change character; cold-process durable trait storage remains a later concern.

## ADR-026: Capabilities are inert, provider-qualified descriptions

Status: accepted

Phase 9F represents a capability as versioned ordinary data: stable ID, name,
description, provider identity and placement, JSON input/output schemas,
availability, permissions and risk, effect classification, and timeout
expectations. Provider kind is descriptive and transport-neutral. A capability
contains no callback or implementation, and registration grants no permission,
trust, or execution authority.

Stable IDs are the primary identity. Provider-qualified names are unique in a
registry, while equal unqualified names may coexist across providers. An
unqualified lookup with more than one match fails explicitly instead of using
registration order or provider kind as an implicit priority. Invocation,
Action routing, discovery, pairing, authorization, and provider implementations
remain later decisions.

## ADR-027: Capability routing is deterministic and does not retry side effects

Status: accepted

Phase 9G keeps provider ownership and health in the capability registry and
maps providers to transport IDs through explicit route bindings. An Action
names the capability Echo already chose to attempt. The router filters on
declared capability availability and provider health, delegates authorization
to an injected permission hook, and orders eligible routes by availability,
health, configured priority, provider ID, and stable capability ID.

Registration order is never a routing input. A provider exception is converted
to a structured dispatch failure, and the declared maximum capability timeout
is enforced. After one route begins execution, Medulla returns its result or
failure without trying another provider, because an uncertain failure may have
already changed external state. Discovery, trust policy, Action planning, and
automatic execution from Runtime history remain outside this decision.

## ADR-028: Remote nodes announce inert manifests over existing transports

Status: accepted

Phase 9H defines `medulla/1` as a versioned manifest carried by the existing
safe `echo.medulla` wire envelope. A node announces identity, type, endpoints,
capabilities, produced Signal types, resources, health, and authentication or
pairing metadata. Capabilities use the Phase 9F contract directly and must be
owned by the manifest's remote provider. Signal names are canonical Echo type
names; adapter names refer only to code already installed on the Echo side.

Manifests contain closed finite JSON data and never identify executable types
for dynamic import. Authentication and pairing fields are untrusted claims,
not authorization or trust state. Unsupported protocol versions fail before
registry mutation.

The node directory disables provider health and all node-owned capabilities on
disconnect, preserving enough description for inspection. A new valid
manifest replaces and restores them on reconnect. Registration is preflighted
against a detached registry snapshot, and the WebSocket adapter exposes only
injected manifest/presence hooks. This phase adds no active discovery, remote
code loading, node daemon, pairing workflow, or trust policy.
