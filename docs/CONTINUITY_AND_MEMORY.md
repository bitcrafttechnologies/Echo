# Phase 9 Stabilization Gate — Continuity, Context & Memory

Echo separates current dialogue from persistent Entity memory. These classes must
not be treated as interchangeable:

- **Immediate context** is the token-budgeted, session-scoped dialogue window. It is
  reliably included in every normal cognition request and is the first source for
  resolving pronouns, confirmations, and follow-ups. It is cleared when a new
  handler/session is constructed.
- **Episodic memory** records what occurred in interactions. It is durable and is
  ranked strongly for questions about recent or past conversations.
- **Semantic / durable facts** are stable participant facts and preferences. They
  carry confidence and provenance. Explicit user facts outrank observations and
  inferences.
- **Character memory** is durable evidence that can affect Entity identity or
  development. Existing reflection policy still requires repeated, high-quality
  evidence before consolidation.
- **Runtime / telemetry state** describes the environment now. Battery, health,
  clock, location, and similar readings are transient and are not converted into
  semantic or episodic records by the chat path.

## Provenance

New memory records distinguish `user_explicit`, `observed`, `inferred`, and
`system_configured` evidence. Legacy provenance values remain readable so an
existing SQLite database does not require a destructive migration. Direct
experience and reflection remain explicit provenance categories for episodes and
character development.

## Session and restart semantics

Same-session turns retain immediate context and pending intentions. A new session
or full process restart may clear both. Episodic, semantic, and character records
live in the Entity SQLite database and survive process replacement or application
reinstallation when that database is preserved. A fresh database represents a
fresh Entity memory state.

## Inspection

Each `EchoResponse` action includes a `cognition_trace` with immediate-context,
retrieval, candidate, persistence, capability-selection, and action-result fields.
The inference context also includes `memory_retrieval_trace`. Developers can run:

```text
memory query <entity-id> "<query>" [limit]
```

The result shows candidate records and deterministic scores, selected IDs, and
the exact records eligible for context injection. Candidate classification and
commit decisions are included in the response action trace. These are data and
policy decisions, not hidden model reasoning.
