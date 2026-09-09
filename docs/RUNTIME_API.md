# Runtime API contract

## Purpose and stability

This is the Phase 3 contract for controlling and inspecting a live Echo
Runtime before network transports are introduced. CLI, Console, tests, and
future HTTP or WebSocket adapters should depend on `RuntimeServiceProtocol` or
call `RuntimeService`; they should not reach into Runtime queues, histories,
registries, broker state, or mutable Entity dictionaries.

The public contract consists of names exported from `echo` and documented in
this file. Private attributes, underscore-prefixed methods, concrete queue
types, history storage, dispatch bookkeeping, and event-broker implementation
are not public API. Phase 3 provides no wire protocol, endpoint, socket,
authentication scheme, or transport status code.

## Conventions

- Service inputs use immutable request/query dataclasses when an operation has
  more than one meaningful input.
- Service reads return typed result or history-snapshot objects. Collections
  are tuples ordered newest first unless an operation says otherwise.
- Returned mappings and event metadata are detached from Runtime-owned mutable
  state.
- Every result intended for a presentation or transport adapter implements
  `to_dict()`. Datetimes become timezone-aware ISO 8601 strings and enums become
  their string values.
- `emit_signal()` and `cancel_task()` are asynchronous. Current read and bounded
  mutation operations are synchronous.
- A limit is a non-negative integer. `0` returns no records and `None` means all
  currently retained records.
- IDs are opaque, non-empty strings. Clients must not infer meaning from them.

Service failures raise a `RuntimeServiceError` subclass. Each exposes
`message`, detached `details`, a stable `code`, and `to_dict()`:

```json
{
  "code": "not_found",
  "message": "entity not found",
  "details": {"entity_id": "missing"}
}
```

Stable service error codes are:

| Code | Meaning |
| --- | --- |
| `invalid_request` | Input shape, filter, limit, or priority is invalid. |
| `not_found` | A retained Entity, Signal, Task, or Action was not found. |
| `state_update_not_allowed` | A state key is outside the service allowlist. |
| `task_not_cancellable` | A Task is terminal or has no live execution. |
| `signal_emission_failed` | A handler failed while processing a Signal. |
| `invalid_character_influence` | State/drive dimensions are not configured. |
| `inference_unavailable` | No configured provider allowed by the mode could serve inference. |
| `runtime_service_error` | An otherwise unclassified service failure. |

Transport adapters may map these errors to their own response mechanisms, but
the mapping is outside this contract and must not leak back into Echo Core.

## Runtime service operations

| Operation | Input | Result | Notes |
| --- | --- | --- | --- |
| `get_runtime_status()` | none | `RuntimeStatusResult` | Runtime ID, idle/active/stopped status, uptime. |
| `get_entities()` | none | `tuple[EntityResult, ...]` | Detached Entity views. |
| `inspect_entity(entity_id)` | ID | `EntityResult` | State, character, active Task IDs, handlers. |
| `get_relationships(entity_id)` | ID | Relationship snapshots | Detached per-person social state. |
| `inspect_relationship(entity_id, subject_id)` | two IDs | `RelationshipState` | One detached per-person context. |
| `emit_signal(request)` | `EmitSignalRequest` | `SignalHistoryEntry` | Returns after routing completes. |
| `get_recent_signals(query)` | `SignalQuery` | Signal snapshots | Filter by type/source and limit. |
| `inspect_signal(signal_id)` | ID | `SignalHistoryEntry` | Includes routing result and linked Tasks. |
| `get_tasks(query)` | `TaskQuery` | Task snapshots | Filter by lifecycle status and limit. |
| `inspect_task(task_id)` | ID | `TaskHistoryEntry` | Detached current lifecycle state. |
| `cancel_task(task_id)` | ID | `TaskHistoryEntry` | Waits until live cancellation cleanup finishes. |
| `get_actions(query)` | `ActionQuery` | Action snapshots | Filter by type, status, Task, or Signal. |
| `inspect_action(action_id)` | ID | `ActionHistoryEntry` | Detached intent/lifecycle view. |
| `get_entity_state(entity_id)` | ID | `StateResult` | Ordinary mutable Entity state only. |
| `set_allowed_state_values(request)` | `SetStateValuesRequest` | `StateResult` | Keys require a constructor-time per-Entity allowlist. |
| `get_logs(query)` | `LogQuery` | `LogResult` | Newest retained observations; filter by severity/event type and related IDs. |
| `subscribe_events(request)` | `RuntimeSubscriptionRequest` | `RuntimeEventSubscription` | Future activity only; see subscriptions below. |
| `get_character_state(entity_id)` | ID | `CharacterStateResult` | Internal controls, drives, attention candidates. |
| `get_attention_candidates(entity_id, limit)` | ID, limit | Attention candidates | Newest retained candidates first. |
| `apply_signal_influence(request)` | `ApplySignalInfluenceRequest` | `SignalInfluenceResult` | Applies bounded deltas linked to a retained Signal. |
| `infer(request)` | `InferenceRequest` | `InferenceResult` | Routes one inference through the optional ProviderRouter. |
| `inspect_providers()` | none | `ProviderInspectionResult` | Health, active route/model, latency, failures, and request attribution. |
| `set_provider_mode(mode)` | mode | `ProviderInspectionResult` | Switches among auto, remote, LAN, and offline policy. |
| `reload_configuration()` | none | `ConfigurationReloadResult` | Explicitly validates, classifies, and transactionally applies the configured file. |
| `inspect_configuration()` | none | `ConfigurationInspectionResult` | Effective fields with secret-safe values and editability metadata. |
| `update_configuration(values)` | dotted-path mapping | `ConfigurationReloadResult` | Validates and applies approved live-safe values only. |

Configuration reload requires a `RuntimeConfigurationManager` attached when
the service is constructed. Results classify each changed dotted path as
`live_safe` or `restart_required`. Any restart-required change rejects the
whole candidate, and validation failures leave the active typed configuration
unchanged. Each outcome is also published as a structured
`configuration.reload` Runtime event.

### Character control boundary

`Entity` owns `InternalState`, immutable `DriveProfile` baselines, bounded drive
activation, and retained `AttentionCandidate` records. Public Entity properties
return immutable or detached views. Character control mutation uses
`RuntimeService.apply_signal_influence()` with a retained Signal ID and a
`SignalInfluence` containing normalized deltas from -1 to 1.

An optional `AttentionProposal` identifies a subject, reason, base salience,
and relevant drives. Active drive contributions affect its final score. The
result is only an attention candidate: applying influence never creates a Task,
goal, intention, or Action.

Phase 4 composes a `RelationshipStore` into each Entity. Relationships are
scoped by subject ID and contain bounded social facts such as familiarity,
trust, interaction count, communication preferences, interests, boundaries,
important memory references, and current context. Service reads return
detached snapshots. Relationship learning and durable persistence remain later
operations; presentation adapters cannot mutate this state directly.

## Developer commands

`DeveloperCommandDispatcher.execute()` accepts one of the immutable command
schemas below and returns `CommandResult`. The dispatcher calls only the
runtime service contract.

| Command name | Schema | Service operation |
| --- | --- | --- |
| `runtime.status` | `RuntimeStatusCommand` | `get_runtime_status` |
| `entity.inspect` | `EntityInspectCommand` | `inspect_entity` |
| `signal.list` | `SignalListCommand` | `get_recent_signals` |
| `signal.inspect` | `SignalInspectCommand` | `inspect_signal` |
| `signal.inject` | `SignalInjectCommand` | `emit_signal` |
| `task.list` | `TaskListCommand` | `get_tasks` |
| `task.inspect` | `TaskInspectCommand` | `inspect_task` |
| `task.cancel` | `TaskCancelCommand` | `cancel_task` |
| `action.list` | `ActionListCommand` | `get_actions` |
| `state.get` | `StateGetCommand` | `get_entity_state` |
| `state.set` | `StateSetCommand` | `set_allowed_state_values` |
| `logs` | `LogsCommand` | `get_logs` |
| `config.reload` | `ConfigReloadCommand` | `reload_configuration` |
| `config.inspect` | `ConfigInspectCommand` | `inspect_configuration` |
| `config.set` | `ConfigSetCommand` | `update_configuration` |

Command failures raise a `DeveloperCommandError` subclass and serialize using
the same `code`, `message`, `details` convention. Stable codes are
`command_parse_error`, `command_validation_error`, `unknown_command`, and
`command_execution_error`. Execution errors contain the serialized service
error under `details.service_error`.

The optional Phase 3 text grammar is intentionally small:

```text
runtime status
entity inspect <entity-id>
signal list
signal inspect <signal-id>
signal inject <type> ['<payload-json-object>']
task list
task inspect <task-id>
task cancel <task-id>
action list
state get <entity-id>
state set <entity-id> '<values-json-object>'
logs
config reload
config inspect
config set <dotted-path> '<json-value>'
```

It uses `shlex.split()` and `json.loads()` only. It does not execute Python,
resolve arbitrary callables, invoke a shell, or import modules. Console code
should construct typed command objects directly rather than round-trip through
text.

## Event subscriptions

`RuntimeSubscriptionRequest` has this schema:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `categories` | iterable of category strings/enums | `logs` | Event families to receive; `logs` selects all. |
| `max_queue_size` | positive integer | `100` | Hard per-subscriber pending-event bound. |
| `backpressure` | `drop_oldest` or `drop_newest` | `drop_oldest` | Overflow behavior. |

Published category values are `signal.received`, `signal.routed`,
`task.lifecycle`, `action.lifecycle`, `state.changed`, `runtime.changed`, and
`error`. `logs` is a subscription selector, not a published event category.

`RuntimeSubscriptionEvent.to_dict()` returns:

```json
{
  "sequence": 12,
  "category": "task.lifecycle",
  "event": {
    "event_type": "task.status_changed",
    "timestamp": "2026-09-08T12:00:00+00:00",
    "entity_id": "bit",
    "signal_id": "signal-id",
    "task_id": "task-id",
    "action_id": null,
    "metadata": {"previous_status": "pending", "status": "running"}
  }
}
```

`sequence` is monotonic in Runtime publication order and may contain gaps when
a subscription filters or drops events. `await subscription.get()` receives
the next event; subscriptions are also asynchronous iterators. `close()` is
idempotent. Reading a closed subscription raises `SubscriptionClosedError`;
subscription error objects follow the standard structured-error shape. Stable
subscription codes are `invalid_subscription` and `subscription_closed`.

Runtime publication uses non-blocking queue offers and never awaits a consumer.
Subscribers do not share mutable event metadata. Slow consumers remain within
bounded, expose `pending_count` and `dropped_count`, and cannot stop Echo.

## Executable example

The complete service, command, subscription, state-write, and character
influence example is
[runtime_api_contract.py](../examples/runtime_api_contract.py).
The full test suite executes it as written:

```console
PYTHONPATH=src python3 examples/runtime_api_contract.py
```
