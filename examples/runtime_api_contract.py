"""Executable Phase 3 runtime API contract example."""

from __future__ import annotations

import asyncio
import json

from echo import (
    ApplySignalInfluenceRequest,
    AttentionProposal,
    DeveloperCommandDispatcher,
    DriveProfile,
    Entity,
    Runtime,
    RuntimeService,
    RuntimeServiceProtocol,
    Signal,
    SignalInfluence,
    SignalInjectCommand,
    StateSetCommand,
)


async def run_example() -> dict[str, object]:
    bit = Entity(
        "bit",
        state={"mode": "idle"},
        drives=DriveProfile({"curiosity": 0.8}),
    )
    runtime = Runtime([bit])
    service: RuntimeServiceProtocol = RuntimeService(
        runtime,
        allowed_state_keys={"bit": {"mode"}},
    )
    commands = DeveloperCommandDispatcher(service)
    subscription = service.subscribe_events()

    @bit.on("developer.ping")
    async def ping(signal: Signal):
        return await bit.action("acknowledge", value=signal.payload["value"])

    emitted = await commands.execute(
        SignalInjectCommand(
            signal_type="developer.ping",
            payload={"value": 7},
        )
    )
    await commands.execute(
        StateSetCommand(entity_id="bit", values={"mode": "ready"})
    )
    influence = service.apply_signal_influence(
        ApplySignalInfluenceRequest(
            entity_id="bit",
            signal_id=emitted.data["id"],
            influence=SignalInfluence(
                drive_deltas={"curiosity": 0.6},
                attention=AttentionProposal(
                    subject="developer ping",
                    reason="explicit contract example",
                    salience=0.4,
                    drive_names=("curiosity",),
                ),
            ),
        )
    )
    first_event = await subscription.get()
    status = service.get_runtime_status()
    actions = service.get_actions()
    state = service.get_entity_state("bit")
    subscription.close()
    runtime.stop()

    assert influence.attention_candidate is not None
    return {
        "status": status.status,
        "action": actions[0].type,
        "state": state.values,
        "attention": influence.attention_candidate.subject,
        "first_event": first_event.category.value,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_example()), sort_keys=True))

