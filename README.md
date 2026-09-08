# Echo

Echo is a lightweight, persistent, event-driven runtime for intelligent
entities. The implementation currently targets the kernel milestone described
in `Echo_Plan.md` and intentionally has no LLM, web, ROS, or semantic-memory
dependency.

## Development

Echo requires Python 3.11 or newer and uses only the standard library at
runtime.

```console
python -m unittest discover -s tests -v
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
