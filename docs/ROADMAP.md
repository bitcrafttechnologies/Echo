# Roadmap

The authoritative roadmap is `Echo_Plan.md`, with character details in
`CHARACTER_ARCHITECTURE.md`. Work is delivered incrementally. The requested
character amendment establishes only base types, configuration, and prompts;
runtime integration still follows the phases below.

## Phase 0 — repository cleanup and architecture docs

- Completed on branch `0.1`.
- Established the Python package and test layout.
- Recorded architecture, decisions, roadmap, and current state.
- Excluded local model artifacts and generated files from version control.

## Phase 1 — kernel

- Phase 1A (`0.1.1`): Signal — completed.
- Phase 1B (`0.1.2`): Entity and handler registry — completed.
- Phase 1C (`0.1.3`): Task and Action — completed.
- Phase 1D (`0.1.4`): Scheduler and Runtime — completed.
- Phase 1E (`0.1.5`): integration tests — completed.

- Phase 1F (`0.4-tui_core`): Echo TUI Core — initial implementation completed.
- Added the first-party ANSI terminal interface, `echo` entry point, tmux
  workspace manager, nvim/vi handoff, local/HTTP management clients, and a
  serializable console-surface registry.
- Mirrored the implemented web surfaces: Overview, Chat, Signals, Tasks,
  Entity/character/relationships, and Logs.
- Kept Chat on the `UserMessage` Signal path and commands on the existing safe
  developer-command/control API.
- Shared configuration parsing and startup validation arrived in Phase 6A.
  Phase 6B adds explicit reload through the shared management plane; the TUI
  has no private apply path.

Phase 1F is a standing Core interface track. Every operator-visible phase must
update the TUI and web UI together in small, relevant, tested chunks.

## Phase 2 — observability

- Phase 2A (`0.2.1`): structured runtime logging — completed.
- Phase 2B (`0.2.2`): bounded in-memory Signal history — completed.
- Phase 2C (`0.2.3`): bounded Task and Action history — completed.
- Phase 2D (`0.2.4`): runtime snapshot and inspection — completed.
- Phase 2E (`0.2.5`): observability integration and cleanup — completed.
- Added typed log records, a replaceable sink interface, and chronological
  in-memory storage.
- Instrumented Signal routing, Task and Action lifecycle, Entity state changes,
  Runtime lifecycle, and handler errors.
- Added safe Signal snapshots, routing results, latest and filtered queries,
  and predictable oldest-first eviction.
- Added reference-backed Task lifecycle snapshots and Action creation/execution
  history with independently configurable bounds.
- Added a detached JSON-safe view of runtime status, uptime, Entities, active
  work, queues, recent activity, errors, scheduler state, and handlers.
- Composed Phase 2 identity, traits, and self-model into the public Entity and
  added character mutation audit vocabulary.
- Verified one Signal-to-state causal chain across routing, Task, Action, logs,
  histories, and runtime inspection with exact ID linkage and no duplicate
  lifecycle events.

Phase 2 stops here. Phase 3 requires separate authorization.

## Phase 3 — runtime API

- Phase 3A (`0.3.1`): internal runtime service API — completed.
- Added a transport-agnostic `RuntimeService` for status, Entities, Signals,
  Tasks, Actions, Entity state, allowlisted state updates, and logs.
- Added structured request/query/result objects and clean domain errors.
- Added deterministic cancellation of live handler Tasks.
- Exercised every service operation against a live Runtime.
- Phase 3B (`0.3.2`): structured developer commands — completed.
- Added typed command schemas and an explicit dispatcher over `RuntimeService`.
- Added a minimal `shlex` and JSON text grammar suitable for a future CLI while
  keeping direct command objects suitable for a future Console.
- Added structured command errors and arbitrary-Python execution rejection.
- Phase 3C (`0.3.3`): runtime event subscriptions — completed.
- Added ordered event envelopes for Signal, Task, Action, state, Runtime, log,
  and error activity.
- Added transport-neutral per-subscriber bounded queues, category filters,
  explicit overflow policies, and drop accounting.
- Verified ordered delivery and isolation of slow and failed consumers.
- Phase 3D (`0.3.4`): runtime API contract and Phase 3 character completion —
  completed.
- Documented stable service operations, commands, subscriptions, results,
  errors, and public/private boundaries in `RUNTIME_API.md`.
- Added `RuntimeServiceProtocol` and an executable contract example.
- Composed internal state, drives, explicit Signal influence, and attention
  candidates into Entity without automatic Actions.

Phase 3 stops after Phase 3D. Phase 4 requires separate authorization.
WebSockets, FastAPI, the Console, and the CLI executable are not part of Phase
3D.

## Phase 4 — Echo Web Console

- Phase 4A (`0.4.1`): FastAPI adapter — completed.
- Added an optional FastAPI dependency and an app factory around
  `RuntimeServiceProtocol`.
- Added HTTP health, Runtime status, Entity, Signal, Task, Action, state, and
  log endpoints with structured service-error responses.
- Verified endpoint responses and that Echo Core still runs when FastAPI is
  unavailable.
- Phase 4B (`0.4.2`): WebSocket event streaming — completed.
- Added a `/events` endpoint backed exclusively by Phase 3 bounded event
  subscriptions, with category and backpressure query options.
- Verified structured Signal, Task, and Action delivery; disconnect cleanup;
  reconnect behavior; filtering; and slow-client isolation.
- Phase 4C (`0.4.3`): initial Echo Console frontend shell — completed.
- Added a lightweight SvelteKit interface with Runtime status, API and event
  connection state, responsive navigation, a main content area, and recent
  live activity.
- Connected through the Phase 4A HTTP and Phase 4B WebSocket APIs with
  retry/offline behavior and intentionally deferred inspector placeholders.
- Phase 4D (`0.4.4`): Signal Inspector UI — completed.
- Added pauseable live Signal arrivals, retained history, type/source filters,
  and a structured selection detail view.
- Added Signal identity, payload, metadata, routing outcome, and related
  Task/Action IDs using existing HTTP and WebSocket APIs; replay remains
  deferred.
- Phase 4E (`0.4.5`): remaining first-pass Console views — completed.
- Added active/history Task inspection, hierarchy, and confirmed live
  cancellation; Entity state, handlers, active Tasks, and separated character
  inspection; and structured log severity/event-type filters.
- Added Chat as `UserMessage` Signal injection only, with responses sourced
  from associated Runtime Actions rather than a parallel chat path.
- Completed the Phase 4 character slice with Entity-owned, detached per-person
  relationship context and service/HTTP/Console inspection.

Phase 4 web work stops after Phase 4E. The cross-cutting Phase 1F TUI track was
then established on `0.4-tui_core`. Phase 5 requires separate authorization.

## Phase 5 — provider system

- Phase 5A (`0.5.1`): intelligence provider interface — completed.
- Added asynchronous inference and health contracts, provider metadata, and
  structured request/result/timing vocabulary.
- Added optional capability protocols for future embedding and classification
  without requiring those operations from every provider.
- Added a deterministic `MockProvider`; Echo Core still starts and runs with
  no provider configured.

Phase 5A stopped at the provider contract; routing began only with separate
Phase 5B authorization.

- Phase 5B (`0.5.2`): centralized `ProviderRouter` — completed.
- Added `auto`, `remote`, `lan`, and `offline` routing modes. Automatic routing
  makes one ordered OpenRouter/remote → LAN → offline pass; explicit modes
  never cross their selected boundary.
- Added selected-provider, attempt, per-attempt latency/error, total latency,
  and structured health/status inspection.
- Added structured `InferenceUnavailableError` exhaustion results and coverage
  for every mocked availability permutation.

Phase 5B stopped at generic routing; concrete providers began only with
separate Phase 5C authorization.

- Phase 5C (`0.5.3`): OpenRouter provider — completed.
- Added an environment/config-driven `OpenRouterProvider` using the
  OpenAI-compatible chat-completions API and authenticated key health check.
- Added configurable model selection, normalized Echo results, actual served
  model and token-usage metadata, structured API/network/configuration/response
  failures, timing, and secret-safe logging.
- Verified the provider performs no internal retry or cross-provider fallback;
  `ProviderRouter` remains the only fallback owner.

Phase 5C stopped at the remote provider; LAN work began only with separate
Phase 5D authorization.

- Phase 5D (`0.5.4`): LAN inference provider — completed.
- Added explicit URL, model, timeout, and optional credential configuration for
  llama.cpp and equivalent OpenAI-compatible network servers.
- Added `/v1/chat/completions` inference, `/v1/health` readiness checks,
  normalized Echo results, structured errors, and secret-safe timing logs.
- Added mocked coverage for URL/environment aliases, ready/loading health,
  timeouts and failures, no discovery, and router-owned offline fallback.

Phase 5D stopped at LAN inference; offline work began only with separate Phase
5E authorization.

- Phase 5E (`0.5.5`): lazy offline inference provider — completed.
- Added explicit model-path and llama-server configuration, an inspectable
  unloaded/loading/loaded/unloading/error lifecycle, and explicit unload.
- Added a clean local-backend protocol plus a real loopback `llama-server`
  subprocess adapter with bounded startup, request, and shutdown timeouts.
- Verified remote success and health inspection never load the local model;
  only a router-selected offline inference initializes it.
- Added structured initialization/execution/unload failures and mocked lazy
  load, concurrency, state, unload, and final-fallback coverage.

Phase 5E stopped at the offline provider; integration and operator surfaces
began only with separate Phase 5F authorization.

- Phase 5F (`0.5.6`): provider observability and fallback integration — completed.
- Added optional RuntimeService inference/router ownership, all-provider health,
  bounded per-request serving-provider history, recent failures, and structured
  no-provider/exhaustion behavior.
- Added shared HTTP, developer-command, web Console, TUI, and tmux inspection
  and switching across `auto`, `remote`, `lan`, and `offline`.
- Verified OpenRouter success, OpenRouter failure to LAN, LAN failure to lazy
  offline, restored OpenRouter preference, and provider attribution per request.
- Completed the Phase 5 character slice with distinct Entity-owned working,
  episodic, semantic, preference, and relationship memory records plus
  provider-continuity tests.

Phase 5 stops here.

## Phase 6 — configuration

- Phase 6A (`0.6.1`): typed configuration system — completed.
- Added one strict TOML schema for Runtime startup, logging, history limits,
  provider routing, OpenRouter, LAN, offline, API server, and Console
  connectivity.
- Added centralized allowlisted environment overrides, startup validation with
  aggregated dotted-path errors, and explicit Runtime/provider factories.
- Added CLI startup validation, `echoc config validate`, and a secret-free
  example configuration.
- Phase 6B (`0.6.2`): safe runtime configuration reload — completed.
- Added explicit, transactional reload with typed live-safe versus
  restart-required classification and structured audit events.
- Live-safe logging, provider mode/preference, and bounded history changes apply
  in place. Invalid or restart-required candidates leave active settings
  unchanged and report their dotted paths.
- Added the shared Runtime service, HTTP, developer-command, web Console, and
  TUI reload operation.
- Phase 6C (`0.6.3`): configuration inspection/control — completed.
- Added secret-safe effective configuration inspection, per-field
  live-editable/restart-required metadata, validated dotted-path updates, clean
  validation errors, and matching web Console/TUI surfaces.
- Completed the Phase 6 character slice with normalized importance scoring,
  selective retention, evidence/confidence/scope/contradiction validation,
  semantic/preference/relationship consolidation, and decision audit records.
- Added a non-interactive root installer and a configuration-preserving root
  launcher for Core, Web Console, terminal Console, or both interfaces.

## Phase 7 — state persistence and development restart

- Phase 7A (`0.7.1`): StateStore abstraction — completed.
- Phase 7B (`0.7.2`): SQLite Entity persistent state — completed.
- Phase 7C (`0.7.3`): graceful restart workflow — completed.
- Phase 7D (`0.7.4`): deliberate Console/API restart controls — completed.
- Completed the Phase 7 character slice with bounded provider-neutral context
  assembly and deterministic relevant retrieval across Entity character data.

### Phase 7E — durable semantic and character memory

Phase 7E is a required continuity gate before Phase 8. It extends, rather than
replaces, the typed memory, retention, consolidation, SQLite, and Character
Context work already completed in Phases 5–7.

- Echo, never an inference provider, owns the canonical memory store and commit
  policy.
- Durable records are Entity-scoped and distinguish semantic knowledge from
  conservative character development. Authored Entity seed files remain
  immutable baselines.
- Candidate decisions explicitly report committed, merged, rejected, or
  deferred outcomes. Active, superseded, and archived durable records remain
  inspectable, and corrections preserve prior provenance.
- Provenance is typed and enforced by Core. Only trusted experience or
  observation Signals may create direct-experience provenance; model output
  cannot claim it.
- SQLite uses dedicated memory tables in the configured local database rather
  than hiding memory inside ordinary persistent-state keys. Production startup
  must actually select that store.
- Cross-session consolidation retains concise evidence references or candidate
  records, not complete transcripts.
- Retrieval remains provider-neutral, lexical, hard-bounded, and ranked by
  relevance, importance, recency, type, and Entity scope. Embeddings are not a
  Core requirement.
- Persistence failures are reported through Runtime logging while a successful
  conversational response remains usable.
- Acceptance requires a genuinely fresh Runtime/store instance with no replayed
  transcript, plus Entity-isolation and provider-swap coverage.

Phase 7E does not add intentions, behavior policy, reflection-driven trait
mutation, autonomous forgetting, a vector database, or a transcript archive.
Phase 8A's durable Signal format, Phase 8B's live Runtime recording controls,
Phase 8C's one-Signal, sequential, and step replay, and Phase 8D's realtime,
accelerated, immediate, manual, and cancellable timing are complete. Behavior
policy work and Phase 8E Console replay controls are also complete.

### Phase 8E — Console replay controls

- Added host-local recording-path selection to Signal Inspector using the
  existing replay API; no second browser-side storage model was introduced.
- Added one-Signal replay from the selected Signal, sequential start/stop,
  timing and acceleration controls, manual advance, session identity, and live
  progress.
- Replayed entries are visibly labeled in live/history lists and detail retains
  the original recorded ID and timestamp.
- Completed the Phase 8 character slice with JSON-safe typed intentions,
  Core-owned behavior arbitration, explicit external-Action authorization,
  interruptibility checks, and bounded low-priority curiosity goals. Policy
  approval creates an Action intent but never executes or records it implicitly.

## Deferred

Remaining runtime APIs, concrete model providers, advanced autonomous memory,
concrete Medulla transports, discovery, trust and capability routing,
edge ML, robotics, and advanced task behavior remain deferred to the phases
defined in the plan. Phase 9A's transport abstraction, Phase 9B's local
reference transport, Phase 9C's WebSocket transport, Phase 9D's optional MQTT
transport, Phase 9E's serial transport, Phase 9F's capability contract, Phase
9G's registry/router, and Phase 9H's remote-node protocol are complete.

## Phase 9 Medulla track

- Phase 9A (`0.9.1`): protocol-neutral transport lifecycle, receive/execute
  boundary, safe validation, structured errors, and status/health — completed.
- Phase 9B (`0.9.2`): bounded same-process inbound/outbound queues, explicit
  overflow policy, clean shutdown, and queue health/status — completed.
- `0.9.2-package_prep`: reusable Medulla supervision, explicit Action
  dispatch, one-Entity package composition, distribution metadata, and a
  clean installed-wheel example — completed on the package-preparation branch.
- Phase 9C (`0.9.3`): versioned safe JSON WebSocket protocol, reconnectable
  client transport, authentication hook, and connection health — completed.
- Phase 9D (`0.9.4`): optional MQTT transport, explicit broker configuration,
  provisional Signal/Action/status topics, reconnects, and health — completed.
- Phase 9E (`0.9.5`): optional serial transport, embedded-friendly versioned
  CRC framing, noise isolation, reconnects, and health — completed.
- Phase 9F (`0.9-medulla_node-0.1`): versioned, transport-neutral capability
  and provider descriptions, schemas, availability, permissions, risk/effect,
  timeout expectations, and deterministic collision handling — completed.
- Phase 9G (`0.9-medulla_node-0.2`): provider-owned inventory and health,
  capability removal and introspection, permission-gated deterministic routing,
  timeout enforcement, and structured failure containment — completed.
- Phase 9H (`0.9-medulla_node-0.3`): closed versioned remote-node manifests,
  capability/Signal/resource mapping, health and pairing metadata, lifecycle
  registration, disconnect disablement, and reconnect restoration — completed.
- Later Phase 9 subphases: additional network/device transports,
  capability and Signal-handler discovery, registration and routing, node
  communication, permissions, pairing, authorization, and trust — deferred.
- Phase 9F begins the Medulla node iteration branch series; subsequent phases
  increment its final component.

## Persistent character vertical track

- Phase 2: persistent identity, trait snapshots, self-model, and audit terms —
  completed in memory; durable persistence remains deferred.
- Phase 3: internal state, drives, Signal influence, and attention candidates —
  completed in memory; durable persistence remains deferred.
- Phase 4: relationship models and per-person social context — completed in
  memory; durable persistence and learning remain deferred.
- Phase 5: distinct working, episodic, semantic, preference, and relationship
  memories — completed in memory; persistence remains deferred.
- Phase 6: retention scoring, consolidation, and learned preferences —
  completed in memory; durable persistence remains deferred.
- Phase 7: compact, provider-neutral Character Context Builder and retrieval —
  completed; Phase 7E adds durable semantic and character-development memory.
- Phase 8: typed intentions, behavior arbitration, interruptibility, and
  low-priority curiosity goals — completed.
- Phase 9: typed reflection evidence, bounded trait updates, accepted/rejected
  audit records, and graceful-restart continuity — completed. Medulla remains
  separate and has no character-mutation authority.
- Phase 10 and later: validated embodiment Signals constrain state, goals, and
  behavior after crossing the Medulla boundary.

Provider and embodiment continuity tests are acceptance criteria throughout,
not a final integration exercise.
