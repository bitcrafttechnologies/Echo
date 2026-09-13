# Medulla Architecture

Medulla is Echo's stable boundary to the outside world:

```text
WORLD -> Signals -> Medulla -> Echo
Echo  -> Actions -> Medulla -> WORLD
```

Echo decides meaning, attention, memory, goals, and intended behavior. Medulla
discovers and describes external capability, validates boundary data, enforces
trust and permission decisions, routes approved Actions, executes I/O, and
reports availability. Protocol names and protocol objects do not belong in
Echo Core.

Medulla is MCP-like but not MCP-dependent. MCP can later be one provider or
adapter alongside local queues, files, HTTP, WebSockets, MQTT, serial devices,
GPIO, CAN, ROS, and other systems.

## Phase 9 architecture rules

1. Core depends only on Echo-native `Signal`, `Action`, `Capability`, and
   `Resource` concepts. A transport depends inward on those primitives; Core
   never imports a transport or protocol implementation.
2. Medulla contains no cognition. It does not score emotional importance,
   interpret observations, form memories or goals, or choose behavior.
3. External payloads are untrusted. Adapters decode data using non-executable
   codecs, validate it against a closed schema, and return a typed Echo
   `Signal`. Arbitrary Python object deserialization is prohibited.
4. Discovery grants no authority. The preserved lifecycle is `DISCOVERED ->
   IDENTIFIED -> PAIRED -> AUTHORIZED -> ACTIVE`; later subphases must make
   every transition explicit.
5. Discovery registers descriptions, not downloaded code. A remote capability
   remains implemented remotely and is invoked through its transport.

## Phase 9A (`0.9.1`): transport contract

`echo.medulla.Transport` is a runtime-checkable structural protocol. It is
asynchronous at every point that may perform I/O and contains no assumption
about framing, addressing, sessions, brokers, sockets, ports, or hardware.

```python
class Transport(Protocol):
    @property
    def transport_id(self) -> str: ...
    def status(self) -> TransportStatus: ...
    async def start(self) -> TransportStatus: ...
    async def stop(self) -> TransportStatus: ...
    async def receive(self) -> Signal: ...
    async def execute(self, action: Action) -> ActionDispatchResult: ...
    async def health(self) -> TransportHealth: ...
```

This contract can be implemented by a `LocalQueueTransport`,
`WebSocketTransport`, `MQTTTransport`, `SerialTransport`, or a future adapter
without changing Echo Core. `BaseTransport` is the optional standard lifecycle
implementation; structurally compatible implementations may use the protocol
directly.

### Lifecycle

The local lifecycle states are `stopped`, `starting`, `running`, `stopping`,
and `failed`.

- A new transport is stopped.
- `start()` and `stop()` are idempotent after reaching their respective stable
  state. Implementations serialize lifecycle transitions.
- A successful start enters running and permits `receive()` and `execute()`.
- Receive or execute while not running fails with a structured
  `invalid_state` error.
- Start or stop failure enters failed. A later start may retry recovery.
- Stop is responsible for waking or ending transport-owned receive waits and
  releasing transport-owned resources. Medulla orchestration will own bounded
  shutdown policy in a later phase.
- Task cancellation is control flow, not transport failure;
  `asyncio.CancelledError` propagates unchanged.

`status()` is a local, non-blocking snapshot and performs no I/O. `health()`
may actively probe the external system. A failed probe returns an `unavailable`
health snapshot instead of raising into Core. Health is orthogonal to
lifecycle: a running transport can be degraded or unavailable.

### Errors and failure isolation

Transport operations expose `TransportError` with transport ID, operation,
stable error code, retryability, and a safe message. The initial codes cover
invalid state or payload, connection failure, timeout, permission denial,
unavailability, execution failure, queue saturation, and an internal adapter
failure.

`BaseTransport` translates unexpected adapter exceptions into a structured
`TransportOperationError` and retains the last error in status. Callers at the
Medulla supervision boundary must contain these errors and report them through
Echo observability; Echo Core never owns or directly invokes a transport.
Health-probe failures are represented as data. Phase 9A does not introduce
automatic retry, fallback, routing, or reconnection policy.

### Boundary validation

`external_signal_from_dict()` accepts only an ordinary closed-schema mapping
containing canonical Signal fields. Payload and metadata values must be plain,
finite JSON data with string object keys. It creates a fresh base `Signal` and
never imports a named type or invokes conversion hooks.

`BaseTransport.receive()` validates and detaches every returned Signal before
it can cross inward. `BaseTransport.execute()` likewise validates and detaches
the outbound Action before a protocol hook receives it. This is the minimum
common boundary; concrete adapters remain responsible for safe byte decoding,
size limits, protocol authentication, and protocol-specific schema checks.

An outbound result is protocol-neutral. `accepted` means the external system
acknowledged responsibility but completion is not known; `completed` means
success is known; `rejected` and `failed` require structured error data. No
result tells Echo what to want or whether an Action was cognitively appropriate.

## Phase 9B (`0.9.2`): local queue transport

`LocalQueueTransport` is the reference same-process implementation of the
Phase 9A contract. It intentionally does not connect itself to `Runtime`;
future Medulla supervision owns the loop that receives a Signal and emits it
into Echo, and the loop that routes an authorized Action to a transport.

The external-facing local operations are:

- `publish_signal(signal)` for a producer to offer a `Signal` or canonical
  external Signal mapping to the bounded inbound queue;
- the standard `receive()` operation for Medulla to take that Signal inward;
- the standard `execute(action)` operation to validate and offer an Action to
  the bounded outbound queue; and
- `receive_action()` for a same-process external consumer to take the Action.

Both queues require explicit positive capacities and never block a producer
waiting for space. Each direction independently selects `reject_newest` or
`drop_oldest`. Reject-newest preserves every queued item and raises a
retryable, structured `queue_full` transport error. Drop-oldest removes exactly
one oldest item, accepts the new item, increments its drop counter, and reports
the dropped Signal or Action ID.

Local status adds queue depth, capacity, overflow policy, rejection count,
drop count, and shutdown-discard count for both directions without performing
I/O. Health is healthy while running with capacity, degraded when either queue
is full, and unavailable while the transport is not running.

Stop sets a generation-specific shutdown event so all pending `receive()` and
`receive_action()` calls wake. Queued items are discarded, and a later start
creates fresh queues and a fresh event so stale work cannot cross a transport
generation. Stable start and stop remain idempotent. The adapter is intended
for use on one asyncio event loop; cross-thread publication requires an
explicit thread-safe bridge owned by the embedding application.

## `0.9.2-package_prep`: application supervision

`MedullaSupervisor` supplies the first reusable application-level connection
between transports and Echo without reversing the Core dependency direction.
It accepts a structural `SignalTarget`; `Runtime` satisfies that narrow port
through `emit(Signal)` while continuing to import no Medulla code.

On start, the supervisor starts every configured transport independently and
creates one inbound receive pump for each successful start. A failed transport
is retained as structured, bounded failure history and does not stop Runtime or
another transport. Stop first cancels those pumps and then stops every
transport independently. Status reports lifecycle, transport snapshots,
active receive pumps, and recent failures; aggregate health reports healthy,
degraded, or unavailable without raising probe failures into Core.

Outbound Actions cross through `dispatch(action, transport_id=...)`. A sole
transport becomes the default, while a multi-transport application must name a
default or select a transport for each call. Dispatch returns a failed
`ActionDispatchResult` for unavailable, stopped, or failed transport delivery.
It does not read Action history, select behavior, grant permission, retry, or
infer a capability route. The caller must supply an Action that has already
passed its application and behavior authorization boundary.

`EchoApplication` is a small embedding convenience for one Entity. It can use
a programmatically constructed Entity or load a project-owned seed directory,
then composes a Runtime and Medulla supervisor with ordered startup and
shutdown. It is not a new Core abstraction, HTTP server, web UI, or cognition
provider. See `DEVELOPER_QUICKSTART.md` for a clean-project example.

### Local example sources

Three optional one-shot producers demonstrate where world-specific adapters
belong without turning transport into cognition:

- `ClockSignalSource` emits `time.observed` from a timezone-aware local clock.
- `OpenMeteoWeatherSource` fetches bounded JSON current conditions for explicit
  coordinates and emits `weather.observed`.
- `HackerNewsSignalSource` fetches at most ten official API stories and emits
  one bounded `news.headlines_observed` Signal.

The network sources use a small standard-library JSON client with a response
size limit, timeout, no object hooks, and explicit response-field validation.
Their fetch function is injectable so tests and offline deployments can supply
data without network access. Source failures raise `SignalSourceError` before
publication and do not modify the transport or Runtime. These are local
producer examples, not discovered Medulla capabilities, scheduled services, or
trusted Signal authorities.

Run all three through the local transport with:

```bash
python3 examples/local_medulla_sources.py
```

The example defaults to Phoenix coordinates and accepts `--latitude`,
`--longitude`, `--location`, and `--news-limit` overrides.

## Phase 9C (`0.9.3`): WebSocket transport

`WebSocketTransport` is a reconnectable client implementation of the existing
transport contract. `start()` launches its connection manager and succeeds
without requiring the remote endpoint to be online. Connection attempts use
capped exponential backoff; a disconnect clears connection state but leaves
the adapter running, and bounded Actions queued while offline are delivered
after a later connection. Connection failures remain structured adapter state
and cannot terminate Echo Core.

Wire format version 1 is strict UTF-8 JSON with exactly these envelope fields:

```json
{
  "protocol": "echo.medulla",
  "version": 1,
  "type": "signal",
  "id": "message-id",
  "payload": {"signal": {}}
}
```

The reserved message types are `signal`, `action`, `result`, `status`, `hello`,
`manifest`, `heartbeat`, and `error`. Phase 9C processes inbound Signals,
outbound Actions, hello identity, and heartbeat acknowledgements. Result,
status, and error messages are safely decoded observations without attached
semantics. Inbound Action and manifest messages are rejected because Action
ingress and discovery are not part of this phase.

The decoder enforces a byte limit, valid UTF-8, duplicate-free JSON object
keys, finite numbers, a closed envelope, the current protocol version, known
message types, and ordinary JSON payloads. A Signal then passes the existing
closed canonical Signal validator before becoming an Echo object. It never
uses pickle, imports remote types, or invokes arbitrary construction hooks.
Malformed or unsupported messages produce a protocol `error` response where
possible and do not close an otherwise healthy connection.

Status and health expose a query-free endpoint, socket state, connection
timestamps, established/reconnect/message counters, bounded queue depths,
peer hello identity, and wire version. A running but disconnected adapter is
degraded while its retry loop is active; a connected adapter is healthy.

Authentication is deliberately a dependency-injected header-provider hook.
The hook may obtain or refresh credentials, but Phase 9C defines no credential
format or storage, node pairing, authorization, trust state, discovery,
manifest registry, capability routing, or result correlation.

## Phase 9D (`0.9.4`): MQTT transport

`MQTTTransport` is an optional reconnectable client for lightweight sensors
and device messaging. `MQTTBrokerConfig` makes broker hostname, port, client
ID, TLS, username/password, keepalive, and timeout explicit. `MQTTQoS` selects
QoS 0, 1, or 2; inbound and outbound queue capacities and reconnect bounds are
also explicit adapter settings. Credentials never appear in transport status
or health.

`MQTTTopicMap(entity_id=..., prefix="echo")` defines this provisional mapping:

```text
echo/{entity}/signals/#                 inbound Signal subscription
echo/{entity}/actions/{encoded-type}    outbound Action publication
echo/{entity}/status/transport           connection status observations
```

The prefix may contain multiple ordinary topic levels. Entity IDs must be one
literal level, and Action types are percent-encoded into one level. This is
Phase 9D implementation guidance rather than a frozen public discovery
protocol.

MQTT payloads reuse the Phase 9C `echo.medulla` version-1 JSON envelope.
Messages on the Signal subscription must have wire type `signal` and pass the
closed canonical Signal validator before entering Echo. Outbound Actions use
wire type `action`. Connection and graceful-stop observations use wire type
`status`; they are non-retained because this phase does not configure MQTT
Last Will presence, and a stale retained `connected` value would be unsafe.

Like the WebSocket adapter, start launches a background connection manager
without requiring immediate broker availability. Broker errors and disconnects
degrade MQTT health, retry with capped backoff, and retain bounded Actions for
the next connection within the current transport generation. Invalid inbound
messages are rejected and counted without disconnecting a healthy client.
Stopping cancels the manager and discards that generation's queues.

The default client imports the optional `aiomqtt` package lazily. Importing
Echo, Core, Entity, Medulla, or another transport does not require MQTT.
`MQTTTransport` is configured directly by its embedding application; it is not
required by, nor an implementation of, Medulla discovery. Capability routing,
pairing, authorization, trust, durable broker sessions, and MQTT Last Will
presence remain deferred.

## Phase 9E (`0.9.5`): serial transport

`SerialTransport` is an optional reconnectable adapter for microcontrollers
and directly connected embedded devices. `SerialPortConfig` makes the device
path/name, baud rate, read timeout, and write timeout explicit. Queue sizes,
read chunk size, maximum payload size, and reconnect bounds are adapter
settings. The default connection imports `pyserial` only when a port is opened;
the frame codec and parser have no serial-library dependency.

### Embedded frame version 1

A frame has an HDLC-like delimiter and escape layer:

```text
0x7E | escaped body | 0x7E
```

Inside the body, bytes `0x7E` and `0x7D` are encoded as `0x7D` followed by the
original byte XOR `0x20`. After unescaping, the body is:

```text
uint8 frame_version (= 1)
uint16_be payload_length
uint8 payload[payload_length]
uint32_be crc32
```

The CRC-32 is the standard zlib/IEEE CRC over `frame_version`, the two encoded
length bytes, and the payload. The payload is UTF-8 JSON containing one
versioned `echo.medulla` wire message. The default maximum payload is 16 KiB
and implementations may configure a smaller value; the two-byte length makes
65,535 bytes the absolute format maximum.

The receiver incrementally scans delimiters, bounds encoded data, and
resynchronizes after malformed or noisy input. It rejects dangling or invalid
escapes, unsupported frame versions, oversized or mismatched lengths, bad
CRCs, malformed wire JSON, and non-Signal ingress before Core sees any value.
Raw microcontroller boot logs and bytes outside a frame are counted and
discarded.

Outbound Actions are validated, encoded as wire `action` messages, framed, and
placed on a bounded queue. Blocking serial reads/writes run in worker threads.
Port-open, read, and write failures degrade transport health and reconnect with
capped exponential backoff. Queued or interrupted frames survive reconnect
within the active transport generation. Serial failure cannot stop Runtime or
another Medulla transport.

Status and health expose connection state and timestamps, port and baud rate,
connection/reconnect counts, received/sent/rejected frame counts, discarded
noise bytes, queue depths, payload limit, and frame/wire versions. Serial I/O,
framing, and reconnect semantics remain entirely outside Entity and Core.

## Phase 9F capability contract

`Capability` is the versioned, transport-neutral description Echo can inspect
to understand what an external or local provider says it can do. Each
capability declares a stable ID, name, description, provider, ordinary-JSON
input and output schemas, current availability, permission requirements and
risk class, read-only or state-changing effect, and expected and maximum
execution time. Definitions contain data only: there are no callbacks,
implementation objects, executable schemas, or cognitive instructions.

`CapabilityProvider` gives every provider a stable ID, a local or remote
location, and an implementation-family label. The labels cover anticipated
native, MCP, WebSocket, MQTT, Serial, HTTP, GPIO, CAN, and ROS integrations,
but do not couple a capability to a configured transport or implement any of
those provider families.

The in-memory `CapabilityRegistry` indexes stable IDs and provider-qualified
names. An unqualified name resolves only when exactly one provider registered
it; otherwise resolution fails with an explicit collision error. Registry
inspection is sorted by stable ID. Availability updates replace one immutable
capability observation while preserving identity and definition fields.

Capability objects serialize to closed, finite, ordinary JSON values under
contract version 1. Their schemas are opaque JSON schema descriptions at this
boundary; Phase 9F validates safe serialization, not schema dialect semantics
or invocation arguments.

## Phase 9G registry and router

The registry is the central inventory of capability descriptions and provider
identity. Providers may be registered before their capabilities. Each
capability is owned by exactly the provider embedded in its definition;
conflicting metadata for one provider ID is rejected. Capability or whole-
provider removal updates stable-ID, qualified-name, and unqualified-name
indexes together. Provider health is a transport-neutral unknown, healthy,
degraded, or unavailable observation and is serialized with the inventory.

`CapabilityRouter` maps registered provider IDs to configured transport IDs.
An Action's type is the requested capability name; the router does not choose
the Action. Candidate selection excludes unknown/unavailable capabilities,
unavailable providers, and providers without a binding. Remaining candidates
are ordered by availability, health, non-negative configured priority,
provider ID, and capability ID, so registration order cannot change routing.
Callers may explicitly constrain provider or stable capability ID.

Before selecting a transport, the router calls an injected asynchronous
permission hook for each eligible candidate. The hook returns a typed allow or
deny decision; Medulla defines no grant or authorization policy. If no
candidate is permitted, nothing is dispatched. Once a candidate is selected,
the router invokes only its configured transport through the existing
supervisor-compatible dispatch port. It never falls through after execution
starts because doing so could duplicate a state-changing external effect.

Missing routes, denial, permission-hook errors, capability timeout, provider
exceptions, and transport failures all produce structured Action dispatch
failures. External cancellation is preserved. The router adds no cognition,
Action construction, behavior selection, discovery, trust, or provider
implementation.

## Phase 9H remote node protocol

A Medulla Node is not Echo and has no Entity or cognition. It announces one
closed, JSON-only `medulla/1` manifest containing:

- node ID, display identity, and node type;
- one remote provider identity and one or more transport endpoints;
- complete Phase 9F capability descriptions owned by that provider;
- produced Signal type names, schema descriptions, and optional local adapter
  identifiers;
- resource IDs, descriptions, schemas, media types, and inert metadata;
- current provider health; and
- authentication methods and pairing metadata, which are claims rather than
  grants.

The node protocol version is independent of the outer `echo.medulla` wire
version. `node_manifest_message()` places a manifest in the existing reserved
wire `manifest` message, so WebSocket, MQTT, and Serial framing can carry it
without a new transport protocol. `node_manifest_from_message()` performs
closed validation and rejects unsupported protocol versions before anything
is registered.

Capability entries are already Phase 9F `Capability` objects. Their provider
must exactly equal the manifest's remote provider and the provider kind must
have a corresponding endpoint. A Signal description's `name` is the Echo
`Signal.type`; its optional `adapter` is a string identifying locally installed
adapter logic. No import path or executable implementation is accepted or
loaded from a manifest.

`MedullaNodeDirectory` preflights a complete registration against a detached
registry copy before changing the live inventory. On connection it registers
the provider, capabilities, and health, and may create an explicit Phase 9G
route binding to the already configured transport carrying the node. On
disconnect it preserves descriptions but marks the provider and every owned
capability unavailable. A later valid manifest replaces those descriptions
and restores route eligibility. Explicit node removal deletes the provider,
capabilities, health, and binding.

`WebSocketTransport` can pass validated wire manifest messages to an injected
handler and notify an injected node-presence handler when the peer disconnects.
Manifest identity must match an earlier peer `hello` when present. Handler
exceptions become safe protocol or connection observations and never terminate
Echo Core.

## Deliberately deferred after Phase 9H

- concrete HTTP, MCP, and hardware transports;
- active capability, resource, and signal-handler discovery;
- discovery-driven registration and routing;
- node identity, pairing, authorization, permissions, and trust storage;
- cross-transport routing and fallback policy;
- remote implementation loading (which remains prohibited).

Phase 9A through 9E use the `0.9.x` line. Phase 9F begins the Medulla node
iteration series at `0.9-medulla_node-0.1`; Phase 9G is
`0.9-medulla_node-0.2`, and Phase 9H is `0.9-medulla_node-0.3`.
