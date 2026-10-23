"""Why the hub answers a runner's release ``409`` — the vocabulary the hub and its runners share."""

from __future__ import annotations

from enum import StrEnum


class ReleaseRefusalReason(StrEnum):
    #: The chunk has moved past the epoch the runner last saw it at.
    STALE = "stale"
    #: The chunk's derived status is not ``running``: it holds no step a release could give back.
    NOT_RUNNING = "not-running"
