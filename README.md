# Echo

Echo is a lightweight, persistent, event-driven runtime for intelligent
entities. The implementation currently includes an optional HTTP adapter while
the kernel remains independent of web, LLM, ROS, and semantic-memory
dependencies.

The character architecture amendment establishes a second foundational rule:
Echo—not an inference model—owns Entity identity and continuity. Its design and
vertical phase integration are documented in `docs/CHARACTER_ARCHITECTURE.md`.

The stable Phase 3 control, command, subscription, response, and error contract
is documented in `docs/RUNTIME_API.md`.

## Start Echo before opening a UI

The web console and TUI are clients. Neither one starts Echo Core or its HTTP
management API. Start the API in its own terminal first and leave that terminal
running while using either interface.

The root Core host registers a `UserMessage` handler for Console chat. Each
message follows the normal Signal → Task → `EchoResponse` Action path and uses
the active provider router; provider failures remain visible as failed Tasks.

All commands in this section are run from the repository root. The complete
non-interactive installation is:

```console
./install.sh
```

The installer creates `.venv`, installs Echo with its API/test dependencies,
installs the locked Console packages, and builds the Web Console. It never
creates, edits, or prompts about configuration. Start the installed system with
one of:

```console
./start.sh                         # Core and management API only
./start.sh --web                   # Core plus Web Console
./start.sh --web --host 0.0.0.0    # Expose Core and Web Console on the LAN
./start.sh --terminal              # Core plus tmux terminal Console
./start.sh --terminal --no-tmux    # Core plus single-terminal Console
./start.sh --web --terminal        # Core plus both interfaces
./start.sh --config path/to/echo.toml --web
```

The launcher loads an optional ignored project `.env` as a secret overlay
without replacing variables already present in its process environment. It
then uses `ECHO_CONFIG`, an existing repository `echo.toml`, or typed defaults
in that order. It does not rewrite the selected configuration. Use `--host
0.0.0.0` to expose Core and the development Web Console; its API proxy still
connects to Core over loopback.

### 1. Manual installation

Python 3.11 or newer is required. The following creates an isolated environment
and installs Echo, its optional FastAPI adapter, and the Uvicorn development
server:

```console
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[api]' uvicorn
```

This installation is normally needed only once. In a new terminal, run
`source .venv/bin/activate` before using `python`, `uvicorn`, or `echoc`.

### 2. Optionally create and validate configuration

The scripts use existing configuration as-is and require no configuration file.
Phase 6A provides one typed TOML configuration layer for Runtime, logging,
history limits, provider routing and concrete providers, the API server, and
Console connectivity. Start from the checked-in example:

```console
cp echo.example.toml echo.toml
echoc config validate echo.toml
```

Enable only the providers you intend to use in `echo.toml`. Prefer environment
variables for credentials and other deployment-specific overrides:

```dotenv
ECHO_OPENROUTER_ENABLED=true
OPENROUTER_API_KEY=replace-with-your-key
```

`ECHO_CONFIG` may select a different TOML file. Supported provider aliases from
Phase 5 remain valid as overrides, including `OPENROUTER_*`,
`LAN_INFERENCE_*`, `LLAMA_CPP_*`, and `OFFLINE_*`. The loader validates the
complete merged result before constructing Runtime or provider objects and
reports all discovered field errors together. Do not commit secrets.

The offline provider remains lazy. Merely starting Echo or inspecting provider
health does not load the model. Offline inference also requires the
`llama-server` executable to be installed or identified by `LLAMA_CPP_SERVER`.

### 3. Manual Echo Core startup

In terminal 1, run the development host. This validates configuration before
starting the Runtime or binding the API server:

```console
source .venv/bin/activate
python - <<'PY'
import os
import uvicorn

from echo import (
    Entity,
    RuntimeConfigurationManager,
    RuntimeService,
    load_config,
)
from echo.adapters.fastapi import create_app

config_path = os.environ.get("ECHO_CONFIG", "echo.toml")
config = load_config(config_path)
config.configure_logging()
if not config.api.enabled:
    raise SystemExit("Echo API is disabled by configuration")
runtime = config.create_runtime([Entity("bit")])
router = config.create_provider_router()
configuration_manager = RuntimeConfigurationManager(
    config,
    runtime,
    provider_router=router,
    config_path=config_path,
)
service = RuntimeService(
    runtime,
    provider_router=router,
    configuration_manager=configuration_manager,
)
app = create_app(service)

uvicorn.run(app, host=config.api.host, port=config.api.port)
PY
```

The terminal should report that Uvicorn is running on
`http://127.0.0.1:8000`. In terminal 2, verify Echo before opening a UI:

```console
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/providers
curl http://127.0.0.1:8000/configuration
```

The first command must return `{"status":"ok"}`. The provider response shows
the current `auto` mode and health for OpenRouter, LAN, and offline. A provider
may be unavailable without taking Echo Core down; inference follows the bounded
OpenRouter → LAN → offline fallback order.

Phase 6B reload is an explicit management action. After editing the active
file, use `echoc config reload`, `POST /configuration/reload`, or the Console
Providers view. Logging, routing mode/preference, and practical history limits
apply live. Startup construction and connectivity changes are reported as
restart-required, and if any such change is present nothing in that reload is
applied. Invalid files leave the active configuration untouched.

The Console Configuration view shows the active effective values, labels live
and restart-required fields, and represents credentials only as configured or
not configured. API keys are never returned to the browser. Approved settings
can also be changed with `echoc config set providers.mode '"lan"'`.

### 4A. Open the web console

Keep terminal 1 running. In terminal 2:

```console
cd console
npm install
npm run dev
```

Open the local URL printed by Vite, normally
`http://127.0.0.1:5173`. The development proxy connects it to Echo on port
8000.

### 4B. Open the TUI

Alternatively, keep terminal 1 running and use a second terminal from the
repository root:

```console
source .venv/bin/activate
echoc console
```

This opens the tmux workspace. Use `echoc console --no-tmux` for one terminal,
or `echoc console --snapshot --surface overview` for a noninteractive status
snapshot. Press Ctrl-C in terminal 1 when you are finished with Echo.

## Development

Echo requires Python 3.11 or newer and uses only the standard library at
runtime.

```console
python -m unittest discover -s tests -v
```

Install the optional Phase 4A HTTP adapter with:

```console
python -m pip install -e '.[api]'
```

Create the ASGI application around an existing service rather than a second
Runtime:

```python
from echo.adapters.fastapi import create_app

app = create_app(runtime_service)
```

The HTTP and `/events` WebSocket contracts are documented in
`docs/HTTP_API.md`.

The Phase 4E SvelteKit development interface lives in `console/`. After starting
Echo with the procedure above, run:

```console
cd console
npm install
npm run dev
```

The first-party terminal interface mirrors the implemented web surfaces and
uses the same management boundary. With Echo already running, launch its tmux
workspace:

```console
echoc console
```

Use `echoc console --no-tmux` for one terminal, or
`echoc console --snapshot --surface overview` for semantic plain text. Embedded
hosts can use `LocalEchoClient` with an existing `RuntimeService` and need no
HTTP server. `echoc` loads and validates `echo.toml` (or `ECHO_CONFIG`) at
startup. Use `echoc config reload` to request the shared Phase 6B management
operation; it does not create a terminal-only mutation path. See `docs/TUI.md`.

## OpenRouter development provider

Phase 5C supplies an optional OpenRouter provider as the remote-first
development target. It uses the OpenAI-compatible chat-completions endpoint
without adding an SDK dependency. The Phase 6A application path is the typed
configuration shown above; these low-level provider constructors and legacy
environment readers remain available for embedded compatibility:

```console
export OPENROUTER_API_KEY='your-key-from-the-secret-store'
export OPENROUTER_MODEL='openrouter/auto'
```

```python
from echo.providers import OpenRouterProvider, ProviderRouter

router = ProviderRouter(remote=OpenRouterProvider(), mode="auto")
```

Applications with their own secret/configuration loader may instead construct
`OpenRouterConfig` directly. API keys are never included in provider metadata,
results, or logs. `OpenRouterProvider` performs one request and surfaces a
structured error; only `ProviderRouter` may fall back to LAN or offline slots.
`OPENROUTER_URL` is accepted as an alias for `OPENROUTER_BASE_URL`.

Phase 5D adds an explicitly configured LAN target for llama.cpp or another
OpenAI-compatible server. A host root, `/v1` API root, or full chat-completions
URL is accepted and normalized:

```console
export LAN_INFERENCE_URL='http://echo-model-server.local:8080'
export LAN_INFERENCE_MODEL='local-model'
export LAN_INFERENCE_TIMEOUT_SECONDS='30'
```

`LAN_INFERENCE_BASE_URL` and `LLAMA_CPP_BASE_URL` are accepted URL aliases;
`LLAMA_CPP_MODEL` is accepted as a model alias. If the server requires a key,
use `LAN_INFERENCE_API_KEY` or `LLAMA_CPP_API_KEY`.

```python
from echo.providers import LanInferenceProvider, OpenRouterProvider, ProviderRouter

router = ProviderRouter(
    remote=OpenRouterProvider(),
    lan=LanInferenceProvider(),
    mode="auto",
)
```

No LAN discovery or provider-internal fallback occurs.

Phase 5E adds a lazy offline fallback backed by a child `llama-server` process.
The model path is always explicit; Echo does not scan `models/` or load a model
during provider construction or health inspection:

```console
export OFFLINE_MODEL_PATH='models/Mistral-7B-Instruct-v0.3.Q4_K_M.gguf'
export LLAMA_CPP_SERVER='llama-server'
```

```python
from echo.providers import (
    LanInferenceProvider,
    OfflineInferenceProvider,
    OpenRouterProvider,
    ProviderRouter,
)

offline = OfflineInferenceProvider()
router = ProviderRouter(
    remote=OpenRouterProvider(),
    lan=LanInferenceProvider(),
    offline=offline,
    mode="auto",
)

# Releases the child process and its model memory when no longer needed.
await offline.unload()
```

The offline process starts only if routing actually reaches it. A successful
OpenRouter or LAN request therefore leaves the local model unloaded. The
default backend requires `llama-server` on `PATH`, or an explicit executable
path through `LLAMA_CPP_SERVER`/`OfflineInferenceConfig`.

Phase 5F attaches the router to the optional management plane and exposes the
same provider status and mode controls in the web and terminal consoles:

```python
from echo import InferenceRequest, RuntimeService

service = RuntimeService(runtime, provider_router=router)
result = await service.infer(InferenceRequest(prompt="Hello"))
status = await service.inspect_providers()
await service.set_provider_mode("lan")
```

The status identifies the provider that actually served every retained request,
plus configured providers, health, active model, last latency, and recent
failures. Use `provider status` and `provider mode auto|remote|lan|offline` in
the Echo command prompt. New application startup should construct this router
through `EchoConfig.create_provider_router()` so configuration is validated
before providers are created.

## Phase 1 example

```python
from echo import Entity, Runtime, Signal

bit = Entity("bit")
runtime = Runtime([bit])

@bit.on("battery.low")
async def battery_low(signal):
    bit.state["battery"] = signal.payload["percent"]
    await bit.action("speak", text="I should probably charge soon.")

await runtime.emit(Signal(type="battery.low", payload={"percent": 0.08}))
```

Awaiting `emit` returns after the signal and all matching handlers have been
processed. Actions are observable intent records in Phase 1; external execution
is intentionally deferred.

## Character architecture base

`src/echo/entity/` contains provider-independent base value types for identity,
traits, internal state, drives, relationships, memory, and the self-model.
Phases 2–5
compose identity, traits, embodiment-independent self-model, runtime control
state, drives, attention, detached per-person social context, and five typed
memory categories into the public `Entity`; later character slices remain
deferred to their roadmap phases. Bit remains reference configuration under
`entities/bit/`, including
model guidance under `entities/bit/prompts/`.
