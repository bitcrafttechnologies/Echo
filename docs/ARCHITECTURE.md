# Architecture

`Echo_Plan.md` is the architectural source of truth. The persistent character
amendment is detailed in `CHARACTER_ARCHITECTURE.md`. This document records the
implemented boundary so that code and roadmap can be compared quickly.
The stable Phase 3 control contract is specified in `RUNTIME_API.md`.

## Kernel model

Echo is one asynchronous, event-driven runtime centered on four primitives:

1. An `Entity` is the persistent intelligent actor and owns identity, state,
   active tasks, and handlers.
2. A `Signal` says that something happened and carries source, time, payload,
   and metadata.
3. A `Task` tracks an ongoing unit of work and its lifecycle.
4. An `Action` records something an Entity intends to do.

The Phase 1 data flow is:

```text
Signal -> Scheduler -> Runtime -> Entity handler -> Task -> Action
```

The scheduler and handler registry are small kernel mechanisms, not additional
domain layers. The runtime coordinates them and keeps observable in-memory
records of processed signals, tasks, and actions.

`Runtime.emit` is the Phase 1 execution boundary. It places a Signal in the
Scheduler and processes priority-ordered work through that Signal before
returning. Each matching handler receives its own Task. A handler may update its
Entity state, return Actions, call `Entity.action`, or emit another Signal.

The Runtime retains completed Task and Action records for inspection. An
Entity's `active_tasks` contains only currently executing handler Tasks.

Phase 2A adds direct structured instrumentation to this path. A Runtime writes
typed records through a small `LogSink` interface and uses an
`InMemoryLogSink` by default. Records contain a timestamp, event type,
applicable Entity/Signal/Task/Action IDs, and metadata. Sink failures are
isolated from runtime control flow. This is instrumentation, not a second event
system.

Phase 2B adds a bounded `SignalHistory` owned by each Runtime. It snapshots
Signal transport fields before handler execution and records the routing result
afterward, including matched Entities and resulting Tasks. Queries operate on
the in-memory snapshots by recency, ID, type, and source. Capacity is explicit,
and insertion order determines oldest-first eviction. There is no persistence
or replay behavior in this component.

Phase 2C adds independently bounded Task and Action histories. `TaskHistory`
keeps references to the Task objects already owned by the Runtime, so status
transitions stay current without a duplicate mutable lifecycle model; reads
produce isolated snapshots. `ActionHistory` keeps the Action object plus only
the lifecycle data absent from that primitive: execution time, status,
Signal association, result, and error. The Runtime's compatibility lists use
the same bounds as their histories.

Phase 2D exposes this observability through `Runtime.inspect()` and its
`snapshot()` alias. The result is a detached JSON-safe dictionary covering
runtime status and uptime, Entity state, active and queued work, recent
activity and errors, scheduler state, and handler registrations. Scheduler and
registry components provide read-only summaries; inspection never consumes a
Signal or exposes their implementation objects. The API has no HTTP, database,
or UI dependency.

Phase 2E verifies the complete observability path as one causal chain:

```text
Signal ID
  -> routing record
  -> Task ID and lifecycle
  -> Action ID and execution record
  -> Entity state change
  -> structured logs + bounded histories
  -> Runtime inspection snapshot
```

Each record links through the applicable Signal, Task, Action, and Entity IDs.
The Runtime remains the single instrumentation point; Entity Action collection
does not emit a second copy when the same Action is returned by a handler.
Compatibility lists retain the original kernel objects within the same bounds
as their history APIs, while inspection returns detached data only.

The Phase 2 character slice is also composed into the public `Entity`:
immutable `EntityIdentity`, immutable `TraitProfile`, and mutable `SelfModel`
with enforced Entity-ID agreement. Identity and traits have no public mutation
setter. `CharacterMutationAuditRecord` supplies proposed/accepted/rejected,
target, evidence, reason, and before/after vocabulary without creating a
mutation service or persistence store. Runtime inspection includes this
character slice separately from ordinary mutable Entity state.

Phase 3A adds `RuntimeService` as the supported application boundary above the
kernel. It accepts domain objects and typed request/query records rather than
HTTP concepts. It returns detached Entity and state results plus existing safe
history snapshots, and reports invalid operations through a structured domain
error hierarchy. Per-Entity state mutations require an explicit key allowlist.
Live Task cancellation is coordinated by the Runtime and completes the Task's
lifecycle before returning. A bounded Runtime-owned log read history keeps API
log access independent of external sink capabilities.

Phase 3B adds a structured developer-command layer above `RuntimeService`:

```text
typed command object or minimal text
  -> closed command schema
  -> explicit DeveloperCommandDispatcher branch
  -> RuntimeService operation
  -> structured CommandResult or command error
```

This preserves two presentation paths without duplicating runtime behavior:
Console code can construct command objects directly, while a future CLI can
use or extend the small text parser. Text parsing is limited to `shlex`
tokenization and JSON-object decoding. Command names never resolve dynamically
to Python callables, and there is no `eval`, `exec`, import, or shell path.

Phase 3C publishes the existing structured Runtime observations to a small
in-process broker. Each observation is assigned one monotonic sequence number,
classified into a stable event family, detached, and offered with
`put_nowait()` to each matching subscriber's private bounded queue. Subscribers
choose drop-oldest or drop-newest behavior and can inspect their drop count.
The Runtime never awaits consumer work. Consumer failures therefore remain in
the consumer task, slow consumers are bounded, and a broker-side subscription
failure is removed without entering Runtime control flow.

```text
Runtime lifecycle observation
  -> internal log history and external LogSink
  -> Runtime event broker
       -> subscriber A bounded queue
       -> subscriber B bounded queue
       -> subscriber C bounded queue
```

The `logs` subscription category selects every observation; narrower consumers
can select Signal receipt/routing, Task or Action lifecycle, state changes,
Runtime changes, or errors. The broker defines no wire format or network
transport. A later WebSocket adapter may consume subscriptions through
`RuntimeService` without moving event semantics into the web server.

Phase 3D stabilizes these layers behind `RuntimeServiceProtocol`. The protocol,
documented request/query/result types, service error codes, command schemas,
and subscription objects are the public application boundary. Concrete history
storage, mutable compatibility lists, dispatch ownership, active-handler
tracking, the event broker, subscriber queues, attention retention capacity,
and private helpers remain implementation details. Adapters can implement or
consume the protocol without inheriting an HTTP response model or in-process
Runtime type.

Phase 3D also completes the Phase 3 character slice. The public Entity now owns
normalized internal control state, immutable drive baselines, bounded drive
activation, and retained attention candidates in addition to its Phase 2
identity, traits, and self-model. A retained Signal may affect this state only
through an explicit `RuntimeService.apply_signal_influence()` request. The
operation validates every requested dimension before mutation, clamps accepted
deltas, records the Signal relationship, and emits an observable state-change
event. Active drive values contribute to attention scores. An attention
candidate is not a Task, intention, goal, or Action and cannot cause external
behavior by itself.

Phase 4A adds a one-way optional HTTP adapter under `echo.adapters`. Its app
factory accepts `RuntimeServiceProtocol`; route handlers only construct the
existing request/query records, call the matching service operation, and
serialize the returned transport snapshots. Stable service failures are mapped
to HTTP status codes without introducing HTTP concepts into the service or
kernel. The package root never imports the adapter, so FastAPI is not required
to import or run Echo Core.

Phase 4B maps one WebSocket connection to one Phase 3 event subscription. A
send loop serializes the existing structured event envelope while an
independent receive loop detects disconnects. Both paths close the subscription
in guaranteed cleanup, removing it from the broker. Per-client category,
queue-size, and overflow options are passed directly into
`RuntimeSubscriptionRequest`; the Runtime still publishes only into bounded
queues and never waits on socket I/O.

Phase 4C adds a separate SvelteKit development client under `console/`. Its
default Vite development proxy targets the FastAPI adapter, while optional
public environment values support direct HTTP and WebSocket endpoints. The
shell polls Runtime status, maintains a reconnecting event socket, retains a
small presentation-only event list, and degrades to explicit offline state.
It introduces no dependency from Echo Core to Node, Svelte, or browser APIs.

Phase 4D implements Signal inspection entirely in the Console. Retained records
come from the Phase 4A Signal endpoints; live arrival IDs come from Phase 4B
structured events and are reconciled with retained snapshots. Type and source
filters are presentation-only over the fetched bounded history. Selecting a
Signal refreshes its snapshot and queries Actions by Signal ID, while related
Task IDs remain part of the existing routing result. Pausing freezes only the
visual live-arrival list and does not suspend Runtime subscriptions, history,
or processing. No replay operation is introduced.

Phase 4E completes the initial Console working surface. Task, Entity, log, and
Chat views call only the Phase 3/4 service HTTP adapter and refresh from normal
WebSocket activity. Task cancellation uses the existing service operation.
Chat injects a `UserMessage` through the Signal endpoint and renders only
Actions associated by that Signal ID, so it has no privileged cognition or
response path. Structured logs now carry a severity and can be queried by
severity and event type.

The Phase 4 character slice composes an in-memory `RelationshipStore` into the
public Entity. Per-subject social state is copied at construction and exposed
only as detached snapshots through Entity character inspection, explicit
service methods, HTTP reads, and the Entity Inspector. The inspector keeps
identity, traits, control state, drives, self-model/embodiment, attention, and
relationships visibly separate. Relationship learning and persistence remain
future bounded operations.

Phase 1F adds `echo.tui` as another outward-facing adapter. `LocalEchoClient`
accepts `RuntimeServiceProtocol` for embedded and fully offline operation;
`HttpEchoClient` maps the same reads and structured developer-command grammar
onto the existing HTTP adapter. Renderers consume detached dictionaries only.
They cannot reach Runtime or Entity internals.

`ConsoleSurfaceRegistry` publishes ordered, transport-safe metadata for the
currently implemented Overview, Chat, Signals, Tasks, Entity, and Logs
surfaces. It is the small extension seam later subsystems use to declare
operator capabilities. Runtime implementation objects and callbacks are
deliberately absent from registry entries.

tmux is an optional workspace/session owner, not an Echo architecture layer.
Each tmux view starts the same TUI against the management endpoint; ordinary
tmux controls remain available. The config window delegates editing to
nvim/vi. Phase 6A adds shared TOML parsing and startup validation. Phase 6B
keeps explicit reload behind the shared Runtime service operation, so neither
terminal nor web presentation acquires a private configuration mutation path.

Phase 6A adds `echo.config` as the application composition boundary. Its typed
schema owns Runtime startup, logging, bounded histories, provider routing and
provider-specific settings, API binding, and Console connectivity. TOML values
are merged with an explicit allowlist of environment overrides and validated
before factories construct Runtime or provider objects. Core primitives do not
read the application configuration or process environment.

Phase 6B adds `RuntimeConfigurationManager` at that composition boundary. It
loads and fully validates a candidate before comparing it with the active typed
configuration. Logging level/format, provider mode/preference, router history,
and Runtime history bounds are live-safe. Settings that construct providers,
bind the API, control startup, or select Console connectivity require restart.
A reload containing any restart-required change applies nothing; invalid input
also preserves active state. Every applied, unchanged, rejected, invalid, or
failed request produces a structured `configuration.reload` Runtime event.

Phase 6C makes this boundary inspectable and controllable without exposing the
configuration object itself. `ConfigurationInspectionResult` flattens effective
settings into typed field descriptors. Credentials carry only hidden/configured
flags and a null value. Live controls accept an allowlisted dotted-path mapping,
rebuild a complete typed candidate, and reuse Phase 6B validation and atomic
application. HTTP, web Console, developer commands, and TUI remain adapters
over that service contract.

Phase 5A adds `echo.providers` as an optional intelligence boundary. The base
`IntelligenceProvider` protocol exposes asynchronous inference and health
checks plus immutable provider metadata. Requests and results are detached,
provider-neutral dataclasses; every inference and health result identifies the
serving provider and records wall-clock timestamps plus monotonic elapsed time.
`EmbeddingProvider` and `ClassificationProvider` are separate capability
protocols, so future operations do not expand the minimum provider contract.
`MockProvider` supplies deterministic tests without a model dependency. No
provider is constructed by Core, and Runtime behavior is unchanged when none
is configured.

Phase 5B adds the only provider-selection policy in `ProviderRouter`. It owns
three explicit slots and makes one bounded pass per inference: `auto` tries
the remote OpenRouter slot, then LAN, then offline, while `remote`, `lan`, and
`offline` modes allow only their named slot. Provider failures are retained as
structured attempts with provider identity, slot, elapsed time, and error
reason. Router status
also reports the latest selected provider and total route latency. Exhausting
the allowed slots raises `InferenceUnavailableError` with the complete attempt
record; no retry loop or transport-specific selection logic exists elsewhere.
Async router health checks inspect the providers allowed by the current mode
without issuing inference requests.

Phase 5C implements the remote slot with `OpenRouterProvider`. Configuration is
an explicit immutable object or is read from `OPENROUTER_*` environment
variables; its API-key field is excluded from representations and provider
metadata. The provider sends one non-streaming, OpenAI-compatible chat
completion request and normalizes text, the actually served model, finish
reason, token usage, provider metadata, and timing into `InferenceResult`.
`GET /key` supplies an authenticated health check. HTTP, network,
configuration, request-shape, and response-shape failures use structured
provider errors that are visible to `ProviderRouter`; the provider contains no
fallback or retry policy. Standard-library logging records provider/model,
request ID, outcome, and duration without prompts, authorization headers, or
API keys. The HTTP transport is replaceable for deterministic tests and the
default implementation uses `urllib` in `asyncio.to_thread`, keeping the core
dependency-free.

Phase 5D implements the LAN slot with `LanInferenceProvider`. It accepts only
an explicit HTTP(S) base URL and normalizes a host root, `/v1` root, or full
chat-completions URL to the OpenAI-compatible `/v1` API root. There is no
broadcast, mDNS, subnet scan, or other discovery path. Configuration comes
from `LanInferenceConfig` or `LAN_INFERENCE_*`/`LLAMA_CPP_*` environment
values, including model, timeout, and an optional API key. Inference uses one
non-streaming `/v1/chat/completions` request and normalizes response text,
served model, usage, finish reason, llama.cpp timing details, provider metadata,
and client timing. Health uses llama.cpp's `/v1/health` contract: `status: ok`
is healthy, model-loading `503` is degraded, and connection or other HTTP
failures are unavailable. Errors are structured and visible to
`ProviderRouter`; neither retry nor fallback exists inside the provider.

Phase 5E implements the final slot with `OfflineInferenceProvider`. The provider
requires an explicit model path and starts in `unloaded`; construction, status,
and health inspection never initialize a model. Only `infer()` calls the lazy
backend load operation, which means the router's remote and LAN successes do
not spend local RAM, CPU/GPU time, or battery on the offline model. A lifecycle
lock coalesces concurrent first loads, and an inference lock prevents explicit
unload from racing active generation. State is inspectable as unconfigured,
unloaded, loading, loaded, unloading, or error. `unload()` is idempotent and
releases backend resources where supported.

`OfflineInferenceBackend` is the clean local-engine boundary. The default
`LlamaCppServerBackend` lazily starts an owned `llama-server` child on loopback,
waits for its `/v1/health` readiness, and delegates normalized inference through
the existing OpenAI-compatible LAN client. It uses argument-vector subprocess
launching without a shell, supports an explicit executable and extra arguments,
and terminates then kills only its owned child if graceful shutdown times out.
Initialization, execution, and unload failures remain structured provider
errors so `ProviderRouter` can make the sole fallback decision.

Phase 5F makes routing observable without moving policy. `RuntimeService`
optionally owns one router and exposes inference, all-provider health/status,
and mode switching. The router retains a bounded record for every inference,
including the actual serving provider, attempts, mode, latency, and terminal
failure. HTTP, web, and terminal adapters render this shared snapshot; none
selects a provider. With no router configured, Core remains usable and the
service returns an empty provider view plus structured unavailable inference.

The Phase 5 character slice composes `CharacterMemory` into Entity. Working,
episodic, semantic, preference, and relationship records are distinct immutable
types. Providers receive no ownership reference and route changes cannot erase
or replace them. Selection, scoring, consolidation, persistence, and retrieval
are deliberately outside this Phase 5 boundary.

The Phase 6 character slice adds deterministic `MemoryImportance` scoring and
explicit retention decisions. Consolidation proposals cannot mutate memory
directly: Echo requires multiple retained episodic evidence IDs, sufficient
confidence, valid subject scope, and no contradiction with an existing key
before creating semantic, preference, or relationship memory. All accepted and
rejected decisions remain in bounded audit histories owned with the Entity
memory store.

The Phase 9 character slice makes slow trait evolution an explicit Echo-owned
service. Providers may propose typed evidence grouped into a reflection, but
the proposal contains no assignable trait value or requested delta. Core
requires repeated, recent, confident evidence, accounts for contradiction,
rejects unknown and protected traits, and clamps one accepted adjustment to a
small policy maximum. Both accepted and rejected outcomes retain evidence IDs,
reason, source, and before/after values in bounded immutable audit records.
The service is copied with Entity character during graceful reconstruction;
Medulla does not import or invoke it.

## Boundaries

The implemented kernel, service, command, and subscription layers contain no
LLM, persistence, ROS, or protocol transport. Provider contracts and routing,
FastAPI, the TUI, and the Phase 9A Medulla transport contract exist only as
optional outward-facing layers. Actions remain structured intent records;
Phase 9A defines their transport-facing representation and Phase 9B supplies a
local reference adapter, but neither connects an executor to the Runtime.
The Phase 2A `action.executed` observation identifies execution at the Runtime
intent boundary, not an external side effect.

The implementation favors dataclasses, `asyncio`, explicit method calls, and
composition. Decorators are only registration helpers.

The amendment adds provider-independent character value types under
`echo.entity`. Phases 2–5 attach identity, traits, self-model, internal state,
drives, attention candidates, relationships, and typed memory to
`echo.core.Entity`; they do not persist them or route ownership through
inference. Bit's reference configuration remains outside the generic package
under `entities/bit/`. This preserves one public Entity actor while reserving
later character slices for their roadmap phases.

Handler matching accepts either a Signal type string or a Signal subclass.
Class matching uses normal Python `isinstance` behavior, so a base Signal
handler can observe every Signal while a typed handler remains specific.

## Dependency direction

Core primitives do not depend on optional infrastructure. `Entity` owns a
handler registry and can be attached to a `Runtime`; `Runtime` owns a
`Scheduler` and registered entities. Optional systems added later must depend
on this kernel rather than redefine it.

Medulla follows the same dependency rule. `echo.medulla` depends inward on
`Signal` and `Action`; `echo.core` does not import Medulla. Concrete protocol
adapters implement the structural transport contract outside Core. Incoming
data must be safely decoded and validated into a detached Signal before Core
can see it, and outbound Actions are detached and validated before an adapter
can encode them. See `MEDULLA.md` for lifecycle, health, and error semantics.

`LocalQueueTransport` is a Medulla implementation, not a Core service. Its
same-process queues demonstrate both directions of the boundary.
`MedullaSupervisor` owns transport lifecycle and inbound receive pumps at the
application layer through a narrow structural `SignalTarget`; Runtime still
does not import Medulla. Outbound dispatch is an explicit application call and
does not consume Action history or make behavior, trust, or capability-routing
decisions. `EchoApplication` is an optional one-Entity composition helper over
these existing objects, not a second runtime architecture.

`WebSocketTransport` adds network I/O without changing that dependency
direction. Its client connection manager owns reconnect backoff and bounded
queues. The versioned `echo.medulla` JSON codec rejects executable or unknown
wire data before canonical Signal validation. Connection state is adapter
health, not Core lifecycle. Authentication is an injected header hook; pairing,
trust, manifests, discovery, and capability routing remain separate later work.

`MQTTTransport` follows the same application-layer rule. Broker configuration,
topic mapping, QoS, reconnect state, and the optional MQTT dependency stay in
`echo.medulla.mqtt`; Entity and Core see only validated Signals and explicit
Action dispatch. MQTT topics are configured endpoints, not discovered
capabilities, and MQTT availability has no authority or cognition semantics.

`SerialTransport` likewise terminates physical link semantics inside Medulla.
Its dependency-free delimiter/escape/length/CRC codec rejects noise before the
shared JSON wire validator constructs a Signal. Blocking `pyserial` operations
run outside the event loop, while device reconnect state remains transport
health. Entity and Core contain no serial ports, baud rates, framing, or device
library types.

Presentation and transport adapters depend on `RuntimeServiceProtocol` and the
public command/subscription schemas. They do not depend on Runtime internals.

Character state follows the same direction: identity, traits, self-model,
internal state, drives, relationships, and memory belong to Echo. Providers
receive compact context and return untrusted proposals. Behavior policy, not a
model, authorizes Actions. Identity remains independent of provider and body.
