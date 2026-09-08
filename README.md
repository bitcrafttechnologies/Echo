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

The Phase 4C SvelteKit development interface lives in `console/`. With the Echo
API running on port 8000, start it with:

```console
cd console
npm install
npm run dev
```

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
traits, internal state, drives, relationships, and the self-model. Phase 2
composes immutable identity and traits plus the embodiment-independent
self-model into the public `Entity`; later character slices remain deferred to
their roadmap phases. Bit remains reference configuration under `entities/bit/`,
including model guidance under `entities/bit/prompts/`.
