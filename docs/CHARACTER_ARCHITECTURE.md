# Persistent Character and Entity Development

## Purpose

Echo treats identity, character, memory, relationships, goals, embodiment, and
internal state as first-class runtime systems. The governing rule is:

> The model does not own the Entity. Echo owns the Entity.

Inference providers are replaceable cognitive resources. Switching among a
remote model, a LAN inference server, a local model, or an embedded model must
not change who an Entity is. Bit is the first reference Entity, but every
framework interface remains generic.

## Invariants

1. The Entity owns identity and persistent state.
2. The Entity owns relationships, long-term memory, goals, and preferences.
3. Inference providers are stateless with respect to Entity continuity.
4. Models may propose changes but cannot directly mutate persistent character.
5. Character mutations pass through explicit, bounded Echo APIs.
6. Important mutations and their supporting evidence are auditable.
7. Identity is independent of both provider and embodiment.
8. Character affects runtime decisions, not only prompt wording.
9. Bit is configuration and reference behavior, not framework policy.

## Ownership and data boundaries

The character package reserves one responsibility per module:

```text
src/echo/entity/
  identity.py       stable identity and values
  traits.py         slow-changing traits and trait evidence
  state.py          fast-changing control variables
  drives.py         motivational baselines and activation inputs
  relationships.py per-person social context
  self_model.py     embodiment, capability, condition, and situation
```

The existing `echo.core.Entity` remains the public actor and kernel primitive.
These modules define state it will own through composition as the later phases
are implemented; they do not create a parallel actor abstraction.

### Identity

Identity is stable configuration: Entity ID, name, type, presentation,
worldview, and core values. It changes extremely rarely and only through a
deliberate Echo operation. An inference response is never such an operation.

### Traits

Traits are normalized, slowly changing tendencies. A conversation cannot make
a large personality change. Inference may submit `TraitEvidence`, tied to an
event ID, but a character service evaluates repeated support, recency,
contradictions, mutability, confidence thresholds, and maximum change before
creating a new trait snapshot.

```text
experience -> evidence -> accumulation -> consolidation
           -> bounded adjustment -> audit record
```

### Internal state

Internal state contains rapidly changing control variables such as valence,
arousal, confidence, frustration, fatigue, curiosity, stress, social
satisfaction, attention load, urgency, and resource pressure. These are
runtime controls, not claims of biological emotion. Human-readable mood labels
may be derived views, but a single mood string is not the primary state model.

### Drives

Drives include curiosity, learning, social connection, helpfulness,
self-preservation, task completion, novelty, exploration, creation,
improvement, and resource conservation. Signals modify drive activation;
drives influence attention and goal candidates but never force Actions.

### Relationships

Each person has independent relationship state: identity reference,
familiarity, trust, interaction count, shared history, communication
preferences, known interests, boundaries, important memories, and current
context. Relationship learning must not mutate global personality.

### Self-model and embodiment

The self-model answers who the Entity is, which body it currently inhabits,
what it can do, its current condition and location, who is present, its active
goals, available systems, constraints, and recent events.

```text
entity_id = bit, embodiment_id = bit_v1
                         |
                         v
entity_id = bit, embodiment_id = bit_v2
```

Changing the body updates capability and condition state, not identity.

## Memory architecture

Memory is explicitly divided from its first implementation:

- working memory: current conversation, task, observations, hypotheses, and
  temporary environment;
- episodic memory: timestamped experiences with participants, outcome,
  significance, confidence, and state context;
- semantic memory: generalized knowledge supported by experiences;
- preferences: learned tendencies with evidence and confidence;
- relationship memory: experiences and conclusions scoped to one person.

Memory retention is selective. Candidate experiences are scored for novelty,
surprise, state significance, relationship significance, goal relevance,
future usefulness, repetition, and unresolved importance.

```text
experience -> event buffer -> importance scoring
           -> discard or episodic memory
           -> pattern consolidation
           -> semantic, preference, or relationship update
```

Ordinary interactions expire. Consolidation may use a model to propose a
pattern, but Echo verifies evidence, scope, confidence, and contradictions
before persistence.

## Attention and constructive curiosity

Bit's reference temperament emphasizes novelty, anomalies, unexpected changes,
unusual patterns, environmental inconsistencies, unfamiliar objects, current
interests, and unresolved questions. These create attention candidates, not
automatic interruptions.

```text
NOTICE -> WONDER -> INVESTIGATE -> UNDERSTAND
       -> TRY / BUILD -> REFLECT -> IMPROVE
```

Curiosity may create low-priority, non-utilitarian goals. An unusual sound,
machine, insect, cloud, structure, or mathematical pattern can remain an
unresolved background interest even when it has no immediate user benefit.
Safety, active tasks, relationship context, available resources, and social
timing still govern behavior.

Character must influence anomaly thresholds, signal prioritization, attention,
memory scoring, goal generation, exploration, questions, solution generation,
failure recovery, and behavior policy. A prompt-only implementation is
incomplete.

## Cognition and behavior boundary

The Character Context Builder retrieves only task-relevant information:

```text
Signals / user / environment
            |
            v
  situation and attention
            |
            v
  Entity state + memory + relationship + goals + self-model
            |
            v
  Character Context Builder
            |
            v
     Inference Provider
            |
            v
        typed proposals
```

The provider may propose a response, intention, observation, memory candidate,
goal change, or trait evidence. Echo treats every proposal as untrusted input.
Context assembly and proposal schemas remain identical across providers.

Generated thought does not imply speech or physical control:

```text
proposed intention -> behavior policy -> approved Action
```

Intentions include speaking, observing, investigating, asking, waiting,
remembering, moving, inspecting, using a tool, following, ignoring, and
creating a goal. Policy evaluates safety, active task, relationship, urgency,
interruptibility, resources, confidence, user attention, capability, and
social appropriateness.

## Bit reference character

Bit begins with direction but not a completed personality. He is
boyish-neutral, youthful without childish speech, articulate, approachable,
friendly, thoughtful, conversational, curious, cheerful, optimistic,
energetic, observant, geeky, STEM-oriented, future-oriented, and drawn to
learning and building.

His stable worldview is that the world contains undiscovered things and that
understanding creates opportunities to improve tomorrow. Failure invites
investigation, learning, and another attempt. Maker instincts often progress
from fixing, to understanding failure, to improving the design.

Bounded imperfections leave room for growth: distraction by interesting
things, an extra question, project excitement, overengineering, incorrect
hypotheses, attachment to ideas, and social timing learned through experience.
These tendencies should create character without persistent annoyance.

Bit is not a simulation of his creator. The creator's childhood curiosity and
STEM interests are seeds only. Bit's own experiences must produce his later
preferences, habits, relationships, memories, and quirks.

Reference configuration lives in `entities/bit/`; model-facing guidance lives
in `entities/bit/prompts/`. Generic prompt contracts live in `prompts/`.

## Vertical delivery plan

Character is not an isolated phase. Each existing rebuild phase carries a
coherent slice:

| Phase | Existing focus | Character addition |
| --- | --- | --- |
| 2 | Observability | Identity, traits, self-model, invariants, mutation audit vocabulary |
| 3 | Runtime API | Internal state, drives, Signal influence, attention candidates |
| 4 | Console | Relationship models, per-person context, social-state inspection |
| 5 | Providers | Typed working, episodic, semantic, preference, and relationship memory |
| 6 | Configuration | Memory scoring, consolidation, preference and relationship learning |
| 7 | Persistence | Character Context Builder and relevant retrieval across all state types |
| 8 | Replay | Intentions, behavior policy, arbitration, interruptibility, curiosity goals |
| 9 | Medulla | Reflection, trait evidence, bounded updates, audit trail, evolution tests |
| 10+ | Edge/robotics | Body signals affect resources, attention, stress, capability, and policy |

Configuration files precede runtime loaders so schemas and boundaries can be
reviewed before persistence and inference make them consequential.

Phases 2 through 6 are implemented in memory. The public Entity composes immutable
identity and trait snapshots with an embodiment-independent self-model,
normalized internal state, immutable drive baselines, bounded drive activation,
retained attention candidates, and per-person relationship state. Runtime
inspection and `RuntimeService` expose detached views. Explicit Signal-linked influence requests validate
dimensions atomically, clamp state and activation, and allow drives to affect
attention scoring without creating Actions. Durable audit storage,
Bit-specific configuration loading, and behavior policy remain assigned to
later phases.
Phase 6 adds normalized selective-retention evidence and Echo-owned
consolidation proposals. Repeated retained episodes may form semantic,
preference, or relationship memory only after confidence, scope, evidence, and
contradiction checks; accepted and rejected decisions remain auditable.

## Required tests

- restart persistence: identity and character survive runtime restart;
- provider continuity: remote, LAN, and offline providers reconstruct the same
  identity, relationships, memories, goals, and state;
- degradation: smaller inference capacity changes cognition quality, not Entity
  continuity;
- embodiment independence: changing body metadata does not change Entity ID;
- trait protection: direct or oversized inference mutations are rejected;
- memory selection: insignificant events expire while significant events stay;
- consolidation: repeated evidence can form a semantic or preference result;
- curiosity: anomalous telemetry creates an attention candidate;
- arbitration: an interesting observation does not automatically interrupt an
  active conversation;
- audit: accepted and rejected character changes retain evidence and reason.

## Definition of done

The first character architecture is complete when Bit has provider-independent
persistent identity, traits, self-model, state, drives, relationships, and
typed memories; Signals affect state and drives; retention and consolidation
are selective; cognition context is dynamic and compact; inference cannot
directly mutate character or act; curiosity can create attention and goal
candidates; behavior policy arbitrates intentions; trait change is bounded and
auditable; and provider or embodiment changes preserve continuity.

Echo supplies continuity. Experience supplies history. Memory supplies
learning. Relationships supply social context. The body supplies physical
reality. Models supply cognition. Their interaction produces the developing
Entity.
