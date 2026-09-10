# Echo Developer Guide

Echo is a lightweight, persistent, event-driven runtime for building intelligent entities.

Instead of treating an AI application as a sequence of isolated model calls, Echo gives the entity a stable identity, structured state, memory, event handlers, observable work, and configurable access to remote, LAN, or offline inference.

Echo owns the entity’s continuity. Models provide inference; they do not own identity, memory, or runtime state.

> Current implementation: `0.7.7`  
> Requires Python 3.11 or later.

## What you build with Echo

A developer defines one or more **Entities** and registers asynchronous handlers for the **Signals** they can receive.

When a signal arrives, Echo:

1. Schedules it by priority.
2. Finds matching entity handlers.
3. creates a tracked **Task** for each handler.
4. Runs the handlers.
5. Records any resulting **Actions**.
6. Publishes structured lifecycle events.
7. Retains bounded histories for inspection.

```text
Signal → Scheduler → Entity Handler → Task → Action
```

An Action represents an entity’s intent. Echo records and exposes that intent, but the current kernel does not execute external side effects automatically. Your application decides how actions such as sending a message, controlling a device, or calling another service are performed.

## Quick start

Clone the repository and install Echo from its root directory:

```console
./install.sh
```

The installer creates a Python virtual environment, installs Echo and its API dependencies, installs the Web Console packages, and builds the Console.

Start Echo Core:

```console
./start.sh
```

Verify that the runtime is available:

```console
curl http://127.0.0.1:8000/health
```

```json
{
  "status": "ok"
}
```

To start Echo with an operator interface:

```console
./start.sh --web
./start.sh --terminal
./start.sh --web --terminal
```

The Web Console is normally available at `http://127.0.0.1:5173`. The terminal interface can also be opened separately:

```console
source .venv/bin/activate
echoc console
```

## Create an entity

An Entity is a persistent actor with an ID, state, handlers, character data, relationships, and memory.

The smallest useful Echo application looks like this:

```python
import asyncio

from echo import Entity, Runtime, Signal


async def main() -> None:
    assistant = Entity(
        "assistant",
        state={"mode": "idle"},
    )
    runtime = Runtime([assistant])

    @assistant.on("message.received")
    async def handle_message(signal: Signal):
        assistant.state["mode"] = "responding"

        return await assistant.action(
            "message.reply",
            text=f"You said: {signal.payload['text']}",
        )

    await runtime.emit(
        Signal(
            type="message.received",
            source="example",
            payload={"text": "Hello, Echo."},
        )
    )

    print(runtime.inspect())
    runtime.stop()


asyncio.run(main())
```

`Runtime.emit()` returns after the signal and all matching handlers have been processed. Every matching handler runs as a separately tracked Task.

Handlers can:

- Read or update entity state.
- Create Actions.
- Emit additional Signals.
- Add or query entity memory.
- Interact with application services.
- Request inference through a configured provider router.

## Work with state

Echo separates ordinary state into three lifetimes:

| Category | Lifetime |
| --- | --- |
| `ephemeral` | Temporary process-local state |
| `session` | State for the current runtime session |
| `persistent` | State restored across configured restarts |

The compatibility mapping `entity.state` represents session state:

```python
entity.state["mode"] = "active"
```

Use the category-aware API when state lifetime matters:

```python
from echo import StateCategory

entity.set_state(
    "preferred_temperature",
    21,
    category=StateCategory.PERSISTENT,
)

temperature = entity.get_state(
    "preferred_temperature",
    category=StateCategory.PERSISTENT,
)

persistent_state = entity.list_state(
    category=StateCategory.PERSISTENT,
)
```

The default state store is in memory. The configured Echo host uses SQLite when persistence is enabled.

```toml
[persistence]
enabled = true
database_path = ".echo/echo.sqlite3"
```

Session and ephemeral state remain process-local. Only persistent state is restored into a fresh runtime generation.

## Add intelligence providers

Inference is optional. Echo Core can run without an LLM or model server.

When inference is enabled, a `ProviderRouter` owns provider selection and fallback. The supported slots are:

1. OpenRouter
2. A LAN-hosted OpenAI-compatible server
3. A lazy offline `llama-server` process

In `auto` mode, Echo attempts configured providers in preference order. Each provider receives one attempt; providers do not perform their own retries or fallback.

Start with the example configuration:

```console
cp echo.example.toml echo.toml
echoc config validate echo.toml
```

### OpenRouter

```toml
[providers]
mode = "auto"
preference = ["remote", "lan", "offline"]

[providers.openrouter]
enabled = true
model = "openrouter/auto"
base_url = "https://openrouter.ai/api/v1"
timeout_seconds = 60
```

Provide the credential through the environment:

```console
export OPENROUTER_API_KEY="your-key"
```

### LAN inference

Echo can connect to llama.cpp or another OpenAI-compatible server:

```toml
[providers.lan]
enabled = true
base_url = "http://echo-model-server.local:8080"
model = "local-model"
timeout_seconds = 30
```

Echo does not discover LAN servers automatically. The server address must be configured explicitly.

### Offline inference

```toml
[providers.offline]
enabled = true
model_path = "models/model.gguf"
executable = "llama-server"
request_timeout_seconds = 120
startup_timeout_seconds = 120
shutdown_timeout_seconds = 10
port = 0
```

The offline model is loaded only when routing reaches it. A successful remote or LAN request leaves the model unloaded.

## Request inference

Applications should send provider-neutral requests through `RuntimeService`:

```python
from echo import (
    Entity,
    InferenceRequest,
    Runtime,
    RuntimeService,
    load_config,
)

config = load_config("echo.toml")

entity = Entity("assistant")
runtime = config.create_runtime([entity])
router = config.create_provider_router()
service = RuntimeService(runtime, provider_router=router)

result = await service.infer(
    InferenceRequest(
        prompt="Summarize the latest sensor reading.",
        context={
            "sensor": "temperature",
            "value": 21.4,
            "unit": "celsius",
        },
    )
)

print(result.output)
print(result.provider.provider_id)
print(result.timing.duration_ms)
```

Inference results identify the provider and model that actually served the request. Routing history also retains failed attempts, latency, and terminal errors for inspection.

## Give an entity character

Echo’s character model is provider-independent. An Entity can own:

- Immutable identity
- Traits
- A self-model
- Internal control state
- Drive baselines and activation
- Attention candidates
- Per-person relationship state
- Working, episodic, semantic, preference, and relationship memories
- Durable semantic and character-development memory

The configured host loads the included `bit` reference entity from `entities/bit/`. Its seed files define identity, traits, drives, policies, and model-facing character guidance.

A custom embedded entity can be assembled directly:

```python
from echo import (
    DriveProfile,
    Entity,
    EntityIdentity,
    TraitProfile,
)

entity = Entity(
    "guide",
    identity=EntityIdentity(
        entity_id="guide",
        name="Guide",
        entity_type="assistant",
        presentation="calm and concise",
        worldview="Clarity should precede action.",
        core_values=("honesty", "curiosity", "care"),
    ),
    traits=TraitProfile({
        "curiosity": 0.8,
        "patience": 0.9,
    }),
    drives=DriveProfile({
        "understanding": 0.9,
        "helpfulness": 0.8,
    }),
)
```

Before model inference, `CharacterContextBuilder` can assemble a bounded snapshot of the entity’s relevant character, state, relationships, attention, and memory. This keeps prompts focused and prevents providers from becoming the canonical memory layer.

## Use Echo as a service

`RuntimeServiceProtocol` is Echo’s stable application boundary. Embedded applications can call it directly, while networked applications can use the optional FastAPI adapter.

```python
from echo import RuntimeService
from echo.adapters.fastapi import create_app

service = RuntimeService(runtime, provider_router=router)
app = create_app(service)
```

Run the ASGI application with Uvicorn:

```console
uvicorn your_module:app --host 127.0.0.1 --port 8000
```

Common endpoints include:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/runtime/status` | Inspect runtime health and uptime |
| `POST` | `/runtime/recording` | Start Signal session recording |
| `GET` | `/runtime/recording` | Inspect recording status and metadata |
| `DELETE` | `/runtime/recording` | Stop recording after draining writes |
| `POST` | `/runtime/replays` | Start one, sequential, or step Signal replay |
| `POST` | `/runtime/replays/{id}/step` | Advance a step replay by one Signal |
| `GET` | `/runtime/replays/{id}` | Inspect replay progress and safety policy |
| `GET` | `/entities` | List entities |
| `GET` | `/entities/{id}` | Inspect an entity |
| `POST` | `/signals` | Emit a signal |
| `GET` | `/signals` | Query retained signals |
| `GET` | `/tasks` | Query handler tasks |
| `POST` | `/tasks/{id}/cancel` | Cancel active work |
| `GET` | `/actions` | Query recorded actions |
| `GET` | `/logs` | Read structured runtime logs |
| `POST` | `/inference` | Route an inference request |
| `GET` | `/providers` | Inspect provider health and history |
| `PATCH` | `/providers/mode` | Change routing mode |
| `GET` | `/configuration` | Inspect effective configuration |
| `POST` | `/configuration/reload` | Apply eligible configuration changes |
| `WS` | `/events` | Subscribe to future runtime events |

Emit a signal over HTTP:

```console
curl -X POST http://127.0.0.1:8000/signals \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "message.received",
    "source": "my-application",
    "payload": {
      "text": "Hello"
    }
  }'
```

The one-shot terminal client exposes the same recording operations:

```console
echoc record start .echo/development-session.jsonl '{"label":"debug"}'
echoc record status
echoc record stop
```

Recording writes run outside Signal dispatch. Status reports the session and
output metadata plus enqueued, written, dropped, and failed-write information.

Replay uses the same `Runtime.emit()` path as live Signals. Replayed Signals
receive a fresh ID and current timestamp while retaining their original ID and
timestamp under `metadata.echo_replay`. Phase 8C permits only the `record_only`
safety policy and never invokes external Action execution:

```console
echoc replay signal .echo/development-session.jsonl <signal-id>
echoc replay session .echo/development-session.jsonl
echoc replay step .echo/development-session.jsonl
echoc replay next <replay-id>
echoc replay status <replay-id>
```

## Subscribe to runtime events

Embedded applications can consume events without running an HTTP server:

```python
from echo import (
    RuntimeEventCategory,
    RuntimeSubscriptionRequest,
)

subscription = service.subscribe_events(
    RuntimeSubscriptionRequest(
        categories=(
            RuntimeEventCategory.SIGNAL_RECEIVED,
            RuntimeEventCategory.TASK_LIFECYCLE,
            RuntimeEventCategory.ACTION_LIFECYCLE,
        ),
        max_queue_size=100,
        backpressure="drop_oldest",
    )
)

async for event in subscription:
    print(event.to_dict())
```

Subscriptions receive future activity only. Retained signal, task, action, and log histories remain available through service queries.

Each subscriber has an isolated bounded queue. Slow consumers cannot block the runtime; overflow follows the subscription’s `drop_oldest` or `drop_newest` policy.

## Inspect and operate Echo

The `echoc` command communicates through the same management boundary used by the Web and terminal consoles.

```console
echoc status
echoc entities
echoc signals
echoc tasks
echoc logs
echoc entity inspect bit
echoc signal inject system.ping '{"value": 1}'
echoc provider status
echoc provider mode lan
```

For noninteractive monitoring:

```console
echoc console --snapshot --surface overview
echoc console --snapshot --surface providers
echoc console --snapshot --surface logs
```

The available operator surfaces cover runtime status, chat, signals, tasks, entities, character state, relationships, memories, providers, configuration, and logs.

## Reload configuration safely

Echo validates the complete merged configuration before constructing runtime or provider resources.

Validate a file without starting Echo:

```console
echoc config validate echo.toml
```

After editing the active configuration:

```console
echoc config reload
```

Changes fall into two categories:

| Change class | Behavior |
| --- | --- |
| Live-safe | Applied without replacing the runtime |
| Restart-required | Reported without applying any part of the candidate |

Live-safe settings include logging, routing policy, provider preference, routing-history size, and practical runtime-history limits.

Provider construction, credentials, API binding, runtime startup, persistence, and Console connectivity require a restart. A candidate containing any restart-required change is rejected atomically, leaving the active configuration untouched.

Credentials are represented only as configured or not configured. API keys are never returned through the service or Console.

## Persist and manage memory

When persistence is enabled, each Entity can own durable semantic and character-development memory in SQLite.

Durable records contain:

- Provenance and source references
- Confidence and importance
- Active, superseded, or archived status
- Creation and access timestamps
- Version and supersession links

Applications interact with memory through the Entity or runtime service—not through raw database access.

```python
memories = service.get_durable_memories("bit")

memory = service.inspect_durable_memory(
    "bit",
    memories.records[0].id,
)

service.archive_durable_memory("bit", memory.id)
```

The configured chat handler may propose durable memories from explicit user statements. Echo validates and commits those candidates itself; the inference provider cannot write memory directly or claim unsupported personal experiences.

## Restart a runtime generation

Echo provides a deliberate development restart workflow that:

1. Stops accepting new signals.
2. Waits for or cancels active Tasks according to policy.
3. Persists Entity persistent state.
4. Closes configured providers and stores.
5. Creates a fresh Runtime generation.
6. Verifies that persistent state was restored.

Restart requests require an explicit reason, state preservation, and the exact confirmation token `RESTART`:

```console
curl -X POST http://127.0.0.1:8000/runtime/restart \
  -H 'Content-Type: application/json' \
  -d '{
    "reason": "Apply updated runtime resources",
    "confirmation": "RESTART",
    "preserve_state": true
  }'
```

Existing event sockets close with restart code `1012`. Clients should reconnect after the operation reaches a terminal state.

Echo does not hot-replace Python modules. A restart creates a new runtime with new queues, tasks, signals, actions, and bounded histories.

## Integration boundaries

Echo is designed to sit between your application and its intelligence providers:

```text
Application inputs
       ↓
     Signals
       ↓
  Echo Runtime
       ↓
 Entity handlers
   ↙       ↘
State     ProviderRouter
Memory      ↓
   ↘      Inference
       ↓
     Actions
       ↓
Application-owned executors
```

Use the kernel directly when Echo runs inside your process. Use `RuntimeServiceProtocol` when building an adapter or operator tool. Use the HTTP and WebSocket adapter when another process needs to control or inspect Echo.

## Current boundaries

The current implementation intentionally does not provide:

- Automatic execution of external Actions
- Autonomous goal or intention generation
- Signal replay
- Conversation-history persistence as a chat transcript
- Automatic LAN provider discovery
- Provider-owned identity or memory
- Python module hot reload
- Authentication or authorization in the HTTP adapter
- A production process supervisor or deployment platform

These boundaries keep Echo focused on its core responsibility: maintaining a persistent, inspectable entity runtime while allowing applications and inference systems to remain replaceable.
