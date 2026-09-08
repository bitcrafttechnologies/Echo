Project Echo — Clean-Sheet Redevelopment Plan

1. Objective

Rebuild Echo from scratch as a lightweight, persistent, event-driven runtime for intelligent entities.

Echo is intended to scale from development laptops to SBCs, robotics platforms, and embedded/edge environments without requiring an LLM to function.

The foundational definition is:

Echo is a persistent event-driven runtime for an intelligent entity.

Echo should not become another LangChain-style LLM application framework. Models, memory systems, robotics middleware, transports, and other AI frameworks should plug into Echo rather than define its architecture.

The system should remain understandable at its core even as capabilities grow.

The primary architectural rule is:

Complexity belongs outside the kernel.

The persistent character amendment adds an equally important ownership rule:

The model does not own the Entity. Echo owns the Entity.

Identity, personality continuity, memories, relationships, goals, embodiment,
preferences, and character development must survive changes in inference
provider. The detailed design and acceptance criteria are maintained in
`docs/CHARACTER_ARCHITECTURE.md`; the phase integrations below are normative.

⸻

2. Core Philosophy

Echo revolves around four fundamental primitives:

1. Entity
2. Signal
3. Task
4. Action

Medulla serves as the boundary between Echo and the outside world.

Everything else is optional infrastructure or a service.

The basic runtime model is:

Environment
    ↓
 Signal
    ↓
 Entity
    ↓
 Handler / Task
    ↓
 Action
    ↓
 Medulla
    ↓
Environment

The Echo kernel must be capable of functioning completely without:

* an LLM
* vector databases
* LangChain
* LangGraph
* ROS
* cloud connectivity
* semantic memory
* model inference

A small Echo deployment should be able to operate as:

signals
state
handlers
tasks
actions

More advanced intelligence is layered on top.

⸻

3. Core Primitives

Entity

An Entity represents a persistent intelligent actor.

The Entity owns:

* identity
* state
* active tasks
* registered handlers
* capabilities
* references to optional services

Example:

bit = Entity("bit")

A minimal working Echo application should be approximately this simple:

from echo import Entity
bit = Entity("bit")
@bit.on("battery.low")
async def battery_low(signal):
    await bit.action("speak", text="I should probably charge soon.")
bit.run()

An LLM must not be required.

The Entity should expose simple high-level capabilities such as:

await bit.think(...)
await bit.remember(...)
await bit.act(...)
await bit.emit(...)

These should delegate internally to optional providers.

Do not make users understand deep object chains such as:

Entity
→ Mind
→ TurnManager
→ Provider
→ Model

The Entity is the primary public abstraction.

⸻

Signal

Signals are the central communication primitive of Echo.

A Signal represents:

Something happened.

Examples:

UserMessage
CameraFrame
PersonDetected
BatteryLow
TimerExpired
ToolCompleted
MemoryRetrieved
MotorFault
NetworkDisconnected
TaskCompleted
FileChanged
TemperatureChanged
TouchDetected

Base structure:

class Signal:
    type
    source
    timestamp
    payload
    metadata

Typed Signals should be supported:

class BatteryStatus(Signal):
    voltage: float
    percent: float
    charging: bool

Use typed schemas where useful, preferably using lightweight Pydantic models or dataclasses.

Signals should be:

* serializable
* loggable
* inspectable
* replayable
* routable
* transportable
* timestamped
* source-aware

Decorators should remain simple registration helpers:

@bit.on(BatteryLow)
async def handle_low_battery(signal):
    ...

Internally this should effectively resolve to:

bit.handlers.register(BatteryLow, handle_low_battery)

Avoid decorator magic that hides control flow.

⸻

Task

Tasks replace chatbot-specific concepts such as TurnManager.

A robot or persistent AI does not fundamentally operate in conversational turns.

Tasks represent ongoing units of work.

Examples:

answer-user
bring-water
monitor-door
inspect-directory
navigate-home
identify-object

A Task should include:

id
name
status
priority
created_at
started_at
completed_at
context
parent
children
result
error
owner

Possible statuses:

pending
running
paused
blocked
completed
failed
cancelled

Tasks must eventually support interruption and resumption.

Example:

BringWater
    ↓
running
ObstacleDetected
    ↓
BringWater paused
ObstacleAvoidance
    ↓
BringWater resumed

Tasks should support parent/child relationships for planning.

⸻

Action

An Action represents:

Something Echo intends to do.

Examples:

Speak
Move
TurnHead
ReadFile
WriteFile
RunInference
SendMessage
Navigate
StoreMemory
CallTool
SetGPIO

Actions should be structured objects rather than arbitrary function calls whenever practical.

Example:

SpeakAction(text="Hello")

Actions may be:

* executed locally
* routed through Medulla
* serialized
* logged
* replayed where safe
* converted into external command representations

The future Echo command language should eventually serialize Actions but should not initially be required by the core runtime.

⸻

4. Medulla

Medulla should be sharply defined as:

The I/O boundary between Echo and the outside world.

Medulla does not decide what the Entity wants.

Medulla handles communication and execution.

Conceptually:

           Echo
            │
         Medulla
            │
 ┌──────────┼───────────┐
 │          │           │
GPIO       MQTT        ROS
Serial     HTTP        CAN
Camera     Audio       Remote
WebSocket  Sensors     Devices

Inbound:

hardware/network/world
        ↓
     Medulla
        ↓
      Signal

Outbound:

      Action
        ↓
     Medulla
        ↓
hardware/network/world

Medulla transports should be pluggable.

Potential transports:

LocalQueueTransport
WebSocketTransport
HTTPTransport
MQTTTransport
ZeroMQTransport
SerialTransport
CANTransport
ROS2Transport

The same Signal should be able to originate:

* inside Echo
* from another Python process
* from another CPU
* from a microcontroller
* from another machine
* from another robot

without changing the Entity-facing API.

⸻

5. Runtime Architecture

Do not maintain separate conceptual “main” and “telemetry” runtimes.

Instead build one unified asynchronous event runtime.

Use:

Python
asyncio
typed queues
priorities
scheduled events

Basic conceptual loop:

while runtime.running:
    signal = await scheduler.next_signal()
    entity.update(signal)
    handlers = router.resolve(signal)
    tasks = await dispatch(handlers, signal)
    actions = await collect_actions(tasks)
    await execute(actions)

The real implementation will become more sophisticated, but this flow should remain recognizable.

The scheduler should support signal classes such as:

critical
high
normal
background
periodic

Examples:

MotorFault           critical
ObstacleDetected     high
UserMessage          normal
MemoryMaintenance    background
BatteryStatus        periodic

Sensor telemetry therefore does not require a second conceptual loop.

Sensors simply generate Signals at appropriate frequencies and priorities.

⸻

6. Intelligence Architecture

Models must be resources available to Echo, not the center of Echo.

Create a generic provider interface.

Example:

class IntelligenceProvider:
    async def infer(self, request):
        ...
    async def embed(self, request):
        ...
    async def classify(self, request):
        ...

Potential implementations:

OpenRouterProvider
LlamaCppProvider
ONNXProvider
TensorRTProvider
ExecuTorchProvider
LiteRTProvider
MockProvider
LangChainProvider

Echo itself should not care which implementation performs inference.

⸻

7. Default Model Routing and Battery-Aware Development

During Echo framework development on the laptop, inference should default toward remote execution whenever possible to avoid unnecessary battery drain, heat, and sustained CPU/GPU usage.

Default provider preference:

1. Remote OpenRouter
        ↓ unavailable / disabled
2. Local network inference server
        ↓ unavailable / disabled
3. Offline model on the laptop/device

In shorthand:

OpenRouter
   ↓
LAN model server
   ↓
offline/local device model

This should be implemented as a provider router rather than scattered fallback logic.

Example:

router = ProviderRouter([
    OpenRouterProvider(...),
    RemoteLlamaCppProvider(...),
    LocalOfflineProvider(...),
])

Expected behavior:

request
   ↓
provider router
   ↓
OpenRouter available?
   ├─ yes → use it
   └─ no
        ↓
LAN inference server available?
   ├─ yes → use it
   └─ no
        ↓
offline model available?
   ├─ yes → use it
   └─ no → structured inference unavailable error

The provider router should support explicit overrides:

auto
remote
lan
offline

Example configuration:

inference:
  mode: auto
  preference:
    - openrouter
    - lan
    - offline

The development default should be:

inference:
  mode: auto

This allows development work to use OpenRouter while the laptop remains relatively cool and battery-efficient.

If Internet connectivity disappears but the local network inference machine is available, use it.

If both are unavailable, automatically fall back to the offline model.

Provider selection should be visible in the Echo Console.

Example:

Inference
Mode: AUTO
ACTIVE
OpenRouter → qwen/...
Fallback 1
LAN → http://echo-server.local:8080
Fallback 2
Offline → ./models/qwen.gguf

Individual tasks should eventually be able to specify requirements:

await bit.think(
    prompt,
    provider="auto",
    requirements={
        "latency": "low",
        "privacy": "normal",
    },
)

Certain workloads may force offline execution:

provider="offline"

or force remote execution:

provider="remote"

The system should log which provider actually served every request.

⸻

8. Edge Intelligence

Do not send every sensor event to an LLM.

Most physical signals should be processed using:

* normal deterministic code
* threshold logic
* small classifiers
* ONNX models
* TensorRT
* ExecuTorch
* LiteRT/TFLite
* hardware DSP where appropriate

Example:

Camera
  ↓
small vision model
  ↓
PersonDetected
  ↓
Echo Signal Runtime

Not:

Camera
  ↓
LLM
  ↓
everything

Another example:

IMU
  ↓
orientation / stability algorithm
  ↓
BalanceInstability
  ↓
Echo

Echo should escalate to a larger reasoning model only when contextual reasoning is useful.

Example:

Signal
  ↓
simple handler enough?
  ├─ yes → handle
  └─ no
      ↓
small model enough?
  ├─ yes → classify
  └─ no
      ↓
LLM reasoning

This architecture is necessary for real edge deployment.

⸻

9. Model Technology Priorities

Prioritize these integrations:

llama.cpp

Primary local LLM inference backend.

Support:

* LAN-hosted llama.cpp server
* local device llama.cpp
* OpenAI-compatible server interfaces

⸻

ONNX Runtime

High priority.

Use for:

* vision
* audio classification
* embeddings
* object detection
* classifiers
* sensor interpretation
* lightweight ML

ONNX will often matter more to Echo’s robotics target than LangChain.

⸻

TensorRT

Optional NVIDIA backend.

Useful for:

* Jetson hardware
* NVIDIA edge systems
* optimized vision
* model inference

⸻

ExecuTorch / LiteRT

Potential future support for constrained edge devices.

Keep them outside the kernel.

⸻

10. LangChain / LangGraph

Do not make LangChain a core dependency.

If supported, implement it through an adapter:

echo.use(LangChainAdapter(...))

LangGraph is more interesting conceptually but still should not define Echo.

Borrow useful ideas such as:

* stateful workflows
* interruptible execution
* task graphs
* resumable planning

Echo should eventually implement its own small behavior/task graph system if necessary.

⸻

11. ROS 2

ROS should be an integration boundary rather than Echo’s foundation.

ROS handles:

robot drivers
sensor topics
navigation
hardware abstraction
robot tooling

Echo handles:

persistent entity state
attention
tasks
reasoning
memory
behavior
meaningful Signals
decision making

Architecture:

ROS2
 ↓
Echo ROS Adapter
 ↓
Signals
 ↓
Entity

Echo must function without ROS.

⸻

12. Entity Character, State, and Memory

Avoid building a giant abstract “memory system” or treating character as one
prompt. Entity-owned state must be explicit and separated by responsibility.

Reserve:

echo/entity/identity.py
echo/entity/traits.py
echo/entity/state.py
echo/entity/drives.py
echo/entity/relationships.py
echo/entity/self_model.py

Identity is stable and provider-independent. Traits change slowly through
bounded evidence. Internal state changes rapidly through normalized control
variables. Drives influence attention and goals without forcing Actions.
Relationships remain scoped per person. The self-model keeps Entity identity
separate from embodiment, capability, condition, location, and active goals.

Memory must begin with explicit types:

working
episodic
semantic
preferences
relationship memory

Do not store everything. Score experiences for novelty, surprise, state and
relationship significance, goal relevance, future usefulness, repetition, and
unresolved importance. Consolidation may infer patterns from repeated events,
but Echo decides whether those proposals update semantic knowledge,
preferences, relationships, or traits.

Models may propose responses, intentions, observations, memories, goals, and
trait evidence. They may not directly rewrite persistent Entity data. Accepted
changes pass through explicit Echo APIs, use bounded rules, and retain an audit
trail.

When cognition is needed, a Character Context Builder selects relevant
identity, traits, internal state, active drives, relationship context, memories,
goals, self-model, and environment. It must not dump all stored information
into a prompt. Context and proposal contracts remain provider-neutral.

Start persistence with something simple such as SQLite. Semantic/vector
retrieval remains an optional provider; embeddings are not required for core
Echo operation.

Bit's initial configuration and prompts live under `entities/bit/`, outside
generic framework internals. Bit begins with a curious, optimistic,
observant, maker-oriented temperament and room to develop through experience.

⸻

13. Echo Console

Build the Echo Console concurrently with the runtime.

Do not wait until the framework is “finished.”

The Console should become the primary development, testing, inspection, and live communication environment for Echo.

Recommended stack:

Svelte / SvelteKit
TypeScript
WebSockets
HTTP API

Backend/API:

FastAPI
WebSockets

FastAPI should remain isolated from the Echo kernel.

The runtime should be usable without it.

⸻

14. Console Layout

Initial Console concept:

┌────────────────────────────────────────────────────────────┐
│ ECHO CONSOLE                         BIT ● RUNNING          │
├───────────────┬────────────────────────────────────────────┤
│ ENTITY        │ SIGNAL STREAM                              │
│               │                                            │
│ State         │ 16:22:01 PersonDetected                   │
│ Tasks         │ 16:22:02 UserMessage                      │
│ Memory        │ 16:22:02 TaskStarted                      │
│ Models        │ 16:22:03 InferenceRequest                 │
│ Hardware      │ 16:22:04 ActionCreated                    │
│ Providers     │ 16:22:04 SpeechAction                     │
│ Logs          │                                            │
│ Config        │                                            │
├───────────────┴────────────────────────────────────────────┤
│ >                                                    SEND │
└────────────────────────────────────────────────────────────┘

Major sections:

Overview
Entity Inspector
Signal Inspector
Task Inspector
Action Inspector
State
Memory
Providers
Hardware
Logs
Configuration
Chat

⸻

15. Signal Inspector

This should be one of the first major development tools.

Every Signal should be inspectable.

Example:

PersonDetected #81531
Source
vision.front
Timestamp
16:22:04.282
Payload
confidence: 0.932
person_id: 18
Routed to
attention_handler
proximity_handler
Created Tasks
#981 evaluate-nearby-person
Created Actions
HeadTurnAction

The Console should allow:

inspect
filter
search
pause stream
copy
replay

Replay is particularly important.

A recorded Signal should be injectable again:

REPLAY SIGNAL

This enables repeatable debugging without reproducing physical circumstances.

⸻

16. Synthetic Signal Injection

Developers must be able to manually inject Signals into a running Entity.

Examples:

signal battery.low percent=0.12
signal person.detected confidence=0.93
signal network.disconnected interface=wlan0

This should work from:

* browser Console
* CLI
* automated tests

⸻

17. Chat With Echo

Talking to Echo through the Console must not bypass its architecture.

A message typed in Chat should generate:

UserMessage(
    source="console",
    text="What are you seeing?"
)

Echo processes this normally.

A response should be emitted as an Action or Signal such as:

TextResponse(...)

or:

SpeechAction(...)

Therefore Chat is simply another interface into the Signal system.

⸻

18. Runtime Command Interface

Provide a structured developer command system.

Examples:

entity inspect bit
signal list
signal replay 81531
task list
task inspect 981
task cancel 981
state get location
state set location workshop
provider status
provider switch auto
provider switch offline
action invoke speak text="Testing Echo"
runtime status

Avoid arbitrary Python eval through the Console.

Commands should map to structured runtime operations.

⸻

19. CLI

Build an Echo CLI using the same runtime API as the Console.

Initial commands:

echo run bit.yaml
echo status
echo attach
echo inspect bit
echo signals
echo tasks
echo providers
echo logs

echo attach should connect to a live Echo runtime.

Example:

$ echo attach
Connected to bit@localhost
echo> signal battery.low percent=.12
Signal injected: #19282
echo> task list
...

The CLI and browser should not have separate control implementations.

Both should use the same API.

⸻

20. Remote Development

The runtime API should make it possible to manage Echo remotely.

Potential development workflow:

Laptop browser
      ↓
Echo Console
      ↓
WebSocket/API
      ↓
Echo running on another machine

Eventually the same interface should work from:

laptop
phone
tablet
desktop
development server
robot

The Console should not assume Echo is running on localhost.

⸻

21. Live Configuration

Separate:

code
configuration
state

These must not be conflated.

Configuration should be reloadable while Echo is running where safe.

Examples:

provider preference
model selection
signal thresholds
handler enable/disable
logging verbosity
sensor frequencies
transport configuration

Example:

config signal.PersonDetected.threshold = 0.72
config handler.greeting.enabled = false
config inference.mode = auto

Configuration changes should generate their own audit Signals/events.

⸻

22. Code Reloading

Do not initially implement arbitrary Python hot module replacement.

It introduces difficult state and task consistency problems.

Instead support graceful runtime restart with state preservation.

Development cycle:

Echo running
   ↓
serialize persistent state
   ↓
stop runtime
   ↓
load new code
   ↓
start runtime
   ↓
restore state

The Console should eventually provide:

RESTART RUNTIME

with optional state preservation.

Tasks that cannot safely survive restart should be marked accordingly.

⸻

23. Logging and Replay

Echo should be observable by design.

Record:

signals
task lifecycle
actions
state transitions
provider calls
inference routing
errors
runtime events
configuration changes

Use structured logging.

Eventually support session recording:

echo record start
echo record stop

and replay:

echo replay session.echo

This should allow developers to replay sensor and runtime events against an Echo instance.

Replay should support:

real-time speed
accelerated
step-by-step
signal-by-signal

This will be extremely valuable for robotics debugging.

⸻

24. Framework / Language Choices

Initial implementation:

Python 3.12+
asyncio
Pydantic or dataclasses
SQLite
FastAPI
WebSockets
Svelte/SvelteKit
TypeScript

Keep dependencies minimal.

Suggested core dependencies:

asyncio / standard library
typing
Pydantic if justified

The kernel should not require FastAPI, ONNX, llama.cpp, ROS, LangChain, etc.

⸻

25. Future Rust Migration

Do not rewrite Echo in Rust now.

The architecture is still evolving.

However, design clear module boundaries so stable low-level parts can eventually move to Rust.

Likely Rust candidates:

Signal runtime
scheduler
queues
network transports
serialization
hardware interfaces
embedded runtime

Likely Python components:

cognition
model orchestration
experimentation
high-level behavior
memory strategies
development tooling

Potential long-term architecture:

          Echo
            │
     ┌──────┴──────┐
     │             │
echo-runtime    echo-brain
    Rust          Python
     │             │
signals          models
scheduler        reasoning
transport        memory
hardware         planning
     │             │
     └──────┬──────┘
            │
       Echo Console

Only migrate a Python subsystem to Rust once its architecture is stable.

A good heuristic:

If a low-level module has remained conceptually unchanged for several months and performance, memory, reliability, or embedded compatibility would benefit, consider migrating it to Rust.

⸻

26. Runtime Profiles

Echo should eventually support multiple deployment profiles.

echo-core

Minimal runtime:

Entity
Signal
Task
Action
Scheduler
State
local handlers

No model required.

⸻

echo-edge

Adds:

SQLite
hardware transports
ONNX Runtime
serial
MQTT
small models
device interfaces

Suitable for:

Raspberry Pi
Jetson
edge SBC
robot compute

⸻

echo-full

Adds:

LLM providers
OpenRouter
semantic memory
development API
Console
simulation
advanced tooling
ROS

Suitable for:

workstations
development machines
large robot compute
servers

Profiles should avoid forcing workstation dependencies onto embedded deployments.

⸻

27. Suggested Repository Layout

Use a monorepo initially.

echo/
├── README.md
├── pyproject.toml
├── docs/
├── examples/
├── tests/
│
├── src/
│   └── echo/
│       │
│       ├── core/
│       │   ├── entity.py
│       │   ├── signal.py
│       │   ├── task.py
│       │   ├── action.py
│       │   ├── scheduler.py
│       │   ├── router.py
│       │   └── runtime.py
│       │
│       ├── entity/
│       │   ├── identity.py
│       │   ├── traits.py
│       │   ├── state.py
│       │   ├── drives.py
│       │   ├── relationships.py
│       │   └── self_model.py
│       │
│       ├── state/
│       │   ├── base.py
│       │   └── sqlite.py
│       │
│       ├── providers/
│       │   ├── base.py
│       │   ├── router.py
│       │   ├── openrouter.py
│       │   ├── llamacpp_remote.py
│       │   ├── llamacpp_local.py
│       │   ├── onnx.py
│       │   └── mock.py
│       │
│       ├── medulla/
│       │   ├── base.py
│       │   ├── local.py
│       │   ├── websocket.py
│       │   ├── http.py
│       │   ├── mqtt.py
│       │   └── serial.py
│       │
│       ├── api/
│       │   ├── server.py
│       │   ├── websocket.py
│       │   └── schemas.py
│       │
│       ├── cli/
│       │   └── main.py
│       │
│       └── config/
│           ├── schema.py
│           └── loader.py
│
├── entities/
│   └── bit/
│       ├── identity.yaml
│       ├── traits.yaml
│       ├── drives.yaml
│       ├── policies.yaml
│       └── prompts/
│
├── prompts/
│   └── character_context.md
│
└── console/
    ├── package.json
    └── src/

Do not create empty abstractions merely to satisfy this directory layout.

Modules should appear only when they are actually needed.

⸻

28. API Boundary

Echo Core should expose an internal runtime API independent of HTTP.

Example:

runtime.emit(signal)
runtime.get_entity(id)
runtime.get_tasks()
runtime.get_signals()
runtime.cancel_task(id)
runtime.execute(action)
runtime.get_provider_status()
runtime.set_config(...)

FastAPI wraps this API.

The CLI wraps this API.

Tests call this API.

This prevents the web server from becoming architecture.

⸻

29. Testing Strategy

Testing should be central to the rewrite.

Unit tests

Test primitives independently.

Examples:

Signal serialization
Signal routing
Task lifecycle
Task cancellation
Action execution
Entity state updates
Provider routing
Fallback behavior

⸻

Runtime tests

Create synthetic Signals and verify resulting Tasks and Actions.

Example:

signal = BatteryLow(percent=.08)
await runtime.emit(signal)
assert entity.state["battery"] == .08
assert SpeakAction in runtime.actions

⸻

Provider fallback tests

Explicitly test:

OpenRouter available
→ OpenRouter used
OpenRouter unavailable
LAN available
→ LAN used
OpenRouter unavailable
LAN unavailable
offline available
→ offline used
all unavailable
→ structured error

⸻

Replay tests

Recorded Signals should produce deterministic runtime paths when handlers themselves are deterministic.

⸻

Character continuity tests

Explicitly test:

remote provider → LAN provider → offline provider
→ identity, relationships, memories, goals, and state remain continuous

runtime restart
→ Bit remains Bit

embodiment bit_v1 → bit_v2
→ Entity identity remains bit

oversized model-proposed trait mutation
→ rejected and audited

insignificant event
→ expires

repeated significant evidence
→ may consolidate into semantic, preference, or relationship knowledge

unexpected telemetry during an active conversation
→ attention candidate retained without automatic interruption

⸻

30. Implementation Phases

Phase 1 — Kernel

Build only:

Entity
Signal
Action
Task
Scheduler
Runtime
Handler registry

Goal:

bit = Entity("bit")
@bit.on(BatteryLow)
async def handler(signal):
    ...
await runtime.emit(BatteryLow(...))

Must work cleanly.

No LLM.

No web UI.

No ROS.

No semantic memory.

⸻

Phase 2 — Observability

Add:

structured runtime log
Signal history
Task history
Action history
state inspection
runtime status
Entity identity core
persistent trait snapshot
self-model separated from embodiment
character mutation audit vocabulary

Establish the invariant that providers cannot directly modify persistent
identity or personality. Compose the new Entity-owned value types into the
public Entity without making inference a kernel dependency.

The system should become inspectable before becoming smarter.

⸻

Phase 3 — Runtime API

Add a stable internal API for:

signals
entities
tasks
actions
state
config
providers
runtime status

Also add internal state, low-level drives, attention candidates, and explicit
APIs through which Signals may influence state and drive activation. Drives
influence attention and goal generation; they do not directly force Actions.

⸻

Phase 4 — Echo Console

Create:

FastAPI
WebSocket event stream
Svelte Console

First screens:

Overview
Signal Inspector
Task Inspector
Entity State
Logs
Chat

Add relationship models, per-person context, and social state. The Entity
Inspector must distinguish identity, traits, control state, self-model,
relationships, and embodiment rather than presenting one undifferentiated
state dictionary.

⸻

Phase 5 — Provider System

Implement:

Provider interface
ProviderRouter
OpenRouterProvider
LAN llama.cpp provider
offline llama.cpp provider
MockProvider

Default:

OpenRouter
→ LAN model server
→ offline model

Expose routing status in Console.

Build memory with distinct working, episodic, semantic, preference, and
relationship types. Provider changes must not own or erase any of them. Add
provider-continuity tests before optimizing retrieval.

⸻

Phase 6 — Configuration

Implement:

YAML/TOML config
live-safe reload
provider config
handler config
signal config
transport config

Implement memory importance scoring and consolidation. Models may propose
semantic, preference, and relationship learning; Echo validates evidence,
confidence, scope, and contradictions before persistence.

⸻

Phase 7 — State Persistence

Implement SQLite-backed persistent state.

Support runtime restart while preserving relevant Entity state.

Add the Character Context Builder and relevant retrieval of identity, traits,
state, drives, relationships, memories, goals, self-model, and environment.
The assembled cognition context and response proposal schema must be portable
across remote, LAN, and offline providers.

⸻

Phase 8 — Signal Replay

Implement:

Signal recording
Signal replay
session recording
session playback

Expose through CLI and Console.

Implement typed intentions, behavior policy, Action arbitration,
interruptibility, and low-priority autonomous curiosity goals. A thought or
attention candidate must not automatically become speech or physical action.

⸻

Phase 9 — Medulla Transports

Start with:

local
WebSocket
HTTP

Then:

MQTT
serial
ZeroMQ if justified

Add reflection, accumulated trait evidence, bounded character updates, and a
durable audit trail for accepted and rejected changes. Verify that curiosity is
expressed through attention and goals while safety and behavior policy control
external action.

⸻

Phase 10 — Edge ML

Add ONNX provider and allow Signal preprocessors.

Example:

camera frame
→ ONNX detector
→ PersonDetected
→ Echo

Begin embodiment integration. Battery, sensors, capability, and physical
condition update the self-model, resource pressure, attention, confidence,
goals, and behavior. Physical limits constrain curiosity without changing
identity.

⸻

Phase 11 — Robotics

Add optional:

ROS2 adapter
CAN
GPIO
sensor interfaces
Jetson optimization

Do not introduce ROS into Echo Core.

Verify embodiment replacement and degraded-provider continuity on real or
simulated robot hardware.

⸻

Phase 12 — Advanced Task Behavior

Add:

task dependencies
parent/child tasks
interruptions
resume
behavior graphs
planning

Use ideas from LangGraph where valuable, but do not require LangGraph.

⸻

31. Things Explicitly Not To Do

Do not:

* make an LLM mandatory
* use LangChain as the core runtime
* use ROS as the core runtime
* rebuild everything around chat turns
* make Medulla responsible for reasoning
* build semantic memory before basic state works
* send raw sensor data directly to an LLM by default
* implement arbitrary hot Python code replacement early
* build a custom DSL before Actions are stable
* migrate to Rust prematurely
* introduce distributed infrastructure before local Signals work
* create abstractions only because they may be useful someday
* force desktop dependencies onto edge builds
* implement Bit primarily as a giant system prompt
* store every conversation forever
* allow models to directly change personality or persistent memory
* reduce internal state to a single mood string
* assume every generated thought becomes speech or action
* require every autonomous goal to have immediate user utility
* let curiosity override safety, priorities, capability, or resources
* couple identity to an inference provider or embodiment
* hard-code Bit-specific behavior into generic Echo runtime modules

⸻

32. Coding Principles

Favor:

explicit > magical
composition > inheritance
small interfaces > giant abstractions
typed data > loosely structured dictionaries
observable state > hidden state
structured concurrency > background chaos
provider interfaces > hard-coded dependencies
events/signals > polling where practical

When creating a new abstraction, ask:

Does Echo require this concept, or is this merely one implementation of an existing concept?

If it is merely an implementation, keep it outside the core.

⸻

33. Architectural Invariants

These should be treated almost as tests for the design.

Invariant 1

Echo runs without an LLM.

Invariant 2

A Signal can originate anywhere without changing Entity handler code.

Invariant 3

The Entity is the persistent intelligent actor.

Invariant 4

All meaningful runtime activity should be observable.

Invariant 5

Models are replaceable providers.

Invariant 6

Medulla transports information and Actions but does not make cognitive decisions.

Invariant 7

Chat is simply another Signal source.

Invariant 8

A workstation and a robot should use the same core conceptual model.

Invariant 9

Heavy features are optional.

Invariant 10

The central Echo runtime should remain understandable.

Invariant 11

The Entity owns identity, relationships, memories, goals, preferences, and
persistent character.

Invariant 12

Inference output is an untrusted proposal and cannot directly mutate persistent
character or authorize an Action.

Invariant 13

Identity survives inference-provider and embodiment changes.

Invariant 14

Character influences attention, memory, goals, and behavior policy rather than
existing only as prompt prose.

Invariant 15

Persistent character changes are bounded, evidence-based, and auditable.

Invariant 16

Reference Entity configuration does not leak into generic framework policy.

⸻

34. First Milestone

The first meaningful milestone is not “Echo can chat.”

It is:

Echo can run a persistent Entity, accept Signals, update state, spawn Tasks, create Actions, expose everything live through the Console, and survive runtime restarts while preserving appropriate state.

For Bit, “appropriate state” includes identity, traits, relationships, selected
memories, goals, preferences, and self-model continuity. The same saved Entity
must reconstruct across OpenRouter, LAN, and offline inference providers.

Demo scenario:

1. Start Echo.
2. Open Echo Console.
3. Entity "bit" appears RUNNING.
4. Inject:
   battery.low percent=.12
5. Signal appears live.
6. Signal is routed to handler.
7. Entity state updates.
8. Task is created.
9. Task creates SpeakAction.
10. Action appears in inspector.
11. Inject UserMessage:
    "How much battery do you have?"
12. Echo requests inference.
13. ProviderRouter chooses OpenRouter.
14. Disconnect Internet.
15. Repeat request.
16. Echo automatically selects LAN model server.
17. Disable LAN server.
18. Repeat request.
19. Echo automatically uses offline model.
20. Restart Echo from Console.
21. Persistent Entity state returns.
22. Replay the original BatteryLow Signal.
23. Observe the runtime path again.

If this demonstration works cleanly, the foundation is strong enough to begin building robotics intelligence on top.

⸻

35. Final Direction

Echo should evolve toward this structure:

              OUTSIDE WORLD
                    │
                    ▼
                 MEDULLA
                    │
                    ▼
                 SIGNALS
                    │
                    ▼
              ┌──────────┐
              │  ENTITY  │
              └────┬─────┘
                   │
          ┌────────┴─────────┐
          ▼                  ▼
        TASKS              STATE
          │
          ▼
       ACTIONS
          │
          ▼
        MEDULLA
          │
          ▼
       REAL WORLD

Optional intelligence surrounds rather than replaces this loop:

                 ENTITY
                   │
       ┌───────────┼────────────┐
       │           │            │
       ▼           ▼            ▼
     Rules       ONNX          LLM
                              Router
                                │
                     ┌──────────┼──────────┐
                     ▼          ▼          ▼
                OpenRouter     LAN      Offline

The long-term goal is not to create an LLM wrapper.

The goal is to create a small runtime in which a persistent intelligent Entity can perceive events, maintain state, organize work, invoke whichever intelligence resources are available, and act on the physical or digital world.

That runtime should eventually be capable of operating everywhere from a development laptop to Bit.

Echo supplies continuity. Experience supplies history. Memory supplies
learning. Relationships supply social context. The body supplies physical
reality. The model supplies cognition. Over time, those systems together
produce Bit.
