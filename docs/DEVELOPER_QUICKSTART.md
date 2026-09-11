# Using Echo in a New Python Project

This guide gets one project-owned Echo Entity running behind the local Medulla
transport. The website, simulator, or other external system remains a separate
application and communicates with Echo through project-specific Signals and
Actions.

## What the package provides

`echo-runtime` provides Echo Core, the generic Entity model and file loader,
Medulla's transport boundary, and a small `EchoApplication` composition helper.
It does not package the Echo repository's web console or the Bit character
files. Your project owns its character definition and integration vocabulary.

Python 3.11 or newer is required. Until a release is published to a package
index, install a checked-out build or Git revision:

```console
python3 -m venv .venv
source .venv/bin/activate
python -m pip install /path/to/Echo
```

After publication, the corresponding command will be:

```console
python -m pip install echo-runtime==0.9.5
```

Install `echo-runtime[websocket]==0.9.5` when the project uses the Phase 9C
WebSocket transport.

## 1. Create a project-owned Entity

Create this structure in your new project:

```text
my-echo-project/
├── entities/
│   └── echo/
│       ├── identity.yaml
│       ├── traits.yaml
│       ├── drives.yaml
│       └── prompts/
│           └── character.md
└── main.py
```

`entities/echo/identity.yaml`:

```yaml
schema_version: 1
entity:
  id: echo
  name: Echo
  type: website_character
  presentation: text
identity:
  worldview: Curious, thoughtful, and grounded
  core_values:
    - honesty
    - curiosity
```

`entities/echo/traits.yaml`:

```yaml
schema_version: 1
traits:
  curiosity: 0.82
  patience: 0.75
```

`entities/echo/drives.yaml`:

```yaml
schema_version: 1
drives:
  learning: 0.75
  connection: 0.65
```

`entities/echo/prompts/character.md`:

```markdown
Speak in the first person as Echo. Be curious, concise, and honest about
uncertainty.
```

These four files are the supported version-1 seed contract. A `policies.yaml`
file is not currently interpreted by the generic loader; behavior policy is
configured through the Python API until a typed policy-file contract exists.

## 2. Connect Echo to local Medulla

Put the following in `main.py`:

```python
import asyncio

from echo import EchoApplication, LocalQueueTransport, Signal


async def main() -> None:
    transport = LocalQueueTransport(
        transport_id="website",
        inbound_capacity=100,
        outbound_capacity=100,
    )
    app = EchoApplication.from_entity_directory(
        "entities/echo",
        [transport],
    )

    message_received = asyncio.Event()

    @app.entity.on("website.user_message")
    async def handle_user_message(signal: Signal) -> None:
        text = signal.payload.get("text")
        if not isinstance(text, str) or not text:
            return

        # This simple response proves both boundary directions. A real project
        # can ask its cognition provider for content before creating the Action.
        action = await app.entity.action(
            "website.avatar_speak",
            text=f"Echo received: {text}",
        )
        await app.dispatch(action)
        message_received.set()

    async with app:
        # This call represents a same-process external producer. A later
        # WebSocket transport will normalize a network message the same way.
        await transport.publish_signal(
            Signal(
                type="website.user_message",
                source="example-browser",
                payload={"text": "Hello"},
            )
        )
        await asyncio.wait_for(message_received.wait(), timeout=2)

        # This call represents the website backend consuming Echo's output.
        action = await transport.receive_action()
        print(action.type, action.parameters)


asyncio.run(main())
```

Run it from the project root:

```console
python main.py
```

Expected output:

```text
website.avatar_speak {'text': 'Echo received: Hello'}
```

`EchoApplication` starts Runtime before Medulla and stops Medulla before
Runtime. `MedullaSupervisor` continuously delivers inbound Signals to the
runtime, contains transport failures, and exposes status and aggregate health.

Outbound delivery is deliberately explicit. Creating an Action records Echo's
intent; `app.dispatch(action)` separately authorizes the application-level I/O
attempt. Echo does not automatically execute everything in Action history.

## 3. Construct an Entity in Python instead

Character files are optional. A project can construct the Entity with the
library's typed configuration objects:

```python
from echo import DriveProfile, EchoApplication, Entity, EntityIdentity
from echo import LocalQueueTransport, TraitProfile

entity = Entity(
    "echo",
    identity=EntityIdentity(
        entity_id="echo",
        name="Echo",
        entity_type="website_character",
        presentation="text",
        worldview="Curious and grounded",
        core_values=("honesty", "curiosity"),
    ),
    traits=TraitProfile({"curiosity": 0.82}),
    drives=DriveProfile({"learning": 0.75}),
)
app = EchoApplication(entity, [LocalQueueTransport("website")])
```

## Defining project Signals and Actions

Use namespaced types so ownership is clear. For example:

```text
website.user_message
website.page_changed
website.avatar_visible

website.avatar_speak
website.avatar_animate
website.navigate
```

Transport validation guarantees safe Echo primitives and JSON-compatible
boundary data. Your project still owns the schema for each type and must check
required fields, ranges, and permissions before using them. Discovery,
capability registration, and durable trust policy arrive in later Phase 9
work.

When WebSocket transport is added, replace or add the transport in application
composition. Entity handlers and Echo Core do not change:

```text
browser <-> website backend <-> WebSocketTransport <-> Medulla <-> Echo
```

Keep browser code, page rendering, authentication, and deployment in the
website project. The Python backend is the process that installs and runs
`echo-runtime`.

## Inspecting the boundary

```python
status = app.medulla.status()       # local, non-I/O snapshot
health = await app.medulla.health() # probes each transport

print(status.lifecycle)
print(status.recent_failures)
print(health.state)
```

With multiple transports, configure a default or name the destination on each
dispatch:

```python
app = EchoApplication(
    entity,
    [website_transport, device_transport],
    default_transport_id="website",
)

await app.dispatch(action, transport_id="device")
```

Selecting a transport is not capability discovery or permission. Applications
must dispatch only Actions that have passed their own explicit authorization
boundary until those later Medulla services are implemented.
