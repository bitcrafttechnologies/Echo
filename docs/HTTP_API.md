# HTTP API

## Scope

Phase 4A exposes the existing `RuntimeServiceProtocol` over HTTP, and Phase 4B
adds live WebSocket event streaming. The adapter translates transport inputs
into the Phase 3 request/query dataclasses and serializes their returned
snapshots. It does not read from `Runtime`, Entity registries, queues,
histories, or state dictionaries directly.

The adapter is optional:

```console
python -m pip install -e '.[api]'
```

Importing `echo` does not import FastAPI. Applications that install the extra
create an ASGI app explicitly:

```python
from echo.adapters.fastapi import create_app

app = create_app(runtime_service)
```

## Endpoints

| Method | Path | Runtime service operation |
| --- | --- | --- |
| `GET` | `/health` | `get_runtime_status()` readiness check |
| `GET` | `/runtime/status` | `get_runtime_status()` |
| `POST` | `/runtime/restart` | `request_restart()` |
| `GET` | `/runtime/restart/{operation_id}` | `get_restart_operation()` |
| `GET` | `/configuration` | `inspect_configuration()` |
| `PATCH` | `/configuration` | `update_configuration()` |
| `GET` | `/providers` | `inspect_providers()` |
| `PATCH` | `/providers/mode` | `set_provider_mode()` |
| `POST` | `/inference` | `infer()` |
| `POST` | `/configuration/reload` | `reload_configuration()` |
| `GET` | `/entities` | `get_entities()` |
| `GET` | `/entities/{entity_id}` | `inspect_entity()` |
| `GET` | `/entities/{entity_id}/relationships` | `get_relationships()` |
| `GET` | `/entities/{entity_id}/relationships/{subject_id}` | `inspect_relationship()` |
| `GET` | `/signals` | `get_recent_signals()` |
| `POST` | `/signals` | `emit_signal()` |
| `GET` | `/signals/{signal_id}` | `inspect_signal()` |
| `GET` | `/tasks` | `get_tasks()` |
| `GET` | `/tasks/{task_id}` | `inspect_task()` |
| `POST` | `/tasks/{task_id}/cancel` | `cancel_task()` |
| `GET` | `/actions` | `get_actions()` |
| `GET` | `/actions/{action_id}` | `inspect_action()` |
| `GET` | `/entities/{entity_id}/state` | `get_entity_state()` |
| `PATCH` | `/entities/{entity_id}/state` | `set_allowed_state_values()` |
| `GET` | `/logs` | `get_logs()` |
| `WS` | `/events` | `subscribe_events()` |

List filters use the names in `docs/RUNTIME_API.md`. Action type is the `type`
query parameter; Task and Action lifecycle filters use `status`. Log queries
accept `severity` and `event_type`. Limits default to the underlying
service-query defaults and may be zero.

Console Chat uses the existing `POST /signals` endpoint with type
`UserMessage` and source `console`. There is deliberately no chat response
endpoint: handlers, Tasks, and Actions follow the ordinary Runtime path.

Signal injection accepts `type`, optional `id`, `source`, `timestamp`,
`payload`, `metadata`, and `priority`. State writes accept an object shaped as
`{"values": {...}}` and remain constrained by the service's per-Entity
allowlist.

Provider mode writes accept `{"mode":"auto"}`, with `remote`, `lan`, and
`offline` as the other allowed values. Inference accepts a required `prompt`
plus optional structured `context`, `parameters`, `metadata`, and `request_id`. Provider inspection
returns configured provider metadata, health, active provider/model, latest
latency, recent failures, and bounded per-request serving-provider history.

`POST /configuration/reload` accepts no path or body. It reloads the file owned
by the host's `RuntimeConfigurationManager`, preventing remote clients from
selecting arbitrary server files. The result lists every changed dotted path,
its `live_safe` or `restart_required` classification, previous/requested
secret-safe values, and whether anything was applied. If any changed setting
requires restart, the operation applies none of the candidate. Invalid TOML or
schema values also preserve the active configuration.

`GET /configuration` returns the active effective typed fields and labels each
as `live_editable` or `restart_required`. Secret fields contain only
`secret: true`, whether they are configured, and a null value; API keys never
cross the service boundary. `PATCH /configuration` accepts
`{"values":{"providers.mode":"lan"}}` using dotted field paths. Only fields
classified live-editable are accepted, and the complete candidate is validated
before application. Validation issues retain their dotted paths under
`error.details.issues`.

`POST /runtime/restart` is an explicit development control. Its body must
contain a non-empty reason, `"confirmation":"RESTART"`, and
`"preserve_state":true`; missing or different values are rejected. A `202`
response returns an operation ID, Task settlement policy, preservation flag,
old/new Runtime IDs, and the current phase. Clients poll
`GET /runtime/restart/{operation_id}` through `quiescing`, `settling_tasks`,
`persisting_state`, `closing_resources`, `starting`, and a terminal status.
On success, existing event sockets close with WebSocket code `1012` so clients
reconnect and subscribe to the fresh Runtime generation.

## WebSocket event stream

`/events` sends future `RuntimeSubscriptionEvent.to_dict()` values as JSON. A
message contains its monotonic sequence, category, and structured Runtime log
event. Reconnecting creates a new future-only subscription; retained history
remains available through the HTTP inspection endpoints.

Optional query parameters map directly to `RuntimeSubscriptionRequest`:

| Parameter | Default | Meaning |
| --- | --- | --- |
| repeated `category` | `logs` | One or more Phase 3 event categories. |
| `max_queue_size` | `100` | Positive per-client pending-event bound. |
| `backpressure` | `drop_oldest` | `drop_oldest` or `drop_newest`. |

For example, `/events?category=signal.received&category=task.lifecycle` streams
only Signal receipt and Task lifecycle events. Each client owns an isolated,
bounded Phase 3 subscription. Runtime publication never waits for network I/O;
a slow client drops events according to its requested policy. Disconnecting
always closes and removes that client's subscription.

## Durable memory

- `GET /entities/{entity_id}/memories` lists inspectable durable records. The
  optional `status` and `type` query fields filter lifecycle state and semantic
  versus character-development memory.
- `GET /entities/{entity_id}/memories/{memory_id}` returns one Entity-owned
  record, including provenance, confidence, importance, source references,
  timestamps, access data, and supersession links.
- `POST /entities/{entity_id}/memories/{memory_id}/archive` deactivates a record
  while preserving its audit history.
- `DELETE /entities/{entity_id}/memories/{memory_id}` explicitly removes a
  record.

Provider adapters and Console clients never access SQLite directly.

## Errors

Request-schema errors use FastAPI's standard `422` response. Runtime service
errors keep their stable Phase 3 body under an `error` key:

```json
{
  "error": {
    "code": "not_found",
    "message": "entity not found",
    "details": {"entity_id": "missing"}
  }
}
```

The adapter maps invalid requests to `400`, missing resources to `404`, denied
state updates to `403`, non-cancellable Tasks to `409`, failed Signal handling,
invalid character influence, and configuration validation to `422`,
unavailable inference or reload support to `503`, and unclassified service
errors to `500`.

Authentication and process hosting remain outside this adapter.
