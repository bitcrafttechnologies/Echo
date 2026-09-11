# Current State

## Current implementation

Echo through Phase 9H plus `0.9.2-package_prep` and the `0.4-tui_core`
interface track consists of a
minimal, standard-library Python kernel, an optional FastAPI HTTP and WebSocket
adapter, and a separate SvelteKit development console shell. The documented,
transport-agnostic runtime
contract, structured developer commands, and live event subscriptions remain
the only control boundary used by the transport and presentation layers. Phase
3 character state, drives, Signal influence, and attention candidates, plus
Phase 4 per-person relationship state, are implemented in memory.

Phase 8A defines recording format version 1 as UTF-8 JSON Lines. A recording
has a self-describing `session.started` line, zero or more Signal lines, and an
optional `session.ended` line so an active or interrupted session remains
readable through its last complete record. Signal records preserve ID, type,
source, original timezone-aware timestamp, recording timestamp, payload, and
metadata. Optional linkage retains Runtime, Entity, Task, and Action IDs
without putting routing results into the Signal itself. The writer uses
exclusive file creation, flushes every line, and synchronizes writes by
default. The validator accepts only ordinary JSON values with finite numbers
and string object keys; it rejects arbitrary Python objects rather than using
pickle, type imports, hooks, or `repr` fallbacks. The reader validates session
ordering, version, record types, IDs, timestamps, and final counts but never
injects a Signal. Phase 8A itself does not attach to a Runtime or implement
recording controls, replay, or playback timing; Phase 8B supplies only the live
recording and control layer described below.

Phase 8B connects that format to live Runtime activity. One explicitly started
session attaches a post-dispatch capture sink, including Signals emitted from
Entity handlers as well as service and transport callers. Dispatch only makes
a non-waiting handoff to a bounded queue; safe serialization, JSON encoding,
flush, and storage synchronization run through a worker thread. Stop detaches
capture before draining queued records and writing the session footer. Status
exposes lifecycle state, format/version, Runtime/session IDs, output path,
timestamps, developer metadata, enqueued/written/dropped counts, and the latest
error. Queue overflow and background serialization/write failures emit
recoverable Runtime errors and never change Signal routing or stop Echo.
Runtime service, HTTP, structured developer commands, and one-shot `echoc`
commands expose start, stop, and status.

Phase 8C injects validated Phase 8 recordings through the same
`Runtime.emit()` entry path as live Signals. It supports one selected Signal,
complete file-order sequential replay, and step-by-step sessions that emit one
Signal per advance. Replayed Signals receive fresh IDs and current UTC receipt
timestamps while a reserved `metadata.echo_replay` marker preserves original
Signal ID, original timestamp, recording timestamp, session/replay IDs, and
the active safety policy. Status exposes mode, progress, lifecycle, the
original-to-new ID mapping, timestamps, and failures through Runtime service,
HTTP, structured commands, and one-shot `echoc`. Only the explicit
`record_only` safety policy exists: normal handlers and Action intent history
still run, but replay never calls an external Action executor.

Phase 8D adds explicit replay timing independent of Signal selection.
`immediate` dispatches without added delay; `realtime` preserves recorded
timestamp offsets at 1×; `accelerated` divides those offsets by a finite,
positive multiplier; and `manual_step` waits for each developer advance.
Realtime and accelerated sessions run in a background Task so progress and
cancellation remain accessible during waits. Deadlines use the event loop's
monotonic clock and are anchored to the first Signal, avoiding wall-clock
changes and cumulative drift. Cancellation wakes pending waits and settles at
a safe Signal boundary without cancelling an active handler. Replay status now
includes timing, multiplier, cancellation request/terminal state, remaining
Signals, and original-to-runtime Signal mappings.

Phase 8E adds replay controls directly to Echo Console's Signal Inspector.
Developers can select a host-local JSON Lines recording path, replay the whole
session or the inspected recorded Signal, choose immediate, realtime,
accelerated, or manual-step timing, advance manual sessions, stop replay, and
inspect session identity and progress. Replayed traffic is labeled in both
live and retained lists; Signal detail shows its original recorded ID and
timestamp. The Console continues to use the Phase 8C/8D HTTP boundary and the
only available `record_only` safety policy.

The Phase 8 character architecture slice is also implemented. Entity owns
JSON-safe typed intentions, deterministic behavior arbitration, retained
decisions, and bounded low-priority curiosity goals. Policy checks confidence,
capability, explicit external-Action authorization, resources,
interruptibility, user attention, and relationship trust. An approved policy
decision contains an Action intent but neither records nor executes it
implicitly. High-salience attention can create background curiosity work;
active Entity work defers it rather than interrupting or causing speech,
movement, or tool use.

The Phase 9 character architecture slice is implemented alongside Medulla.
Providers and runtime observations may submit typed `TraitEvidence` only
through a `TraitReflection`; they cannot assign trait values or request an
arbitrary delta. Echo-owned `TraitEvolutionPolicy` requires repeated, recent,
sufficiently confident evidence with a dominant direction, rejects unknown or
policy-protected traits and reused evidence, and bounds any accepted adjustment
to 0.05 by default. Accepted and rejected decisions retain their evidence IDs,
before/after values, source, and reason in bounded immutable audit records.
The current profile, evidence window, duplicate protection, and audit window
survive graceful Entity reconstruction and appear in detached character
inspection. This slice does not let Medulla mutate character and does not add
automatic model reflection or a durable trait database.

Phase 9A establishes `echo.medulla` as a separate, protocol-neutral boundary
that depends inward on Echo's `Signal` and `Action` primitives. Its structural
`Transport` contract covers asynchronous start, stop, receive, Action execute,
active health, and non-probing local status. `BaseTransport` provides the
shared stopped/starting/running/stopping/failed lifecycle, idempotent stable
transitions, boundary validation and detachment, structured error translation,
last-error inspection, and health failure containment while preserving normal
task cancellation. No concrete transport, Runtime execution wiring,
discovery, capability routing, or trust workflow is part of Phase 9A.

Phase 9B adds `LocalQueueTransport` as the same-process reference adapter.
External producers publish validated Signals into a bounded inbound queue;
Medulla receives them through the Phase 9A contract. Outbound Actions are
validated, accepted into a separate bounded queue, and retrieved by an
external consumer. Each direction independently rejects the newest item with
a retryable structured error or drops exactly one oldest item. Queue-aware
status exposes depth, capacity, overflow policy, rejection, drop, and shutdown
discard counters, while saturation degrades health. Stop wakes all pending
readers, drains both queues, and a later start uses a fresh generation. Runtime
wiring is supplied by the package-preparation composition described below.

Three optional one-shot source adapters demonstrate the local inbound path.
`ClockSignalSource` publishes `time.observed`; `OpenMeteoWeatherSource`
normalizes bounded current-condition JSON into `weather.observed`; and
`HackerNewsSignalSource` normalizes at most ten stories into one bounded
`news.headlines_observed` Signal. HTTP access uses a standard-library client
with explicit timeout and response-size limits, and tests replace it with
deterministic fetchers. These sources are examples, not formal registered or
trusted capabilities, recurring schedulers, or Runtime wiring.

The `0.9.2-package_prep` branch supplies the first reusable Runtime wiring at
the application layer. `MedullaSupervisor` starts and stops transports,
maintains one inbound Signal pump per running transport, contains failures in
bounded inspection records, provides aggregate health, and explicitly
dispatches an already-authorized Action to a named or sole default transport.
It never polls Runtime Action history and adds no cognition, discovery, trust,
retry, or capability-routing policy. `EchoApplication` composes one
programmatic or project-seeded Entity with a Runtime and supervisor. Project
character files, application-specific Signal/Action schemas, and any UI remain
outside the wheel. Distribution metadata declares the MIT license and
typed-package marker; a clean-wheel integration test installs and runs Echo
from outside the repository. `docs/DEVELOPER_QUICKSTART.md` documents the new
developer path.

Phase 9C adds a reconnectable client-side `WebSocketTransport`. A strict
version-1 JSON envelope identifies the `echo.medulla` protocol, message type,
message ID, and ordinary JSON payload. The schema reserves `signal`, `action`,
`result`, `status`, `hello`, `manifest`, `heartbeat`, and `error`; only the
Phase 9C transport behavior is active. Incoming Signals pass through the
closed canonical Signal validator before entering the bounded receive queue,
and outbound Actions are detached and validated before entering a bounded
send queue. JSON decoding rejects duplicate keys, non-finite numbers,
oversized messages, unknown envelope fields, unknown types, and malformed
payloads without importing types or constructing arbitrary Python objects.

The connection manager starts independently of remote availability, retries
with bounded exponential backoff, retains queued Actions across reconnects,
and reports malformed messages back as safe protocol errors without dropping
an otherwise usable connection. Initial connection failure and later
disconnects remain contained inside the adapter, so neither Echo Runtime nor
another Medulla transport is stopped. Status and health expose sanitized
endpoint identity, connection/reconnection state, timestamps, counters, queue
depths, peer hello identity, and wire version. An injected header-provider
hook is the only authentication surface; Phase 9C does not implement pairing,
authorization, stored credentials, manifest discovery, capability routing, or
result semantics.

Phase 9D adds an optional reconnectable `MQTTTransport` for lightweight
distributed sensors and devices. Broker hostname, port, client ID, TLS,
credentials, keepalive, timeout, QoS, capacities, and reconnect bounds are
explicit configuration rather than discovery. `MQTTTopicMap` supplies the
provisional `echo/{entity}/signals/#`, `echo/{entity}/actions/{type}`, and
`echo/{entity}/status/transport` mapping; prefixes are configurable and Action
types are percent-encoded into one topic level. Signal and Action payloads use
the Phase 9C versioned JSON envelope, so MQTT ingress reaches the same closed
Signal validation path and never constructs arbitrary Python objects.

The optional `aiomqtt` dependency is imported only when the default client is
constructed. Startup launches a contained broker connection manager even when
the broker is offline, and capped reconnect backoff retains bounded outbound
Actions within the active transport generation. Invalid broker messages are
counted and rejected without ending a healthy client session. Connected and
graceful-stop status observations use the mapped status topic without retained
presence claims. MQTT status/health exposes broker address, TLS, client/topic
mapping, QoS, connection timestamps and counters, and queue depth while
excluding credentials. No Entity imports MQTT, and MQTT is neither required
nor consulted for Medulla discovery.

Phase 9E adds an optional reconnectable `SerialTransport` for microcontrollers
and directly attached embedded devices. `SerialPortConfig` makes the device
port, baud rate, read timeout, and write timeout explicit. The dependency-free
frame codec uses `0x7E` delimiters and `0x7D` escaping around a version byte,
big-endian 16-bit payload length, UTF-8 Medulla JSON, and big-endian CRC-32.
The incremental parser bounds encoded data, discards out-of-frame noise,
rejects invalid escapes, versions, lengths, and CRCs, and resynchronizes at the
next delimiter. This small framing contract can be implemented directly in
microcontroller firmware without importing Echo or Python.

Only complete frame-version-1 payloads containing wire-version-1 `signal`
messages reach canonical Signal validation. Outbound supported Actions use the
same JSON Action message inside a serial frame. Blocking device reads and
writes run outside the event loop through the optional, lazily imported
`pyserial` adapter. Port-open, read, and write failures degrade serial health
and trigger capped reconnect backoff without stopping Echo or another
transport; bounded queued and in-flight Actions survive reconnect within the
active generation. Status and health expose port/baud, connection state,
timestamps, reconnects, frame counts, rejected/noise counts, queue depths, and
frame/wire versions.

Phase 9F defines the version-1 Medulla capability contract independently of
every transport. A `Capability` has an explicit stable ID, provider-qualified
name, human description, detached JSON input and output schemas, current
availability, declarative permission requirements and risk class, read-only or
state-changing effect, and normal/hard timeout expectations. A
`CapabilityProvider` identifies the provider, its local or remote placement,
and its implementation family without exposing a transport object. The family
vocabulary anticipates native, MCP, WebSocket, MQTT, Serial, HTTP, GPIO, CAN,
and ROS providers; Phase 9F implements none of those providers.

`CapabilityRegistry` provides deterministic inspection by stable ID and by
provider-qualified name. Equal unqualified names from different providers are
retained and produce an explicit ambiguity error rather than silently choosing
one provider. Availability can be updated as a new immutable capability
snapshot and inspected for every registered ID. Capability and registry
serialization contain ordinary finite JSON values only, and capability
decoding uses closed version-1 objects. The contract contains no callback,
downloaded implementation, cognition, discovery, invocation, routing,
permission grant, trust decision, or transport selection.

Phase 9G turns the descriptive catalog into Medulla's central capability and
provider inventory. The registry now supports explicit provider registration,
provider-owned capability lookup and removal, provider removal, current
capability availability, transport-neutral provider health, and complete JSON
introspection. A provider ID cannot silently acquire conflicting metadata, and
removal updates every capability-name index predictably.

`CapabilityRouter` binds each registered provider to an explicitly configured
transport ID. For an already-created Action, it finds capabilities with the
matching name, excludes unavailable capabilities and providers, evaluates an
application-owned asynchronous permission hook, and chooses one route by
capability availability, provider health, configured priority, provider ID,
and capability ID. This order is stable and does not depend on registration
order. An explicit provider or capability ID can narrow the lookup.

The selected route dispatches through the existing supervisor-compatible
transport port and enforces the capability's maximum timeout. Lookup,
permission, timeout, provider, and transport failures return structured
`ActionDispatchResult` failures. Provider exceptions are contained; normal
task cancellation remains cooperative. Once execution begins, the router does
not fall through to another provider, avoiding duplicate external side
effects. It does not select, create, or plan Actions and does not implement a
permission policy, discovery, pairing, or trust.

Phase 9H defines the lightweight `medulla/1` remote-node manifest. A closed,
ordinary-JSON manifest describes node identity and type, a remote capability
provider, transport endpoints, Phase 9F capabilities, produced Echo Signal
types and optional local adapter identifiers, resources, provider health, and
authentication/pairing metadata. The inner node protocol is versioned
independently of the existing version-1 `echo.medulla` wire envelope. Unknown
protocol names and unsupported versions fail with typed `NodeProtocolError`
codes.

The manifest uses full `Capability` values and requires every capability to be
owned by the remote node provider. Endpoint/provider identity and duplicate
IDs are validated before registration. Signal names map directly to Echo
`Signal.type`; an optional adapter string is only a local adapter identifier.
Schemas, endpoint metadata, health details, resource metadata, and pairing
claims are inert JSON. No module name, callback, class, bytecode, or remote
implementation is loaded or invoked.

`MedullaNodeDirectory` applies a manifest atomically to the Phase 9G registry
and can bind the provider to an already-configured router transport. It tracks
node presence and exposes deterministic node, capability, Signal, resource,
and transport inspection. Disconnect marks every node-owned capability and
the provider unavailable, immediately removing the route from eligibility;
reconnect with a valid manifest restores the descriptions and availability.
Explicit removal also removes the provider and route binding.

The existing wire `manifest` message now has typed encode/decode helpers.
`WebSocketTransport` accepts optional manifest and node-disconnection hooks,
checks manifest identity against peer `hello`, and contains hook/manifest
errors as protocol or connection failures. Serial and MQTT continue using the
same safe wire envelope; Phase 9H adds no new socket, broker, framing, pairing,
discovery, or trust implementation.

Phase 7A introduces the small, runtime-checkable `StateStore` boundary between
Entity state access and storage. Ordinary state is explicitly separated into
`ephemeral`, `session`, and `persistent` categories. The interface supports
get, set, delete, category listing, complete snapshots, and whole-Entity
load/save. `InMemoryStateStore` remains the dependency-free default, while an
injected store can back an Entity without changing handler code. The existing
`Entity.state` mutable mapping remains as the session-state compatibility view;
new category-aware Entity methods expose all three lifetimes. Handler mutations
in every category retain causal `state.changed` logging. This phase does not add
SQLite, restart persistence, or semantic-memory persistence.

Phase 7B adds `SQLiteStateStore` for Entity-scoped persistent state. Persistent
get/set/delete operations commit through SQLite, and whole-Entity saves replace
only that Entity's persistent rows in one transaction. Session and ephemeral
categories remain process-local and do not reappear when a new store and
Runtime are constructed. Values use a versioned, tagged JSON codec that
preserves supported primitive, bytes, list, tuple, and nested dictionary types;
unsupported, cyclic, non-finite, or corrupt values fail explicitly rather than
falling back to executable serialization. Dedicated schema metadata records the
schema and serializer versions and storage scope. The database contains no
Signal, Task, Action, log, event, character-memory, or semantic-memory tables.

Phase 7C adds the explicit `GracefulRestartCoordinator` development workflow.
The old Runtime first enters a quiescing state that rejects new Signals. Its
defined Task policy either waits for active handler Tasks for a bounded grace
period and cancels any remainder, or cancels active Tasks immediately. The
coordinator then saves each Entity's persistent category while clearing old
session and ephemeral values, closes supplied providers, transports, and state
stores through their supported async or synchronous lifecycle hooks, and stops
the old Runtime. A factory must construct a different Runtime generation. The
coordinator verifies every Entity's persistent snapshot was restored before it
reports completion. The fresh Runtime has new queues, histories, Tasks, Signals,
Actions, and other runtime-only state. Structured results, Runtime inspection,
service status, and runtime lifecycle events report the restart reason, status,
old Runtime ID, and new Runtime ID. No Python module hot replacement is
attempted.

Phase 7D exposes that workflow through the stable service and HTTP boundaries.
A restart request requires a non-empty reason, the exact deliberate
confirmation token `RESTART`, and an explicit state-preservation flag set to
true. The returned operation makes preservation, Task policy, timestamps,
current phase, failure, and old/new Runtime IDs inspectable. The long-lived API
switches to the fresh Runtime and its reconstructed provider/configuration
resources. Old event subscriptions close with WebSocket restart code `1012`;
the Console polls operation progress and reconnects its HTTP status and event
stream after completion. Its Overview control uses a prepare step plus typed
confirmation, preventing a stray click from restarting Echo.

The Phase 7 character slice is also complete. `CharacterContextBuilder`
assembles bounded, detached, provider-neutral cognition context from identity,
traits, all ordinary state lifetimes, internal state, drive baselines and
activation, relevant attention, relationships and typed memories, active goal IDs,
self-model, and supplied environment. Relevance selection is deterministic and
hard-limited rather than dumping all Entity data. The host attaches this
structured context to inference requests and reconstructs provider-independent
character when it creates a fresh Runtime generation. This does not add Phase
8 intention proposals or behavior decisions.

Phase 6A adds the single typed `EchoConfig` application schema and a TOML
loader based on Python's standard-library `tomllib`. Nested sections cover
Runtime startup, Python logging, Signal/Task/Action/log/error history limits,
provider routing, OpenRouter, LAN inference, offline inference, API binding,
and Console connectivity. `load_config()` loads an explicit path,
`ECHO_CONFIG`, or a repository-local `echo.toml`, overlays allowlisted
environment values, rejects unknown keys, and reports every discovered field
error together with its dotted path and source file. Secrets are excluded from
configuration representations.

Phase 6B adds explicit, transactional reload through
`RuntimeConfigurationManager`. A candidate file and its environment overrides
are fully validated before comparison with active state. Logging level/format,
provider mode/preference, router history, and Runtime history bounds are
live-safe. Provider construction and credentials, Runtime startup, API binding,
and Console connectivity are restart-required. If any restart-required field
changes, no part of the candidate is applied. Invalid candidates likewise
leave active state intact. Every outcome emits a structured, secret-safe
`configuration.reload` audit event.

Phase 6C exposes the active effective configuration through the shared service
boundary. Each dotted field includes its effective value and is marked
`live_editable` or `restart_required`; credential fields expose only hidden and
configured metadata with a null value. API keys never enter HTTP or browser
responses. Approved dotted-path updates rebuild and validate a complete typed
candidate before using the Phase 6B transaction. The web Console and TUI show
the same classification, provide live-safe controls, and render aggregated
validation issues by field.

The root `install.sh` now installs the complete Python/API/test environment and
locked Web Console dependencies without prompts or configuration mutation. The
root `start.sh` loads the ignored project `.env` as a non-overwriting secret
overlay and starts Core alone, Core with the Web Console, Core with the terminal
Console, or all three. Its explicit `--host` option can bind Core and the Web
Console for LAN testing while the development proxy uses loopback. Process
cleanup is bounded to the child processes started by the launcher.

The root Core host registers one built-in `UserMessage` handler for Console
chat. It invokes the active `ProviderRouter` and records the reply as an
`EchoResponse` Action associated with the originating Signal and handler Task,
so both Console implementations display responses without bypassing Runtime
routing. This bridge does not add conversation persistence. Phase 7D attaches
bounded provider-neutral character context to each inference request.

`EchoConfig.create_runtime()` and `create_provider_router()` are the explicit
construction boundary for validated settings. Disabled provider sections do
not construct providers, so Core still runs without inference. Runtime history
limits now include its management-plane log and error buffers. `echoc`
validates configuration before starting a Console client and consumes the
configured API URL, request timeout, logging level, and tmux session default;
`echoc config validate [PATH]` offers a startup-free validation check. The API
host example uses the same validated object for its bind address and port.

Phase 5A adds a dependency-free `echo.providers` boundary. Its runtime-checkable
`IntelligenceProvider` protocol supports asynchronous inference and health
checks plus immutable provider metadata. Structured inference and health
results include the serving provider and timing. Embedding and classification
are isolated behind optional capability protocols, and the deterministic
`MockProvider` supports tests without configuring a model. Echo Core does not
construct or require a provider.

Phase 5B centralizes selection in `ProviderRouter`. `auto` makes one bounded
OpenRouter/remote → LAN → offline pass, while `remote`, `lan`, and `offline`
modes attempt only the matching configured slot. The router retains the latest
selected provider, every failed attempt, per-attempt and total latency, request ID, and
error reasons. Its async health result and detached status snapshot are
inspectable without invoking a model. If every allowed provider fails—or the
selected mode has no configured provider—it raises the structured
`InferenceUnavailableError` with the complete bounded attempt record.

Phase 5C implements the router's remote slot with `OpenRouterProvider`. It uses
OpenRouter's OpenAI-compatible chat-completions endpoint and authenticated key
health endpoint through a replaceable, standard-library HTTP transport.
`OpenRouterConfig` reads `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, base URL,
timeout, and optional attribution values from the environment, or accepts
equivalent application-supplied configuration. Inference results normalize
text, actual served model, finish reason, token usage, provider metadata, and
timing. Configuration, request, network, API, and response failures are
structured. The provider makes one attempt and never falls back internally;
the router remains the only fallback owner. Logs include provider, model,
request ID, outcome, and timing, but exclude prompts, headers, and credentials.

Phase 5D implements `LanInferenceProvider` for one explicitly configured
llama.cpp or equivalent OpenAI-compatible server. It accepts a host root,
`/v1` root, or full chat-completions URL and normalizes it to the API root.
Configuration supports `LAN_INFERENCE_*` and `LLAMA_CPP_*` environment aliases
for base URL, model, timeout, and an optional API key. Inference uses one
non-streaming `/v1/chat/completions` request; health uses `/v1/health` and
distinguishes ready, loading/degraded, malformed, and unavailable states.
Results normalize text, served model, usage, finish reason, server timing,
provider metadata, and client timing. The provider performs no discovery,
retry, or fallback; `ProviderRouter` remains the sole fallback owner.

Phase 5E implements `OfflineInferenceProvider` as the final lazy fallback. Its
model path is mandatory for use and is supplied explicitly through
`OfflineInferenceConfig`, `OFFLINE_MODEL_PATH`, or `LLAMA_CPP_MODEL_PATH`;
there is no model-directory scan. Construction, status, and health checks do
not initialize the model. When—and only when—routing reaches offline inference,
the provider loads its backend once, normalizes the result, and reports loaded
state. `unload()` releases the backend and returns to unloaded state. The
inspectable lifecycle distinguishes unconfigured, unloaded, loading, loaded,
unloading, and error states.

The default `LlamaCppServerBackend` launches an owned `llama-server` subprocess
on loopback without a shell, waits for bounded health readiness, uses the LAN
OpenAI-compatible client for inference, and terminates or kills only that child
during bounded unload. `OfflineInferenceBackend` remains replaceable for other
local engines. Initialization, execution, and unload errors are structured.
Concurrent first inference calls share one load, and unload cannot race active
inference. This preserves the battery goal: normal OpenRouter success leaves
the offline model entirely unloaded.

Phase 5F integrates the router as an optional `RuntimeService` dependency and
adds bounded per-request routing history. Each record identifies the request,
mode, provider that actually served it, total latency, all attempts, and any
terminal error. Service inspection combines this history with every configured
provider's health, current active provider/model, latest latency, and recent
failures. HTTP endpoints, typed developer commands, the Svelte Console, and the
TUI expose the same management-plane view and can switch among `auto`,
`remote`, `lan`, and `offline`. A runtime without a router still runs normally
and reports an empty provider view; inference fails with a structured
`inference_unavailable` service error.

The Phase 5 character slice adds Entity-owned `CharacterMemory` with distinct
working, episodic, semantic, preference, and relationship record types. Records
are immutable and provider-independent, and Entity inspection keeps the five
categories separate. The Phase 6 character slice adds normalized importance
evidence for selective retention and Echo-owned consolidation proposals.
Repeated retained episodes can produce semantic, preference, or relationship
memory only after evidence, confidence, subject scope, and contradiction
checks. Both accepted and rejected retention/consolidation decisions are kept
in bounded in-memory audit histories. Phase 7D supplies relevant context
retrieval; durable character-memory persistence remains deferred.

The first-party terminal interface now mirrors every implemented web console
surface through detached management snapshots: Overview, Chat, Signals,
Tasks, Entity/character/relationships/memory, Providers, and Logs. It can run in-process through
`LocalEchoClient`, against the local HTTP adapter through `HttpEchoClient`, as
a single full-screen terminal, as a semantic plain-text snapshot, or in a
named tmux workspace. The TUI has no direct Runtime mutation path.

The `0.1-amendment/character_plan` branch also records the persistent character
architecture and adds its provider-independent base vocabulary. Phase 2 now
composes identity, traits, and self-model into the Entity. Phase 3 now composes
internal state, drive baselines and activation, and attention candidates.
Phase 6 now supports evidence-based relationship-memory consolidation. Broader
relationship learning remains later work. The configured host now loads Bit's
checked-in identity, traits, drives, and character guidance as versioned startup
seed configuration.

The public package exports:

- `EchoConfig`, its typed section dataclasses, `ConfigurationIssue`,
  `ConfigurationError`, and `load_config`
- `RuntimeConfigurationManager`, reload change/result schemas and enums, and
  `ConfigurationReloadApplyError`
- secret-safe `ConfigurationInspectionResult`/`ConfigurationField` schemas and
  the validated `parse_config()` mapping entry point
- `Signal`
- recording format/version constants, `JsonLinesSignalRecorder`, typed session
  and Signal records, `RuntimeLinkage`, `RecordedSession`, validation errors,
  and the non-replaying record decoder/reader
- `RuntimeSessionRecorder`, `RecordingState`, `RecordingStatus`,
  `StartRecordingRequest`, recording service errors, and recording developer
  command schemas
- `RuntimeSignalReplay`, replay selection/timing/state/status and safety policy
  schemas, `StartReplayRequest`, replay service errors, cancellation, and
  replay command schemas
- `Entity`
- `HandlerRegistry`
- `Task` and `TaskStatus`
- `Action`
- `Scheduler` and `SignalPriority`
- `Runtime`
- `RuntimeLogEvent`, `RuntimeEventType`, `RuntimeLogSeverity`, `LogSink`, and
  `InMemoryLogSink`
- `SignalHistory`, `SignalHistoryEntry`, and `SignalRoutingResult`
- `TaskHistory` and `TaskHistoryEntry`
- `ActionHistory`, `ActionHistoryEntry`, and `ActionStatus`
- `EntityIdentity`, `TraitProfile`, `TraitEvidence`, and `SelfModel`
- `CharacterMutationAuditRecord`, `CharacterMutationTarget`, and
  `CharacterMutationDecision`
- `RuntimeService`, its typed request/query/result objects, and its
  `RuntimeServiceError` domain-error hierarchy
- `RuntimeServiceProtocol`, the stable structural interface for adapters
- `DeveloperCommandDispatcher`, typed command schemas, `CommandResult`, the
  `DeveloperCommandError` hierarchy, and `parse_developer_command`
- `RuntimeEventSubscription`, `RuntimeSubscriptionRequest`,
  `RuntimeSubscriptionEvent`, event categories, backpressure policies, and
  subscription errors
- `InternalState`, `DriveProfile`, `SignalInfluence`, `AttentionProposal`, and
  `AttentionCandidate`
- `RelationshipState` and `RelationshipStore`
- typed `CharacterMemory` plus working, episodic, semantic, preference, and
  relationship memory records
- `MemoryImportance`, retention decisions, and consolidation proposal/decision
  schemas
- `IntelligenceProvider`, structured provider request/result types, health and
  metadata vocabulary, optional capability protocols, and `MockProvider`
- `ProviderRouter`, routing modes/status/attempt and inference-history records, and
  `InferenceUnavailableError`
- `OpenRouterProvider`, `OpenRouterConfig`, its replaceable HTTP transport, and
  structured OpenRouter error hierarchy
- `LanInferenceProvider`, `LanInferenceConfig`, its replaceable HTTP transport,
  and structured LAN inference error hierarchy
- `OfflineInferenceProvider`, explicit configuration/status/error types, the
  local-backend protocol, and `LlamaCppServerBackend`
- `ProviderInspectionResult` and optional RuntimeService inference, inspection,
  and mode-switch operations
- explicit RuntimeService configuration reload with structured validation and
  availability errors

The optional `echo.adapters.fastapi` module exports `create_app()`, providing
HTTP inspection/control and `/events` WebSocket streaming. It is not imported
by the public package root, so Echo Core still imports and runs when FastAPI is
unavailable.

The working runtime path is:

```text
Signal
  -> priority Scheduler
  -> Runtime dispatch
  -> matching Entity handlers
  -> one Task per handler
  -> zero or more Actions
```

`Runtime.emit` is awaited and deterministic. It returns after the emitted
Signal has been processed. The Runtime keeps bounded compatibility lists of
recent Signal, Task, and Action objects alongside their read APIs.

`RuntimeService` is the supported programmatic control boundary for CLI,
tests, and network adapters. It exposes runtime status; Entity listing
and inspection; Signal emission, listing, and inspection; Task listing,
inspection, and live cancellation; Action listing and inspection; detached
Entity state reads; detached relationship inspection; explicitly allowlisted
state writes; and severity/event-type structured log queries. Inputs and
outputs are typed where that clarifies the contract.
Returned state, metadata, and history values are detached from Runtime-owned
mutable structures. Missing resources, invalid requests, forbidden state
writes, failed Signal processing, and invalid Task cancellation use structured
domain errors with stable codes.

Live Task cancellation targets the executing handler and waits for lifecycle
cleanup, so the returned Task snapshot is already `cancelled`. The Runtime also
retains a bounded internal read history of log events, allowing service log
queries to work independently of the configured external `LogSink`.

`DeveloperCommandDispatcher` maps explicit command dataclasses to
`RuntimeService` calls for runtime status, Entity inspection, Signal listing,
inspection and injection, Task listing, inspection and cancellation, Action
listing, Entity state reads and allowlisted writes, logs, provider inspection
and mode selection, and explicit configuration reload. Direct command objects
and the minimal text parser feed the Console/TUI clients through the same
schemas. Results and failures have structured `to_dict()` representations. The
parser uses `shlex` for tokenization and `json.loads` for object values. It has
no dynamic imports, `eval`, `exec`, shell invocation, or arbitrary Python
execution path.

`RuntimeService.subscribe_events()` creates an isolated subscription to future
Runtime activity. Each structured log event is classified as Signal receipt,
Signal routing, Task lifecycle, Action lifecycle, state change, Runtime change,
or error. The `logs` category is an all-event selector. Publications receive a
global sequence number and are copied into a private bounded `asyncio.Queue`
for each subscriber. Runtime publication uses only `put_nowait`: subscribers
are never awaited. Queue overflow uses an explicit drop-oldest or drop-newest
policy and exposes a dropped-event count. A closed, failed, or slow consumer
cannot stop Runtime execution or affect another subscriber. The mechanism has
no socket, WebSocket, HTTP, or framework dependency.

The public Phase 3 contract is documented in `docs/RUNTIME_API.md`. It defines
service operations, request and response conventions, stable error codes,
developer command mappings and text grammar, event subscription schemas, and
the boundary between public types and private Runtime implementation details.
The executable `examples/runtime_api_contract.py` example is run by the test
suite.

An Entity now owns detached normalized `InternalState` views, immutable
`DriveProfile` baselines, read-only drive activation views, and bounded
`AttentionCandidate` history. `RuntimeService.apply_signal_influence()` is the
explicit mutation path: it requires a retained Signal, validates all dimensions
atomically, applies bounded deltas, lets active drives contribute to attention
scoring, and publishes the change through logs and subscriptions. It never
creates a Task, goal, intention, or Action.

Each Entity also owns a copied `RelationshipStore` keyed by person/subject ID.
Social state includes familiarity, trust, interaction count, communication
preferences, interests, boundaries, important memory references, and current
context. Entity, service, and HTTP inspection always return detached snapshots;
learning, mutation policy, and persistence remain deferred.

`create_app(runtime_service)` exposes health, Runtime status, Entity list and
inspection, Signal emission/list/inspection, Task list/inspection/cancellation,
Action list/inspection, Entity state reads and allowlisted writes,
relationship reads, log queries, provider management, inference, and explicit
configuration reload. Routes translate inputs into existing service dataclasses
and serialize service results; they do not access Runtime internals. Domain
errors retain their stable codes and details in structured HTTP error responses.

Each `/events` client receives a private subscription created through
`RuntimeService.subscribe_events()`. Events use the existing structured Phase 3
envelope. Repeated `category` query parameters provide basic filtering, while
`max_queue_size` and `backpressure` configure the existing bounded queue.
Socket writes consume the queue in a separate coroutine from Runtime event
publication, so a slow client cannot block Echo. A concurrent receive loop
detects disconnects; guaranteed cleanup closes and removes the subscription.
Reconnects create fresh future-only subscriptions.

The `console/` SvelteKit application provides a responsive Runtime header,
navigation sidebar, main workspace, separate API and event-stream connection
states, and a small recent-event view. Overview, Signals, Tasks, Entity, Logs,
and Chat are implemented through Phase 4E. The browser client polls
`/runtime/status`, connects to `/events`, retries with bounded delay, and
remains navigable while Echo is offline. Local development uses a Vite proxy by
default, preserving the FastAPI adapter as the network boundary.

The Signal Inspector reconciles live `signal.received` envelopes with the
bounded `/signals` history, supports case-insensitive type and source filters,
and refreshes a selected Signal through its inspection endpoint. Selection
shows identity, timestamp, payload, metadata, complete routing result, Task
IDs, and Action IDs queried by Signal association. Pause/resume controls only
whether new arrivals enter the visual live list; Runtime processing,
subscription consumption, and retained history continue normally. Signals
arriving while paused are counted but not replayed when the view resumes.

The Task Inspector separates active and terminal history, displays lifecycle
status and parent/child hierarchy, refreshes exact Task detail, and confirms
before invoking the existing cancellation endpoint. The Entity Inspector shows
ordinary state, registered handlers, active Tasks, and distinct identity,
traits, control state, drives, self-model/embodiment, attention, and per-person
relationship sections. Logs load the recent structured Runtime record and
filter through the service by severity and event type.

Chat posts text as a `UserMessage` Signal from source `console`. Its timeline
shows the retained UserMessage and only Actions associated with that Signal as
Echo responses. Unhandled messages remain visibly unhandled; there is no
chat-specific backend, direct handler call, or synthetic response path.

`SignalHistory` defaults to 1,000 entries and can be configured through
`Runtime(signal_history_size=...)`. It stores safe snapshots of each Signal's
ID, type, source, timestamp, payload, and metadata before routing, then attaches
the routing outcome, matched Entity IDs, attempted handler count, Task IDs and
statuses, and any processing error. Queries return newest entries first and
support limits, ID lookup, and filtering by type and source. Unknown or evicted
IDs return `None`; the oldest entry is evicted first when capacity is exceeded.

`TaskHistory` retains bounded references to the live Task objects rather than
duplicating their mutable state. Queries materialize safe read snapshots with
identity, name, owner, status, priority, lifecycle timestamps, parent/children,
associated Signal, result, and error. Completed and failed Tasks remain
inspectable until predictable oldest-first eviction.

`ActionHistory` records Action creation and execution at the Runtime intent
boundary. Its snapshots include creation and execution times, lifecycle status,
parameters, associated Entity/Task/Signal IDs, and result or error. Task and
Action capacities default to 1,000 and are independently configurable through
`task_history_size` and `action_history_size`.

`Runtime.inspect()` (also available as `Runtime.snapshot()`) returns a detached,
JSON-safe dictionary containing runtime status and uptime, Entity IDs and state,
active Tasks, queued and recent Signals, recent Actions and errors, scheduler
status, and a per-Entity handler registry summary. Status distinguishes idle,
active, and stopped runtimes. Opaque values are represented safely, cyclic data
is bounded, and inspection never consumes queued work or mutates runtime state.

The public `Entity` now composes immutable identity, an immutable trait
snapshot, and an embodiment-independent self-model. Entity IDs must agree
across all three. Identity and traits cannot be replaced through public
setters; changing embodiment state does not change Entity identity. Structured
mutation-audit vocabulary records target, decision, source, evidence, reason,
before/after values, metadata, and time, but Phase 2 adds no mutation service or
audit store.

Every Runtime uses a replaceable `LogSink` and defaults to an
`InMemoryLogSink`. Structured, timestamped events cover Signal receipt and
routing; Task creation and status changes; Action creation and runtime
execution; Entity state changes; Runtime start and stop; and handler errors.
Events carry severity, the applicable Entity, Signal, Task, and Action IDs,
plus structured metadata. In-memory events can be filtered by severity, event
type, and causal IDs and are returned chronologically.
Logging failures are isolated from runtime execution.

Entities own an ID, mutable state, currently active Tasks, and their handler
registry. An Entity registered with a Runtime can create Actions and emit nested
Signals through simple high-level methods.

Signals include a stable ID, type, source, timezone-aware timestamp, payload,
and metadata. They serialize to dictionaries and JSON. Domain code can use
small typed Signal subclasses while preserving the same transport shape.

Tasks implement pending, running, paused, blocked, completed, failed, and
cancelled states. Their timestamps, owner, priority, context, parent/child IDs,
result, and error remain observable.

Actions are structured intent records with type, parameters, creation time, and
optional Entity and Task attribution. Phase 1 records them but does not execute
them against external systems.

The `action.executed` log event describes execution at the Runtime's structured
intent boundary. It does not claim an external side effect; external Action
execution remains deferred to Medulla.

## Completed

- Phase 0 (`0.1`): repository initialization and cleanup.
- Phase 0: Python `src` package and standard-library test setup.
- Phase 0: architecture, roadmap, decisions, and status documents.
- Phase 1A (`0.1.1`): Signal validation and serialization.
- Phase 1A: typed Signal subclass coverage.
- Phase 1B (`0.1.2`): Entity identity and copied initial state.
- Phase 1B: explicit ordered handler registry.
- Phase 1B: string and Signal-class handler matching.
- Phase 1B: decorator registration without hidden control flow.
- Phase 1C (`0.1.3`): Task lifecycle and invalid-transition checks.
- Phase 1C: Task parent/child identity relationships.
- Phase 1C: structured Action records.
- Phase 1D (`0.1.4`): stable async priority Scheduler.
- Phase 1D: Runtime Entity registration and lookup.
- Phase 1D: handler dispatch, Task creation, and Action collection.
- Phase 1D: failure recording and propagation.
- Phase 1D: nested Signal emission.
- Phase 1E (`0.1.5`): public-API integration coverage.
- Phase 1E: typed battery signal end-to-end scenario.
- Phase 1E: one Signal dispatching independently to multiple Entities.
- Character amendment: ownership invariants, vertical phase plan, base value
  types, Bit seed configuration, and provider-boundary prompts.
- Phase 2 base branch (`0.2`) established from the completed character
  amendment.
- Phase 2A (`0.2.1`): typed runtime log records and a replaceable sink.
- Phase 2A: default chronological in-memory storage and filtered queries.
- Phase 2A: Signal, Task, Action, Entity state, Runtime lifecycle, and error
  instrumentation with related IDs.
- Phase 2A: logging failure isolation and observability tests.
- Phase 2B (`0.2.2`): bounded in-memory Signal history and safe snapshots.
- Phase 2B: latest, ID, type, and source queries with predictable oldest-first
  eviction.
- Phase 2B: structured routing results for handled, unhandled, failed, and
  cancelled processing.
- Phase 2C (`0.2.3`): bounded Task and Action lifecycle histories.
- Phase 2C: reference-backed Task inspection across live, completed, and failed
  states without duplicating mutable Task state.
- Phase 2C: Action creation/execution timestamps, status, associations, result,
  and error inspection.
- Phase 2C: clean latest, ID, and filtered queries with oldest-first eviction.
- Phase 2D (`0.2.4`): unified, framework-independent runtime inspection API.
- Phase 2D: idle, active, and stopped status with monotonic accumulated uptime.
- Phase 2D: JSON-safe Entity, Task, Signal, Action, error, scheduler, and handler
  summaries without exposing internal queue or registry objects.
- Phase 2E (`0.2.5`): end-to-end observability integration and cleanup.
- Phase 2E: exact causal-ID verification across Signal routing, Task lifecycle,
  Action lifecycle, state logs, histories, and runtime inspection.
- Phase 2E: identity, traits, and self-model composed into the public Entity
  with Entity-ID invariants and mutation audit vocabulary.
- Phase 2E: exact event-count coverage confirms Action collection does not
  duplicate creation/execution logging.
- Phase 3 base branch (`0.3`) established from completed Phase 2.
- Phase 3A (`0.3.1`): transport-agnostic `RuntimeService` control boundary.
- Phase 3A: detached Entity, Signal, Task, Action, state, status, and log reads.
- Phase 3A: structured Signal emission, allowlisted state updates, and live
  Task cancellation.
- Phase 3A: clean domain failures and live-runtime coverage of every operation.
- Phase 3B (`0.3.2`): typed developer commands resolved exclusively through
  `RuntimeService`.
- Phase 3B: minimal CLI-oriented text grammar plus direct Console-oriented
  command objects.
- Phase 3B: structured parse, validation, unknown-command, and execution errors.
- Phase 3B: valid, malformed, live cancellation, and arbitrary-Python rejection
  tests.
- Phase 3C (`0.3.3`): transport-neutral live Runtime event subscriptions.
- Phase 3C: ordered, detached event envelopes and category filtering.
- Phase 3C: isolated bounded queues with explicit backpressure policy and drop
  accounting.
- Phase 3C: ordered consumer, category, slow-subscriber, failed-consumer, and
  invalid-subscription coverage.
- Phase 3D (`0.3.4`): documented and stabilized runtime API contract.
- Phase 3D: public `RuntimeServiceProtocol`, response/error conventions,
  developer command mapping, and event subscription schemas.
- Phase 3D: executable contract example verified by the test suite.
- Phase 3D: internal state, drive activation, Signal influence, and attention
  candidates composed into Entity and exposed through `RuntimeService`.
- Phase 3D: atomic influence validation, bounded control changes, drive-weighted
  attention, observability, and no-automatic-Action coverage.
- Phase 4 base branch (`0.4`) established from completed Phase 3D.
- Phase 4A (`0.4.1`): optional FastAPI adapter over `RuntimeServiceProtocol`.
- Phase 4A: health, Runtime, Entity, Signal, Task, Action, state, and log HTTP
  endpoints.
- Phase 4A: structured service-error-to-HTTP mapping and endpoint response
  coverage.
- Phase 4A: subprocess verification that Echo Core runs while FastAPI imports
  are blocked.
- Phase 4B (`0.4.2`): `/events` WebSocket streaming backed by Phase 3 event
  subscriptions.
- Phase 4B: structured live Signal, Task, Action, state, Runtime, log, and error
  envelopes with optional category filtering.
- Phase 4B: clean disconnect removal, future-only reconnect behavior, bounded
  slow-client backpressure, and runtime-isolation coverage.
- Phase 4C (`0.4.3`): initial responsive SvelteKit Echo Console shell.
- Phase 4C: Runtime status header, Overview workspace, API/WebSocket connection
  indicators, and Overview/Signals/Tasks/Entity/Logs/Chat navigation.
- Phase 4C: HTTP status polling, WebSocket event consumption, reconnect/backoff,
  explicit offline state, client-helper tests, type checks, and production
  build verification.
- Phase 4D (`0.4.4`): live and retained Signal Inspector UI.
- Phase 4D: type/source filtering, Signal identity and structured data detail,
  routing results, and related Task/Action IDs.
- Phase 4D: pause/resume visual arrivals without stopping subscription
  consumption, plus live-event, history-loading, and filter tests.
- Phase 4E (`0.4.5`): Task, Entity, Logs, and Chat first-pass Console views.
- Phase 4E: active/history Task inspection, hierarchy, exact detail, and
  confirmed live cancellation through the service endpoint.
- Phase 4E: distinct ordinary state, handler, active Task, identity, trait,
  control, drive, self-model/embodiment, attention, and relationship views.
- Phase 4E: structured severity/event-type log filtering and `UserMessage`
  Signal Chat with Action-backed responses only.
- Phase 4E character: Entity-owned per-person relationship state with detached
  RuntimeService, HTTP, and Console inspection.
- Phase 1F (`0.4-tui_core`): first-party terminal operator interface.
- Phase 1F: orange Echo identity asset, semantic ANSI/plain rendering, seven web
  parity surfaces, safe command prompt, and ordinary Signal-routed Chat.
- Phase 1F: `echo` executable, named tmux workspace, Alt+1–7 view shortcuts,
  nvim/vi config window, shell window, and no-tmux/snapshot fallbacks.
- Phase 1F: local and HTTP clients over the shared management boundary plus an
  ordered, serializable operator-surface registry.
- Phase 1F: standing requirement that each operator-visible iteration update
  TUI and web UI together in small, relevant, tested chunks.
- Phase 5A (`0.5.1`): provider-neutral async intelligence contract.
- Phase 5A: structured inference request/result, health result, provider
  metadata, capabilities, and wall-clock plus elapsed timing.
- Phase 5A: optional embedding/classification protocol seams and a deterministic
  dependency-free `MockProvider`.
- Phase 5B (`0.5.2`): centralized, bounded provider selection and fallback.
- Phase 5B: automatic remote → LAN → offline preference plus isolated remote,
  LAN, and offline modes.
- Phase 5B: selected-provider, failed-attempt, latency, error, health, and
  status inspection with structured exhaustion errors.
- Phase 5B: mocked coverage of all eight automatic availability permutations
  and explicit-mode no-fallback behavior.
- Phase 5C (`0.5.3`): environment/config-driven OpenRouter remote provider.
- Phase 5C: OpenAI-compatible chat completion request, authenticated key health,
  configurable model, normalized Echo results, and actual-model/usage metadata.
- Phase 5C: structured configuration, request, network, API, and response
  errors; one provider attempt with router-owned fallback only.
- Phase 5C: secret-safe provider/model/outcome/timing logs and mocked transport
  coverage with no live API dependency.
- Phase 5D (`0.5.4`): explicitly configured LAN inference provider.
- Phase 5D: llama.cpp/OpenAI-compatible chat completions and `/v1/health`, with
  configurable URL, model, timeout, and optional credential.
- Phase 5D: normalized results, structured failures, no discovery or internal
  retry/fallback, and mocked router-owned offline fallback coverage.
- Phase 5E (`0.5.5`): explicit-path, lazy offline inference fallback.
- Phase 5E: inspectable model lifecycle, coalesced lazy initialization, safe
  inference/unload coordination, and idempotent explicit unload.
- Phase 5E: replaceable local backend boundary and loopback llama-server child
  adapter with bounded startup, request, and shutdown timeouts.
- Phase 5E: verified OpenRouter success and health inspection consume no local
  model RAM/compute, with mocked final-fallback and failure coverage.
- Phase 5F (`0.5.6`): provider observability and complete fallback integration.
- Phase 5F: optional RuntimeService router ownership, all-provider health,
  active provider/model, latest latency, bounded inference attribution, and
  recent failure inspection.
- Phase 5F: shared HTTP, developer-command, web Console, TUI, and tmux provider
  inspection/mode switching for `auto`, `remote`, `lan`, and `offline`.
- Phase 5F: acceptance coverage for OpenRouter → LAN → offline → restored
  OpenRouter and proof that every request records its actual serving provider.
- Phase 5 character: distinct Entity-owned working, episodic, semantic,
  preference, and relationship memory with provider-continuity coverage.
- Phase 6A (`0.6.1`): typed, strict TOML configuration and centralized
  allowlisted environment overrides.
- Phase 6A: Runtime, logging, all bounded history, provider routing,
  OpenRouter, LAN, offline, API server, and Console connectivity sections.
- Phase 6A: startup validation with aggregated dotted-path errors,
  secret-safe representations, Runtime/router factories, CLI validation, and
  an environment-safe example file.
- Phase 6B (`0.6.2`): explicit transactional live configuration reload.
- Phase 6B: typed live-safe/restart-required change classification, all-or-none
  application, invalid-candidate preservation, and structured audit events.
- Phase 6B: live logging, provider routing/preference, and practical history
  limits through the shared service, HTTP, developer command, Console, and TUI.
- Phase 6C (`0.6.3`): effective configuration inspection and approved live-safe
  control with hidden credential fields and clean validation errors.
- Phase 6C: matching HTTP, developer-command, web Console, TUI, and tmux
  Configuration surfaces.
- Phase 6 character: selective importance retention and audited consolidation
  into semantic, preference, and relationship memory.
- Phase 6 completion: non-interactive root installer and one root launcher for
  Core-only, Web Console, terminal Console, or combined startup.
- Phase 7 base branch (`0.7`) established from completed Phase 6C.
- Phase 7A (`0.7.1`): replaceable `StateStore` abstraction and default
  `InMemoryStateStore`.
- Phase 7A: get, set, delete, list/snapshot, and whole-Entity load/save across
  ephemeral, session, and persistent categories.
- Phase 7A: compatible `Entity.state` session mapping plus category-aware Entity
  access and causal state-change observability for all categories.
- Phase 7B (`0.7.2`): SQLite-backed Entity persistent state with immediate,
  transactional set/delete operations and atomic whole-Entity replacement.
- Phase 7B: safe versioned tagged-JSON serialization, explicit schema metadata,
  Entity isolation, durable deletion, and incompatible-schema rejection.
- Phase 7B: Runtime stop/new Runtime acceptance coverage verifies persistent
  restoration while session and ephemeral state expire.
- Phase 7C (`0.7.3`): explicit graceful restart coordinator with Runtime and
  provider admission quiescing.
- Phase 7C: bounded wait-then-cancel and immediate-cancel Task policies,
  required Entity-state persistence, and capability-based provider/transport
  cleanup.
- Phase 7C: verified fresh Runtime construction, persistent-state restoration,
  runtime-only-state expiry, and structured restart reason/status reporting.
- Phase 7D (`0.7.4`): deliberate service and HTTP restart request plus tracked
  progress operations that explicitly report state preservation.
- Phase 7D: Console prepare/type-to-confirm control, live phase polling, old
  event-socket closure, and automatic reconnection to the fresh Runtime.
- Phase 7 character: bounded provider-neutral `CharacterContextBuilder`,
  relevant state/relationship/memory retrieval, inference integration, and
  character reconstruction across development restart.
- The configured host loads Bit's versioned seed files and sends character
  guidance as an authoritative provider system instruction, preventing provider
  model/vendor identity from replacing Bit's identity.
- The project launcher supplies the canonical Entity seed root explicitly so a
  non-editable `.venv` installation resolves Bit's files from the checkout.
- Successful chat exchanges enter bounded Entity-owned working memory and are
  available to later cognition requests in the same runtime generation. The
  character contract keeps raw configuration and context machinery private.
- Phase 7E (`0.7.7`): Entity-scoped durable semantic and character-development
  memory with typed provenance, Echo-owned commit policy, SQLite persistence,
  bounded retrieval, and fresh-Runtime continuity coverage.
- Phase 8 base branch (`0.8`) established from completed Phase 7E.
- Phase 8A (`0.8.1`): versioned UTF-8 JSON Lines representation for Signal
  recording sessions, safe JSON-only validation, durable incremental writes,
  optional Runtime linkage IDs, and non-mutating format reads.
- Phase 8B (`0.8.2`): explicit live Runtime recording lifecycle with a bounded
  non-blocking capture queue, worker-thread durable writes, status and metadata,
  recoverable failure reporting, and runtime API/HTTP/CLI operations.
- Phase 8C (`0.8.3`): one-Signal, sequential, and step-by-step replay through
  normal Runtime emission, preserved origin metadata, fresh receipt identity
  and time, inspectable progress, and an explicit record-only Action policy.
- Phase 8D (`0.8.4`): realtime, accelerated, immediate, and manual-step replay
  timing with monotonic relative scheduling, validated multipliers,
  cancellation, and expanded status/API/HTTP/CLI controls.
- Phase 8E (`0.8.5`): Signal Inspector controls for host-local session
  selection, one-Signal and session replay, timing, manual advance,
  cancellation, progress, and explicit replay labels.
- Phase 8 character: typed JSON-safe intentions, Core behavior arbitration,
  explicit external-Action authorization, interruptibility, and bounded
  background curiosity goals without implicit Action recording or execution.
- Phase 9A (`0.9.1`): protocol-neutral Medulla transport interface, reusable
  lifecycle enforcement, structured transport errors, minimal status/health
  snapshots, safe Signal/Action boundary validation, and neutral Action
  dispatch outcomes.
- Phase 9B (`0.9.2`): bounded local Signal and Action queues, explicit
  reject-newest/drop-oldest overflow behavior, queue inspection and health,
  clean waiter shutdown, fresh restart generations, and optional one-shot
  clock, Open-Meteo weather, and Hacker News source examples.
- Phase 9C (`0.9.3`): strict versioned WebSocket wire messages, validated
  inbound Signals, queued outbound Actions, contained connection failure,
  automatic reconnect backoff, an authentication-header hook, safe protocol
  rejection, and connection health/status.
- Phase 9D (`0.9.4`): optional MQTT broker adapter, explicit broker/TLS/QoS
  configuration, provisional Entity topic mapping, typed Signal ingress,
  queued Action and status publication, reconnects, and isolated health.
- Phase 9E (`0.9.5`): optional serial adapter, explicit port/baud/timeouts,
  embedded-friendly framed/versioned JSON, CRC and noise isolation, typed
  Signal ingress, Action encoding, reconnects, and connection health.
- Phase 9F (`0.9-medulla_node-0.1`): versioned transport-neutral capability
  definitions, stable provider identity, JSON schemas, availability,
  permissions/risk, effect and timeout classification, and deterministic
  registry collision handling.
- Phase 9G (`0.9-medulla_node-0.2`): provider-owned capability inventory,
  removal, provider health, deterministic capability-to-transport routing,
  permission hook, timeout enforcement, introspection, and contained failures.
- Phase 9H (`0.9-medulla_node-0.3`): versioned remote-node manifests, typed
  endpoint/Signal/resource/health/pairing descriptions, registry lifecycle,
  disconnect disablement, reconnect restoration, and WebSocket manifest hooks.

## In progress

Nothing. Phase 9H is complete. The Phase 1F
TUI integration requirement remains active across all later phases.

## Known issues

- SQLite persists ordinary Entity state explicitly categorized as persistent;
  wiring a database path into the configured root host remains deferred.
- Runtime histories and structured logs remain bounded in memory; Phase 8B can
  explicitly record live Signal sessions but does not archive general logs.
- Timed replay does not catch up by overlapping Signal dispatch when a handler
  takes longer than a scheduled offset; normal Runtime ordering remains serial.
- Recording session selection in the web Console uses a path available to the
  Echo host; the current storage API has no remote file browser or upload.
- Actions have no automatic Runtime routing. Phase 9B supplies a local queue
  endpoint and Phase 9C supplies explicit WebSocket dispatch without polling
  Action history or coupling Core to Medulla.
- The root launcher provides a development process host; production service
  supervision, packaging, and deployment remain intentionally unspecified.
- OpenRouter, explicit LAN inference, and lazy local llama.cpp inference are
  implemented. Other embedded model integrations are not implemented.
- The default offline backend requires a separately installed `llama-server`
  executable; the model file alone is not an executable runtime.
- Character memory is typed and in memory only; there are no persistence, ROS,
  or robotics integrations.
- Configuration reload covers logging, routing policy, and practical history
  retention. Provider construction/credentials, Runtime startup, API binding,
  and Console connectivity still require restart.
- Handler enable/disable is not yet represented in the typed application
  schema, so Phase 6B does not attempt unsafe registry mutation.
- Character behavior decisions and curiosity goals are retained in the Entity
  and survive a coordinated development restart copy, but do not yet use a
  dedicated durable store or durable character audit log.
- Scheduler `periodic` is a priority class, not a recurring timer facility.
- Local clock, weather, and news sources are one-shot producers. Echo does not
  yet schedule them, configure them through TOML, or grant them discovered
  capability/trust status.
- Signal payloads must already contain JSON-compatible values for `to_json`;
  Phase 8A recording validates that constraint recursively and rejects unsafe
  values explicitly.

These are roadmap deferrals, not missing Phase 9H acceptance criteria.

## Architecture decisions

- `Echo_Plan.md` remains the architectural source of truth.
- Python 3.11+ and the standard library are sufficient for Echo Core; FastAPI
  is isolated in an optional adapter dependency.
- Dataclasses represent kernel records and the typed configuration schema;
  TOML parsing uses Python 3.11's `tomllib` without a runtime dependency.
- `unittest` verifies the project without downloaded test dependencies.
- The Entity is the public actor abstraction.
- Decorators only register handlers; the Runtime owns dispatch.
- Handler registrations accept event names or Signal subclasses.
- Scheduler ordering is priority first and FIFO within equal priority.
- Awaited emission is the Phase 1 runtime boundary.
- One handler invocation maps to one Task.
- Handler exceptions mark Tasks failed and propagate to the emitter.
- Actions remain recorded intent until an application explicitly dispatches
  an already-authorized Action through Medulla. No transport polls Runtime
  Action history or makes authorization decisions.
- Echo Core does not import Medulla. `echo.medulla` depends inward on Signal
  and Action, and concrete protocol adapters will depend on that boundary.
- Transport status is local and non-probing; health may perform I/O and returns
  unavailable data on probe failure. Unexpected adapter failures become
  structured transport errors, while task cancellation remains cancellation.
- Incoming external Signal shapes and outbound Action values must be detached,
  finite JSON data before crossing a transport implementation hook.
- Phase 9C wire messages are closed, versioned JSON envelopes. Unknown or
  malformed input is rejected as protocol data; executable deserialization is
  prohibited.
- WebSocket remote availability is orthogonal to transport lifecycle. A
  running adapter may be disconnected and degraded while its reconnect loop
  remains active. Authentication is supplied only by an injectable header
  provider in this phase.
- MQTT is an optional Medulla adapter and never an Entity or discovery
  dependency. Its topic hierarchy is configurable implementation guidance for
  Phase 9D rather than a frozen discovery contract.
- Serial framing is a dependency-free embedded protocol boundary. Serial port
  libraries and reconnect policy remain inside Medulla and never enter Entity
  or Core.
- Runtime logging depends only on a small synchronous sink interface.
- Sink failures never alter Runtime control flow.
- Signal history owns copied snapshots, is bounded independently per Runtime,
  and evicts in processing order.
- Task history references live Tasks and creates snapshots only when read.
- Action history adds lifecycle annotations without changing the Action intent
  primitive or introducing an external executor.
- Runtime inspection is a pure read operation that returns detached JSON-safe
  data and remains independent of transport and presentation frameworks.
- `RuntimeService` is the stable control boundary; CLI and future network
  adapters depend on it instead of reaching into Runtime-owned structures.
- Developer commands are closed, typed schemas with explicit dispatch. Text is
  only a presentation adapter and cannot select arbitrary callables.
- Runtime events fan out through isolated bounded queues; publication never
  awaits subscribers, and overflow behavior is explicit and observable.
- `RuntimeServiceProtocol` plus `docs/RUNTIME_API.md` define the stable
  application contract; Runtime coordination and storage stay private.
- The FastAPI app is created around `RuntimeServiceProtocol`; routes translate
  transport shapes only and never read or mutate Runtime internals.
- Each WebSocket owns one bounded service subscription. Disconnect cleanup
  closes and removes it, while socket latency remains outside Runtime
  publication flow.
- The SvelteKit console is a separate optional development surface. It depends
  outward on the HTTP/WebSocket adapter; Echo Core has no Node or browser
  dependency.
- The TUI is a separate optional presentation adapter with no third-party
  runtime dependency. It consumes `RuntimeServiceProtocol` locally or the
  existing HTTP representation remotely and renders detached snapshots.
- Every operator-visible phase updates TUI and web UI in small tested chunks;
  neither interface may gain a subsystem-specific private control path.
- Signal pause/resume is presentation state only; it never pauses Runtime event
  publication or creates an implicit replay buffer.
- Console Chat is only a Signal source; responses are associated Runtime
  Actions, never a direct chat backend result.
- Entity relationship state is scoped per subject, copied at construction, and
  exposed to the Console only through detached service snapshots.
- Signals may affect internal state and drive activation only through explicit,
  validated influence requests linked to retained Signal IDs.
- Drives influence attention candidate scoring but never authorize Actions.
- State writes are denied unless the service is configured with an explicit
  per-Entity key allowlist.
- Runtime-owned log read history is independent of external sink behavior.
- Entity owns immutable identity and trait snapshots plus its self-model;
  construction rejects cross-Entity character state.
- Mutation audit types are vocabulary only; approval policy, mutation services,
  durable audit storage, and provider integration remain later work.
- Requested character base modules are concrete, tested value types rather than
  empty interfaces; all other future packages remain unscaffolded.
- Echo owns Entity continuity; providers return untrusted cognitive proposals.
- Identity is independent of inference provider and embodiment.
- Bit-specific seeds remain outside the generic framework package.
- OpenRouter configuration comes only from an explicit config object or
  `OPENROUTER_*` environment values; API keys never enter metadata or logs.
- OpenRouter makes one request per inference. Echo-level provider fallback is
  owned only by `ProviderRouter`.
- LAN inference requires an explicit URL; Echo performs no automatic network
  discovery. The provider makes one request and leaves fallback to the router.
- Offline inference requires an explicit model path, remains lazy until selected
  by the router, and exposes explicit loaded/unloaded state and unload control.
- Provider selection remains solely in `ProviderRouter`; RuntimeService and
  operator adapters expose policy and observations without duplicating it.
- Routing history is bounded, per request, and records the provider that
  actually served a successful inference.
- Memory belongs to Entity and is unchanged by provider routing or replacement.
- Configuration reload validates before mutation, rejects a mixed
  restart-required candidate as one transaction, and audits every outcome.
- Configuration inspection is field-oriented and secret-safe; control accepts
  only live-editable dotted paths and reuses full schema validation.
- Memory retention is evidence-scored, while consolidation is an explicit Echo
  decision rather than a direct inference-provider mutation.
- Entity ordinary state depends on the small `StateStore` protocol rather than
  an embedded dictionary. Session is the compatibility default, while
  ephemeral and persistent are explicit categories.
- StateStore collection reads and whole-Entity loads are detached snapshots;
  whole-Entity saves replace one Entity atomically at the abstraction boundary.
- All three ordinary-state categories remain observable when changed during a
  handler. Session-state event metadata remains backward compatible; other
  categories add an explicit category field.
- `SQLiteStateStore` stores only Entity-scoped persistent key/value state.
  Session and ephemeral categories delegate to in-memory storage and expire
  with that store instance.
- Persistent values use a non-executable, versioned tagged-JSON codec rather
  than pickle. Unsupported and cyclic values are rejected before a write
  transaction begins.
- SQLite schema and serializer metadata are validated when the store opens.
  Per-key writes and deletes transact immediately; whole-Entity replacement is
  atomic and cannot affect another Entity's rows.
- Graceful restart is explicit orchestration, not mutation of a live Runtime.
  Quiescing permanently closes that generation's admission gate; the restart
  factory must return a distinct Runtime with fresh runtime-only structures.
- The `wait` Task policy uses a bounded grace period and cancels remaining work;
  the `cancel` policy cancels active handler Tasks immediately. Persistence and
  resource shutdown happen only after Task settlement.
- Provider routing rejects new inference after quiescing and waits for an
  in-flight inference before closing each provider. Concrete providers close
  capable transports, and the offline provider unloads its backend.
- Restart success requires restored persistent snapshots for every prior
  Entity. Reason, status, and old/new Runtime IDs remain inspectable and are
  published as Runtime lifecycle events.
- Restart control remains behind `RuntimeServiceProtocol`. The API requires an
  exact confirmation token and preservation enabled, returns `202`, and exposes
  progress through an opaque operation ID.
- The API process is not replaced. On success the service swaps its Runtime and
  generation-scoped dependencies, closes old subscriptions with code `1012`,
  and accepts new subscriptions against the fresh Runtime.
- Character context is the structured `InferenceRequest.context` field,
  assembled by deterministic bounded retrieval. OpenAI-compatible adapters
  render the same canonical system message, while offline backends receive the
  same request contract.
- Signal session recordings use a versioned, line-oriented, non-executable JSON
  format. Every line is independently self-describing; session boundaries and
  Signal capture are distinct record types, and causal Runtime IDs remain
  optional linkage rather than mutable Signal data.
- Recording refuses to overwrite an existing file and synchronizes complete
  lines by default. A missing final session record represents an incomplete
  session, while invalid ordering, versions, timestamps, or counts fail
  explicitly. Reading a recording performs no replay or Runtime mutation.
- Live recording attaches once at the Runtime's post-dispatch boundary so
  nested and externally submitted Signals share one path. Dispatch uses only a
  bounded `put_nowait`; serialization and filesystem work execute outside the
  event-loop path. Overflow is explicit and counted rather than applying
  backpressure to Signal handlers.
- Recording write failures detach capture, retain failed status, and enter the
  normal recoverable Runtime error stream. Explicit stop still attempts to
  close the session. Restart is rejected while a recording is active or failed
  but not yet stopped, preventing a session from silently crossing Runtime IDs.
- Replay always reconstructs a base `Signal` with a fresh ID and current UTC
  receipt timestamp and dispatches it through `Runtime.emit`. Original identity
  and time remain under the runtime-owned `metadata.echo_replay` marker.
- Replay exposes only `record_only`: Action objects remain observable Runtime
  intent records, and no external Action executor is invoked. Any future policy
  permitting external effects must be added and selected explicitly.

## Phase 7E durable memory

- Production startup now opens the configured SQLite database for ordinary
  persistent Entity state and durable memory; the default local path is
  `.echo/echo.sqlite3` beside the selected configuration file.
- Durable semantic and character-development records are Entity-scoped and
  carry canonical keys, typed provenance, source references, confidence,
  importance, lifecycle status, versions, access data, and supersession links.
- `MemoryService` applies Echo-owned commit policy. It rejects model knowledge
  as personal memory, protects direct-experience provenance, merges exact
  duplicates, and conservatively defers character development without repeated
  reflected evidence.
- Normal declarative UserMessage evidence is extracted conservatively after a
  successful response. A failed durable write is logged as recoverable and
  does not remove that response.
- `CharacterContextBuilder` injects only bounded active memories and records
  access. Fresh-runtime tests prove retrieval without transcript replay and
  across provider changes.
- RuntimeService and HTTP APIs expose list, inspect, archive, and explicit
  delete operations. No memory UI, embedding store, autonomous decay,
  intention, or reflection engine was added.

## Phase 8B runtime session recording

- `RuntimeSessionRecorder` owns a bounded queue and the Phase 8A JSON Lines
  writer. Header creation, each Signal write, flush/`fsync`, and footer closure
  run through `asyncio.to_thread`.
- Runtime capture occurs after routing results are known and includes optional
  Runtime, Entity, Task, and Action linkage. Snapshots come from detached Signal
  history data rather than retaining caller-owned payload containers.
- `RuntimeServiceProtocol` exposes typed start, stop, and status operations.
  HTTP uses `POST`, `DELETE`, and `GET /runtime/recording`; developer commands
  use `record start`, `record stop`, and `record status`, including remote
  one-shot `echoc` translation.
- Lifecycle conflicts are structured. Status distinguishes idle, starting,
  recording, stopping, stopped, and failed, and retains output/session metadata
  plus capture counts and the last failure.
- Phase 8B does not read sessions into a Runtime, replay Signals, control replay
  speed, add a web Console surface, or introduce behavior policy.

## Phase 8C Signal replay

- `RuntimeSignalReplay` loads only the validated Phase 8A representation and
  selects either one recorded ID or all Signal records in file order.
- Phase 8C's original one-Signal and immediate sequential modes finish in the
  start operation. Manual mode starts `ready` and each explicit advance
  dispatches exactly one Signal; Phase 8D adds background timed sessions.
- Each replayed Signal has a fresh ID/current timestamp and a runtime-owned
  `echo_replay` metadata marker containing original ID/time, record time,
  session/replay IDs, and `record_only` policy.
- Runtime service operations expose start, step, and status. HTTP uses
  `/runtime/replays`; developer and `echoc` commands use `replay signal`,
  `replay session`, `replay step`, `replay next`, and `replay status`.
- Handler failures mark replay failed, remain visible in normal Signal/Task
  history and Runtime error logs, and do not kill Echo.
- Signal Inspector drives replay only through the shared HTTP service. Since
  recordings are host-local files, the current selector is an explicit host
  path rather than a browser upload or a parallel storage catalog.
- `Intention` parameters are copied through strict JSON serialization. Behavior
  policy is Core-owned; external behavior requires explicit authorization, and
  even approval creates only an unexecuted Action intent.
- Attention-derived curiosity stays at background priority or lower and is
  deferred while the Entity has active work.

## Phase 8D replay timing

- `ReplayTiming` separates `realtime`, `accelerated`, `immediate`, and
  `manual_step` scheduling from one-Signal versus sequential selection.
- Realtime and accelerated deadlines preserve offsets from the first recorded
  Signal using the monotonic event-loop clock. Acceleration divides every
  offset by the required finite positive multiplier, avoiding cumulative sleep
  drift.
- Timed sessions start in a background Task. Immediate replay retains the
  Phase 8C synchronous completion behavior, while manual replay advances only
  through `replay_next_step`.
- Cooperative cancellation wakes a pending timed wait immediately and never
  cancels a handler mid-dispatch. Status reports `cancelled`, completed time,
  cancellation request, next index, and remaining count.
- Runtime service and HTTP add `cancel_replay` and
  `DELETE /runtime/replays/{id}`. Developer/`echoc` commands add
  `replay cancel`; `replay session` accepts realtime, accelerated multiplier,
  immediate, or manual-step timing.

## Phase 8E Console replay and character behavior

- Signal Inspector accepts a recording path available to the Echo host and
  starts one-Signal or sequential replay through the existing HTTP API.
- Timing controls expose immediate, realtime, accelerated multiplier, and
  manual-step modes. Status polling reports lifecycle, session ID, completed
  and remaining counts, percentage, speed, and the record-only safety policy.
- Active sessions can be stopped; manual sessions expose one explicit advance
  operation. History refresh is triggered only when progress or terminal state
  changes.
- Live and retained replayed traffic has a visible Replay badge. Signal detail
  labels replayed activity and shows its original recorded Signal ID/time.
- `BehaviorController` owns bounded typed intention, decision, and curiosity
  state for an Entity. `BehaviorPolicy` gates external Actions, resources,
  capability, confidence, relationship trust, user attention, and active-work
  interruptibility without executing behavior.
- High-scoring attention can create only a background-priority curiosity goal;
  active work produces a deferred goal. Entity inspection and coordinated
  restart copies preserve the resulting character state.

## Phase 9A Medulla transport abstraction

- `Transport` is a runtime-checkable, protocol-neutral async interface for
  start, stop, receive, execute, status, and health.
- `BaseTransport` centralizes lifecycle enforcement and translates unexpected
  adapter exceptions into inspectable Medulla errors. Echo Core remains free
  of transport imports and transport failures cannot enter its control flow.
- Receive returns only a freshly detached, validated Echo Signal. The external
  mapping validator accepts a closed canonical schema and plain finite JSON;
  it does not import types or deserialize Python objects.
- Execute receives a detached, validated Action and returns accepted,
  completed, rejected, or failed protocol-neutral dispatch data. It does not
  authorize the Action or interpret the result cognitively.
- Status performs no I/O. Health can probe availability and contains probe
  failure as an unavailable snapshot. Lifecycle and availability remain
  separate dimensions.
- Concrete transports, Runtime supervision/injection, discovery, capability
  registration/routing, pairing, permissions, and trust remain deferred.

## Phase 9B local transport

- `LocalQueueTransport` implements the Phase 9A structural contract without a
  network or device dependency.
- `publish_signal()` and standard `receive()` form the inbound boundary;
  standard `execute()` and `receive_action()` form the outbound boundary.
- Inbound and outbound capacities are positive and independently configured.
  Queue offers never wait for space. Reject-newest raises a retryable
  `queue_full` error; drop-oldest accepts the new item, reports the displaced
  ID, and increments a direction-specific counter.
- Local status reports both depths, capacities, overflow policies, rejections,
  drops, and shutdown discards. Running with available capacity is healthy,
  saturation is degraded, and a stopped transport is unavailable.
- Shutdown wakes every pending Signal and Action reader, discards queued work,
  and prevents stale items from crossing a later restart.
- The adapter targets one asyncio event loop. Runtime pumps, Action routing,
  network/device transports, discovery, authorization, and trust are deferred.
- Optional local source adapters normalize clock, Open-Meteo weather, and
  Hacker News data into three Signal types. Network and schema failures stop
  at the source boundary before queue publication.

## Phase 9C WebSocket transport

- `WebSocketTransport` is a client adapter implementing the Phase 9A contract;
  startup succeeds even when its configured remote is offline.
- The `echo.medulla` version-1 envelope contains exactly `protocol`, `version`,
  `type`, `id`, and `payload`. Supported type names anticipate later protocol
  growth without activating discovery.
- Only strict UTF-8 JSON is decoded. Size limits, duplicate-key rejection,
  finite-number validation, closed Signal validation, and ordinary built-in
  container checks keep external data non-executable.
- Signals enter a bounded inbound queue. Actions enter a bounded outbound
  queue and are sent after the active connection or a later reconnect accepts
  them. Queue saturation is a structured retryable failure.
- Unknown, malformed, directionally unsupported, and premature manifest
  messages receive a bounded `error` response and do not crash Echo.
- Connection loss is contained and retried with capped exponential backoff.
  Stop cancels the manager and clears this generation's queues.
- Local status and active health expose connection state, sanitized endpoint,
  connection timestamps, reconnect/message counters, queue depths, peer hello
  identity, and wire version. Query parameters and authentication headers are
  excluded from inspection.
- `authentication_headers` is a synchronous-or-asynchronous injection hook.
  Phase 9C defines no token format, credential store, pairing, authorization,
  trust, manifest ingestion, discovery registry, or result correlation.

## Phase 9D MQTT transport

- `MQTTBrokerConfig` requires an explicit hostname and client ID and exposes
  port, TLS, username/password, keepalive, and connection timeout. Passwords
  are excluded from representations and all status/health output.
- `MQTTTopicMap` defaults to `echo/{entity}/signals/#` for inbound Signals,
  `echo/{entity}/actions/{encoded-type}` for outbound Actions, and
  `echo/{entity}/status/transport` for connection observations. The prefix is
  configurable and the mapping is not declared a permanent discovery schema.
- MQTT payloads reuse the version-1 Medulla JSON envelope. Only `signal`
  messages accepted on the Signal subscription become typed Echo Signals;
  malformed or directionally incorrect messages are rejected and counted.
- Actions use a bounded outbound queue and configured QoS. Offline and
  interrupted publishes are retried after reconnect within the same transport
  generation; saturation returns a structured retryable failure.
- Broker connection failure degrades MQTT health while the reconnect manager
  remains active. It does not fail Echo Runtime, the Medulla supervisor, or a
  healthy local/WebSocket transport.
- Status events publish on connect and graceful stop. They are non-retained so
  an abrupt disconnect cannot leave a stale retained `connected` assertion in
  the absence of Phase 9D Last Will/session-presence semantics.
- `aiomqtt` is an optional extra and is imported lazily. MQTT types do not
  enter `echo.core`, `Entity`, capability discovery, or trust policy.

## Phase 9E serial transport

- `SerialPortConfig` requires a port and positive baud rate, with explicit
  finite read and write timeouts. `pyserial` is a lazy optional dependency.
- Each frame begins and ends with `0x7E`. Within the frame, `0x7E` and `0x7D`
  bytes are escaped as `0x7D` followed by the byte XOR `0x20`.
- The unescaped body is one frame-version byte, a big-endian unsigned 16-bit
  payload length, that many UTF-8 JSON bytes, then a big-endian CRC-32 over the
  version, length, and JSON bytes. Frame version 1 is currently supported.
- The JSON is the independent version-1 Medulla wire envelope. Serial ingress
  accepts only `signal` messages and passes them through canonical closed
  Signal validation. Outbound Actions use `action` messages in the same frame.
- Incremental parsing has a configured maximum payload and encoded-buffer
  bound. Boot text, partial frames, invalid escapes, unsupported versions,
  mismatched lengths, bad CRCs, malformed JSON, and wrong message directions
  are discarded and counted before Core can observe them.
- Device open/read/write failures leave the transport running but degraded and
  reconnect with capped backoff. Bounded queued or interrupted Actions retry
  after reconnect within the same generation. Other transports keep running.
- Status and health report port/baud, connection timestamps and state,
  reconnects, accepted/sent/rejected frames, discarded noise bytes, queue
  depths, payload bound, and framing/wire versions.
- The frame codec and parser have no `pyserial` dependency, so firmware can
  implement the documented byte protocol without the Python Echo stack.

## Next task

Stop after Phase 9H. Do not begin Phase 9I, add active discovery or implicit
Action execution, or implement pairing, authorization policy, and trust
without separate authorization.

## Important files

- `Echo_Plan.md` — authoritative specification.
- `install.sh` — non-interactive full Python and Web Console installation.
- `start.sh` — configured Core launcher with optional Web/TUI flags and child
  cleanup.
- `README.md` — setup and minimal Phase 1 example.
- `pyproject.toml` — package metadata and Python requirement.
- `src/echo/__init__.py` — public kernel API.
- `src/echo/config.py` — typed TOML application schema, environment overlays,
  aggregated validation, logging setup, and Runtime/provider factories.
- `src/echo/config_reload.py` — explicit transactional reload, change
  classification, inspection/control, rollback, redaction, and structured
  results.
- `echo.example.toml` — secret-free complete configuration example.
- `src/echo/core/signal.py` — Signal representation and serialization.
- `src/echo/core/recording.py` — Phase 8A versioned session/Signal record
  schemas, safe JSON validation, JSON Lines writer, and non-replaying reader.
- `src/echo/runtime_recording.py` — Phase 8B bounded asynchronous live capture,
  lifecycle state/status, worker-thread writes, and recoverable failure path.
- `src/echo/runtime_replay.py` — Phase 8C/8D normal-path Signal reconstruction,
  monotonic timing, cooperative cancellation, origin metadata, progress, and
  safety policy.
- `src/echo/entity/behavior.py` — Phase 8 typed intentions, Core arbitration,
  explicit external authorization, and bounded curiosity goals.
- `src/echo/core/entity.py` — Entity public abstraction.
- `src/echo/entity/state_store.py` — StateStore protocol, state lifetime
  categories, in-memory implementation, and session compatibility view.
- `src/echo/state/sqlite.py` — SQLite persistent-state implementation, schema
  metadata, transactions, and safe tagged-JSON serialization.
- `src/echo/restart.py` — graceful restart phases, Task policy, persistence,
  resource cleanup, restoration verification, and structured results.
- `src/echo/entity/context.py` — bounded provider-neutral Character context and
  deterministic relevance selection.
- `src/echo/core/handlers.py` — explicit handler registration.
- `src/echo/core/task.py` — Task data and lifecycle transitions.
- `src/echo/core/action.py` — structured Action intent.
- `src/echo/core/scheduler.py` — stable priority scheduling.
- `src/echo/core/runtime.py` — dispatch and coordination.
- `src/echo/runtime_service.py` — transport-agnostic runtime service API,
  request/result types, and domain errors.
- `src/echo/host.py` — configured Runtime/service/FastAPI composition and
  Uvicorn process entry point.
- `src/echo/developer_commands.py` — typed developer commands, explicit service
  dispatcher, minimal text parser, and command errors.
- `src/echo/runtime_events.py` — event categories, subscription envelopes,
  bounded subscriber queues, and non-blocking broker.
- `src/echo/providers/base.py` — provider-neutral async protocols, capabilities,
  structured requests/results, metadata, health, and timing.
- `src/echo/providers/mock.py` — deterministic dependency-free test provider.
- `src/echo/providers/router.py` — routing modes, bounded selection/fallback,
  attempt diagnostics, bounded request attribution, health/status inspection,
  and structured exhaustion.
- `src/echo/entity/memory.py` — five typed, Entity-owned memory record classes,
  importance scoring, consolidation validation, and bounded audit histories.
- `src/echo/entity/memory_store.py` — durable record/candidate vocabulary,
  provenance and lifecycle policy, repository protocol, and bounded retrieval.
- `src/echo/state/sqlite_memory.py` — transactional SQLite durable-memory
  repository with Entity-scoped inspection, supersession, archive, and delete.
- `tests/test_phase7e_durable_memory.py` — restart, provider independence,
  Entity isolation, provenance, deduplication, supersession, correction,
  character conservatism, failure degradation, and bounded retrieval coverage.
- `tests/test_phase5f_integration.py` — complete fallback/restore attribution,
  mode switching, optional-provider, and character continuity acceptance.
- `tests/test_config.py` — Phase 6A parsing, environment precedence,
  validation, secret redaction, logging, and factory coverage.
- `tests/test_config_reload.py` — Phase 6B live application, route preference,
  restart rejection, invalid-candidate preservation, audit, redaction, and
  Phase 6C inspection/control coverage.
- `tests/test_phase6_character.py` — selective memory retention and audited
  consolidation acceptance/rejection coverage.
- `tests/test_phase7b_sqlite_state.py` — restart restoration, type preservation,
  Entity isolation, transaction safety, deletion, and schema-version coverage.
- `tests/test_phase7c_graceful_restart.py` — admission quiescing, wait/cancel
  policies, cleanup, state restoration, fresh runtime state, and reporting.
- `tests/test_phase7d_restart_controls.py` — deliberate restart controls,
  progress, preservation, event reconnection, and Character context retrieval.
- `src/echo/providers/openrouter.py` — OpenRouter configuration, HTTP transport,
  inference normalization, authenticated health, errors, and safe logging.
- `src/echo/providers/lan.py` — explicit LAN/llama.cpp configuration, transport,
  inference normalization, readiness health, timeouts, and structured errors.
- `src/echo/providers/offline.py` — explicit local model configuration, lazy
  lifecycle, unload, backend protocol, and llama-server subprocess adapter.
- `tests/test_openrouter_provider.py` — mocked request, health, normalization,
  error, logging, secret-redaction, and router-fallback coverage.
- `tests/test_lan_inference_provider.py` — mocked URL/configuration, inference,
  health, timeout/error, no-discovery, and router-fallback coverage.
- `tests/test_offline_inference_provider.py` — lazy load, remote battery guard,
  concurrency, state, unload, backend failure, and router-fallback coverage.
- `src/echo/adapters/fastapi.py` — optional HTTP adapter and app factory.
- `src/echo/cli.py` — `echoc`, `echoc console`, one-shot commands, and editor
  handoff.
- `src/echo/tui/app.py` — semantic renderers and full-screen terminal loop.
- `src/echo/tui/client.py` — local and HTTP management-plane clients.
- `src/echo/tui/registry.py` — discoverable operator-surface metadata.
- `src/echo/tui/tmux.py` — tmux session, windows, and friendly shortcuts.
- `docs/TUI.md` — terminal usage, parity, boundaries, and integration rule.
- `console/src/routes/+page.svelte` — initial Echo Console layout, navigation,
  connection lifecycle, live activity, and Signal stream coordination.
- `console/src/lib/SignalInspector.svelte` — live/history Signal inspection,
  replayed-activity labeling, one-Signal/session replay controls, timing,
  progress, manual advance, and stop.
- `console/src/lib/TaskInspector.svelte` — active/history Task lists, hierarchy,
  detail, and cancellation.
- `console/src/lib/EntityInspector.svelte` — Entity state, handlers, active
  Tasks, separated character sections, and relationship context.
- `console/src/lib/LogsView.svelte` — structured severity/event-type log view.
- `console/src/lib/ChatView.svelte` — UserMessage Signal input and
  Action-associated responses.
- `console/src/lib/ProviderInspector.svelte` — provider health, active route,
  preference, latency, failure/history inspection, mode controls, and explicit
  configuration reload/restart reporting.
- `console/src/lib/ConfigurationInspector.svelte` — secret-safe effective
  configuration, live/restart labels, validated controls, and field errors.
- `console/src/lib/RestartControl.svelte` — deliberate restart confirmation,
  preservation status, progress polling, and reconnect trigger.
- `console/src/lib/echo-client.ts` — HTTP clients, event reconciliation,
  filtering, and display helpers.
- `console/vite.config.ts` — local HTTP and WebSocket development proxy.
- `src/echo/entity/attention.py` — attention proposal and retained candidate
  schemas.
- `src/echo/entity/influence.py` — bounded Signal influence schema.
- `src/echo/core/runtime_log.py` — structured events and log sink interface.
- `src/echo/core/signal_history.py` — bounded Signal snapshots and queries.
- `src/echo/core/task_history.py` — reference-backed Task lifecycle history.
- `src/echo/core/action_history.py` — bounded Action lifecycle history.
- `src/echo/medulla/transport.py` — Phase 9A structural transport protocol,
  lifecycle base, safe boundary validation, status/health records, Action
  dispatch outcomes, and structured errors.
- `src/echo/medulla/capability.py` — Phase 9F transport-neutral capability,
  provider, schema, availability, permissions, risk, effect, timeout, and
  Phase 9G provider health, ownership, removal, and registry introspection.
- `src/echo/medulla/router.py` — Phase 9G permission-gated deterministic
  capability-to-transport routing, timeout enforcement, and failure
  containment.
- `src/echo/medulla/node.py` — Phase 9H versioned remote-node manifest,
  identity/endpoints/Signals/resources/health/security descriptions, wire
  helpers, and disconnect/reconnect registry lifecycle.
- `src/echo/medulla/local.py` — Phase 9B bounded same-process Signal and Action
  queues, overflow policies, shutdown wakeup, and queue health/status.
- `src/echo/medulla/wire.py` — Phase 9C versioned JSON envelope, safe codec,
  message types, and Signal/Action message conversion.
- `src/echo/medulla/websocket.py` — Phase 9C reconnectable client transport,
  authentication hook, bounded queues, protocol rejection, and health/status.
- `src/echo/medulla/mqtt.py` — Phase 9D optional MQTT broker transport, topic
  mapping, bounded queues, reconnect management, and broker health/status.
- `src/echo/medulla/serial.py` — Phase 9E embedded framing codec/parser and
  optional reconnectable serial transport with health/status.
- `src/echo/medulla/supervisor.py` — application-level transport lifecycle,
  inbound Runtime pumps, bounded failure inspection, aggregate health, and
  explicit outbound Action dispatch.
- `src/echo/application.py` — one-Entity convenience composition for a
  programmatic Entity or project-owned seed directory, Runtime, and Medulla.
- `src/echo/medulla/sources/` — one-shot clock, Open-Meteo weather, Hacker News,
  and bounded JSON source adapters for local transport experiments.
- `examples/local_medulla_sources.py` — runnable local transport demonstration
  for all three source Signals with configurable coordinates and headline cap.
- `docs/MEDULLA.md` — normative Medulla separation, lifecycle, failure,
  validation, local queue, supervision, and later-phase deferral rules.
- `docs/DEVELOPER_QUICKSTART.md` — clean-project installation, Entity seed,
  local Medulla, custom Signal/Action, and future website integration guide.
- `tests/test_phase9a_transport.py` — lifecycle, protocol conformance,
  validation, error containment, health degradation, and cancellation coverage.
- `tests/test_phase9b_local_transport.py` — inbound/outbound Echo flow,
  overflow, isolation, inspection, shutdown, and restart-generation coverage.
- `tests/test_phase9c_websocket_transport.py` — wire validation, bidirectional
  flow, connection containment, reconnects, auth hook, and health coverage.
- `tests/test_phase9d_mqtt_transport.py` — topic/broker configuration, typed
  flow, invalid payload isolation, reconnect delivery, and multi-transport
  failure containment.
- `tests/test_phase9e_serial_transport.py` — byte framing, noise recovery,
  Signal/Action flow, reconnect delivery, and multi-transport isolation.
- `tests/test_phase9f_capability_contract.py` — JSON round trips, provider
  placement, schema safety, effect classification, stable lookup, collision
  handling, and availability inspection.
- `tests/test_phase9g_registry_router.py` — provider ownership/removal and
  health, deterministic duplicate-name routing, permission gating, timeout,
  transport failure, provider exception, and introspection coverage.
- `tests/test_phase9h_node_protocol.py` — manifest/wire round trips, version
  rejection, inert-data enforcement, ownership, routing lifecycle, and
  WebSocket disconnect integration.
- `tests/test_phase9_character_evolution.py` — bounded trait evolution,
  evidence validation, accepted/rejected audit, and restart-copy continuity.
- `tests/test_medulla_supervisor.py` — automatic inbound pumping, explicit
  outbound dispatch, failure isolation, health, application loading, and clean
  shutdown coverage.
- `tests/test_installed_package.py` — builds and installs the wheel into a
  clean environment and runs a downstream project-owned Entity end to end.
- `tests/test_local_medulla_sources.py` — deterministic offline normalization,
  publication, validation, fallback URL, and hard-bound coverage.
- `src/echo/core/inspection.py` — JSON-safe inspection conversion.
- `src/echo/entity/audit.py` — character mutation audit vocabulary.
- `src/echo/entity/traits.py` — immutable traits plus reflection evidence and
  Echo-owned bounded evolution policy.
- `tests/test_signal.py` — Signal unit tests.
- `tests/test_phase8a_signal_recording.py` — Phase 8A format, durability,
  lifecycle, safety rejection, partial-session, and compatibility coverage.
- `tests/test_phase8b_runtime_recording.py` — live/nested capture, causal
  linkage, non-blocking slow writes, failure isolation, lifecycle, HTTP, and
  CLI coverage.
- `tests/test_phase8c_signal_replay.py` — one-Signal, ordered, step, origin
  metadata, Action safety, developer-command, and HTTP replay coverage.
- `tests/test_phase8d_replay_timing.py` — relative timing, acceleration,
  immediate/manual modes, cancellation, status, command, and HTTP coverage.
- `tests/test_phase8_character_behavior.py` — safe intentions, behavior gates,
  interruptibility, curiosity priority/deferral, Action isolation, inspection,
  and restart-copy coverage.
- `tests/test_entity.py` — Entity and registry unit tests.
- `tests/test_task_action.py` — Task and Action unit tests.
- `tests/test_scheduler_runtime.py` — Scheduler and Runtime unit tests.
- `tests/test_integration.py` — public-API integration tests.
- `tests/test_runtime_logging.py` — Phase 2A observability coverage.
- `tests/test_signal_history.py` — Phase 2B Signal history coverage.
- `tests/test_task_action_history.py` — Phase 2C history coverage.
- `tests/test_runtime_inspection.py` — Phase 2D snapshot coverage.
- `tests/test_phase2_observability.py` — Phase 2 acceptance and character tests.
- `tests/test_runtime_service.py` — every Phase 3A operation against a live
  Runtime plus domain-error behavior.
- `tests/test_developer_commands.py` — Phase 3B command dispatch, grammar,
  validation, cancellation, and arbitrary-execution rejection.
- `tests/test_runtime_events.py` — Phase 3C ordering, filtering, backpressure,
  failure isolation, and subscription validation.
- `tests/test_phase3_character.py` — Phase 3 character composition, influence,
  attention, atomicity, and observability.
- `tests/test_phase4_character.py` — Phase 4 relationship composition,
  detachment, validation, and service inspection.
- `tests/test_api_contract.py` — executes the documented contract example.
- `tests/test_fastapi_adapter.py` — HTTP response/error mapping, WebSocket
  streaming lifecycle, slow-client behavior, and optional dependency isolation
  coverage.
- `console/src/lib/echo-client.test.ts` — frontend API, live Signal, history,
  filter, event URL, and display helper coverage.
- `docs/RUNTIME_API.md` — stable Phase 3 service, command, subscription, and
  response/error contract.
- `docs/HTTP_API.md` — Phase 4A HTTP and Phase 4B WebSocket contracts.
- `docs/SIGNAL_RECORDING.md` — Phase 8 JSON Lines schema, live recording,
  replay metadata, modes, control surfaces, and safety contract.
- `examples/runtime_api_contract.py` — executable documented contract example.
- `docs/ARCHITECTURE.md` — implemented architecture boundary.
- `docs/CHARACTER_ARCHITECTURE.md` — persistent character design and phased
  acceptance criteria.
- `docs/ROADMAP.md` — phase status and deferrals.
- `docs/DECISIONS.md` — accepted architecture decisions.
- `docs/CURRENT_STATE.md` — concise handoff record.
- `src/echo/entity/` — provider-independent character base value types.
- `entities/bit/` — Bit reference configuration and prompts.
- `prompts/` — generic model-boundary contracts.
