"""Flows — each branch's delivery line, and the visits standing in it.

A flow is one branch a hub node lands on, named by its resource ``repo:<repository_id>/<branch>``. A visit to a hub node
that names an action is submitted to the flows of the branches it lands on, in the tenant's arrival order, and closed
once it leaves the node. Whether a submission is open, or a flow busy, is derived from the rows
(``bzh:facts-not-status``), never stored."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import HubNodePollFact
from blizzard.hub.domain.graph.model import Node

#: Why a submission left its flows: the lever that ended its chunk, or any other move off the visit.
type Withdrawal = Literal["stopped", "restarted", "completed", "superseded"]
#: What a flow's record says happened.
type FlowFactKind = Literal["submitted", "turn-started", "turn-ended", "landed", "withdrawn"]
#: The outcome of a turn that ended without its pass's answer.
TURN_INTERRUPTED = "interrupted"

# Pending-poll cadence defaults, overridable per node.
DEFAULT_POLL_INTERVAL = timedelta(seconds=30)
DEFAULT_POLL_TIMEOUT = timedelta(hours=3)


@domain_model
@dataclass(frozen=True)
class FlowResource:
    """The branch a visit lands on: a repository record and the branch of it."""

    repository_id: str
    branch: str

    @property
    def resource(self) -> str:
        return f"repo:{self.repository_id}/{self.branch}"


@domain_model
@dataclass(frozen=True)
class Flow:
    """One branch's delivery line, made by the first submission to its resource."""

    flow_id: str
    repository_id: str
    branch: str
    created_at: datetime

    @property
    def resource(self) -> str:
        return FlowResource(self.repository_id, self.branch).resource


@domain_model
@dataclass(frozen=True)
class Submission:
    """One hub-node visit's place in the flows it holds — none for a visit that holds nothing.
    ``closed`` is the closure's outcome, ``None`` while the submission is open."""

    submission_id: str
    chunk_id: str
    node_id: str
    epoch: int
    generation: int
    arrival: int
    submitted_at: datetime
    flows: tuple[Flow, ...]
    closed: str | None = None


@domain_model
@dataclass(frozen=True)
class Turn:
    """One turn a submission takes; open while ``ended_at`` is ``None``."""

    turn_id: str
    submission_id: str
    started_at: datetime
    ended_at: datetime | None = None
    outcome: str | None = None


@domain_model
@dataclass(frozen=True)
class FlowFact:
    """One entry in a flow's append-only record."""

    flow_id: str
    submission_id: str
    chunk_id: str
    kind: FlowFactKind
    at: datetime
    outcome: str | None = None
    revision: str | None = None


@domain_model
@dataclass(frozen=True)
class PollPolicy:
    """A hub command node's pending-poll cadence and give-up bound."""

    interval: timedelta = DEFAULT_POLL_INTERVAL
    timeout: timedelta = DEFAULT_POLL_TIMEOUT

    @classmethod
    def of(cls, node: Node) -> PollPolicy:
        """The node's own overrides, else the module defaults."""
        return cls(
            interval=(
                timedelta(seconds=node.poll_interval_seconds)
                if node.poll_interval_seconds is not None
                else DEFAULT_POLL_INTERVAL
            ),
            timeout=(
                timedelta(seconds=node.poll_timeout_seconds)
                if node.poll_timeout_seconds is not None
                else DEFAULT_POLL_TIMEOUT
            ),
        )

    def due_at(self, row: HubNodePollFact) -> datetime:
        """When a visit waiting since ``row`` is next due: the later of its interval elapsing and
        the row's own ``next_poll_at``, which only ever postpones. Every reader of due-ness asks here."""
        due = row.polled_at + self.interval
        return max(due, row.next_poll_at) if row.next_poll_at is not None else due
