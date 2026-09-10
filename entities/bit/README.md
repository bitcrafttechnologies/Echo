# Bit reference Entity

Bit is Echo's first reference Entity, not a special runtime mode. These files
contain seed identity, trait, drive, policy, and cognition guidance. Loaders and
durable state are separate concerns: the configured host loads the identity,
traits, drives, and character guidance at startup, while learned state remains
owned by Echo's state and memory systems.

The checked-in values are initial tuning, not a finished personality. Learned
preferences, relationships, memories, and quirks belong in future Echo-managed
persistence and must not be written back by an inference provider.
