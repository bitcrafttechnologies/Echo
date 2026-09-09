# Current State

## Current implementation

Echo through Phase 7B plus the `0.4-tui_core` interface track consists of a
minimal, standard-library Python kernel, an optional FastAPI HTTP and WebSocket
adapter, and a separate SvelteKit development console shell. The documented,
transport-agnostic runtime
contract, structured developer commands, and live event subscriptions remain
the only control boundary used by the transport and presentation layers. Phase
3 character state, drives, Signal influence, and attention candidates, plus
Phase 4 per-person relationship state, are implemented in memory.

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
routing. This bridge does not add conversation persistence or context retrieval.

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
in bounded in-memory audit histories. Persistence and context retrieval remain
assigned to Phase 7.

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
relationship learning and Bit-specific configuration loading remain later work;
the checked-in Bit files are still reference scaffolding.

The public package exports:

- `EchoConfig`, its typed section dataclasses, `ConfigurationIssue`,
  `ConfigurationError`, and `load_config`
- `RuntimeConfigurationManager`, reload change/result schemas and enums, and
  `ConfigurationReloadApplyError`
- secret-safe `ConfigurationInspectionResult`/`ConfigurationField` schemas and
  the validated `parse_config()` mapping entry point
- `Signal`
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

## In progress

Nothing. Phase 7B is complete. The Phase 1F
TUI integration requirement remains active across all later phases.

## Known issues

- SQLite persists ordinary Entity state explicitly categorized as persistent;
  wiring a database path into the configured root host remains deferred.
- Runtime histories and structured logs are in memory only.
- Signal replay storage is not implemented.
- Actions have no Medulla executor or transport.
- The root launcher provides a development process host; production service
  supervision, packaging, and deployment remain intentionally unspecified.
- OpenRouter, explicit LAN inference, and lazy local llama.cpp inference are
  implemented. Other embedded model integrations are not implemented.
- The default offline backend requires a separately installed `llama-server`
  executable; the model file alone is not an executable runtime.
- Character memory is typed and in memory only; there are no persistence, ROS,
  or robotics integrations.
- Bit YAML configuration is not yet loaded into `echo.core.Entity`.
- Configuration reload covers logging, routing policy, and practical history
  retention. Provider construction/credentials, Runtime startup, API binding,
  and Console connectivity still require restart.
- Handler enable/disable is not yet represented in the typed application
  schema, so Phase 6B does not attempt unsafe registry mutation.
- Character memory retention and consolidation are in memory only. There is no
  durable character persistence, context builder, behavior policy, or durable
  character audit store.
- Scheduler `periodic` is a priority class, not a recurring timer facility.
- Signal payloads must already contain JSON-compatible values for `to_json`.

These are roadmap deferrals, not missing Phase 7B acceptance criteria.

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
- Actions are recorded intent until Medulla exists.
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

## Next task

Stop after Phase 7B. Do not begin configured host persistence, general event or
history storage, semantic-memory persistence, context retrieval, Signal replay,
or behavior policy without separate authorization.

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
- `src/echo/core/entity.py` — Entity public abstraction.
- `src/echo/entity/state_store.py` — StateStore protocol, state lifetime
  categories, in-memory implementation, and session compatibility view.
- `src/echo/state/sqlite.py` — SQLite persistent-state implementation, schema
  metadata, transactions, and safe tagged-JSON serialization.
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
- `console/src/lib/SignalInspector.svelte` — live/history Signal lists, filters,
  selection detail, and pause/resume controls.
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
- `src/echo/core/inspection.py` — JSON-safe inspection conversion.
- `src/echo/entity/audit.py` — character mutation audit vocabulary.
- `tests/test_signal.py` — Signal unit tests.
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
