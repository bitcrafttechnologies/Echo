"""Process-local lifecycle state without discovery or connection semantics."""

from enum import StrEnum


class MedullaNodeLifecycle(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


NodeLifecycle = MedullaNodeLifecycle

__all__ = ["MedullaNodeLifecycle", "NodeLifecycle"]
