# Character context contract

The Character Context Builder should render a compact, task-relevant context
from the following independently owned inputs:

1. identity core;
2. relevant trait snapshot;
3. current internal control state;
4. active drives;
5. relationship context for current participants;
6. retrieved memories and preferences;
7. active goals;
8. self-model and embodiment condition;
9. current environment and task constraints.

The inference provider returns a response plus optional typed proposals for
intentions, observations, memories, goals, and trait evidence. All proposals
are untrusted until the corresponding Echo subsystem validates them. Context
must remain portable across remote, LAN, and offline providers.
