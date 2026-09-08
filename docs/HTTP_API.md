# HTTP API

## Scope

Phase 4A exposes the existing `RuntimeServiceProtocol` over HTTP. The adapter
translates HTTP inputs into the Phase 3 request/query dataclasses and serializes
their returned snapshots. It does not read from `Runtime`, Entity registries,
queues, histories, or state dictionaries directly.

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
| `GET` | `/entities` | `get_entities()` |
| `GET` | `/entities/{entity_id}` | `inspect_entity()` |
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

List filters use the names in `docs/RUNTIME_API.md`. Action type is the `type`
query parameter; Task and Action lifecycle filters use `status`. Limits default
to the underlying service-query defaults and may be zero.

Signal injection accepts `type`, optional `id`, `source`, `timestamp`,
`payload`, `metadata`, and `priority`. State writes accept an object shaped as
`{"values": {...}}` and remain constrained by the service's per-Entity
allowlist.

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
state updates to `403`, non-cancellable Tasks to `409`, failed Signal handling
and invalid character influence to `422`, and unclassified service errors to
`500`.

WebSockets, event-stream transport, authentication, process hosting, the
Svelte Console, and relationship APIs are not part of Phase 4A.
