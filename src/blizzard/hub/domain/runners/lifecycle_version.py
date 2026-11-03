"""The lifecycle versions a write decided on a host or a runner carries — shared by the registry and route seams."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class HostLifecycleVersion:
    """A host's lifecycle version as a decision read it: the id of its newest lifecycle fact, ``0`` for a host with
    none. A write decided on the host lands only while the host still stands at it."""

    host_id: str
    version: int


@domain_model
@dataclass(frozen=True)
class RunnerLifecycleVersion:
    """A runner's lifecycle version as a decision read it, or as the decision's own fact left it: the id of its newest
    lifecycle fact, ``0`` for a runner with none. A write decided on the runner lands only while it still stands at
    it."""

    runner_id: str
    version: int
