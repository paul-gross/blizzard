"""Execution observation — what a harness can see of one lease's own current execution.

The runner samples this seam about once a minute for each live lease and reads only the
:class:`ExecutionObservation` it answers: stream event types, server part states and version
gates stay inside the adapter that reads them (``bzh:pluggable-seams``). An adapter that cannot
tie what it sees to the requested session and generation answers ``unknown``, never ``alive``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.identity import SessionReference

__all__ = [
    "ExecutionActivity",
    "ExecutionLiveness",
    "ExecutionObservation",
    "ExecutionObservationRequest",
    "IHarnessExecutionObservation",
]


class ExecutionLiveness(StrEnum):
    """Whether the harness reports the execution running."""

    ALIVE = "alive"
    EXITED = "exited"
    UNKNOWN = "unknown"


class ExecutionActivity(StrEnum):
    """What the running execution is doing, as far as the harness can tell."""

    MODEL = "model"
    TOOL = "tool"
    WAITING = "waiting"
    UNKNOWN = "unknown"


@domain_model
@dataclass(frozen=True)
class ExecutionObservationRequest:
    """One lease's current execution, as the runner hands it to the adapter: the session it
    runs, its spawn generation, that generation's own worker-stdout file (``""`` when the
    runner redirects none), and the instant the runner asks at — an adapter whose evidence is a
    write time bounds that write's freshness against it."""

    session: SessionReference
    generation: int
    stdout_path: str
    now: datetime


@domain_model
@dataclass(frozen=True)
class ExecutionObservation:
    """The adapter's answer: liveness, activity, when its evidence was actually produced, the
    newest substantive progress it can see (``None`` when it sees none), and a short diagnostic."""

    liveness: ExecutionLiveness
    activity: ExecutionActivity
    evidence_at: datetime | None = None
    progress_at: datetime | None = None
    diagnostic: str = ""

    @classmethod
    def unknown(cls, diagnostic: str) -> ExecutionObservation:
        """No evidence either way — what an adapter answers whenever it cannot tie what it sees
        to the requested execution."""
        return cls(liveness=ExecutionLiveness.UNKNOWN, activity=ExecutionActivity.UNKNOWN, diagnostic=diagnostic)


class IHarnessExecutionObservation(Protocol):
    """The execution-observation slice: one bounded look at one lease's current execution."""

    def observe_execution(self, request: ExecutionObservationRequest) -> ExecutionObservation:
        """What the harness can see of ``request``'s execution. Never raises for an absent or
        unreadable source — that is ``unknown``."""
        ...
