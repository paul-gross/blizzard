"""Flows — each branch's delivery line, and the visits standing in it.

A flow is one branch a hub node lands on, named by its resource ``repo:<repository_id>/<branch>``. A visit to a hub node
that names an action is submitted to the flows of the branches it lands on, in the tenant's arrival order, and closed
once it leaves the node. Whether a submission is open, or a flow busy, is derived from the rows
(``bzh:facts-not-status``), never stored."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import ChunkFacts, HubNodePollFact
from blizzard.hub.domain.config.repositories import RepositoryResolution
from blizzard.hub.domain.graph.actions import ActionSpec
from blizzard.hub.domain.graph.model import Node

#: Why a submission left its flows: the lever that ended its chunk, or any other move off the visit.
type Withdrawal = Literal["stopped", "restarted", "completed", "superseded"]
#: What a flow's record says happened.
type FlowFactKind = Literal["submitted", "turn-started", "turn-ended", "landed", "withdrawn"]
#: The outcome of a turn that ended without its pass's answer.
TURN_INTERRUPTED = "interrupted"
#: The outcome of a turn its driver ended at the pass's deadline or the build's bound, routing nothing.
TURN_CUT_OFF = "cut-off"
#: The outcome of a turn that handed its chunk to a person — never a verdict.
TURN_ESCALATED = "escalated"
#: The outcome of a turn that found its visit's resources moved under it, or its visit already left.
TURN_SUPERSEDED = "superseded"
#: How many turns in a row cut off send a visit to a person on its next.
STUCK_AFTER = 3

# Pending-poll cadence defaults, overridable per node.
DEFAULT_POLL_INTERVAL = timedelta(seconds=30)
DEFAULT_POLL_TIMEOUT = timedelta(hours=3)
#: The longest a tenant's full reconcile is ever skipped, whatever its change probe says.
RECONCILE_FLOOR = timedelta(minutes=1)
#: The statuses in which a person holds a chunk: a visit they hold at a later epoch keeps its place.
_HELD_BY_A_PERSON = frozenset({ChunkStatus.NEEDS_HUMAN, ChunkStatus.PAUSED})


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
class TurnEvent:
    """The event a turn's end records in the transaction that ends it, about the turn's chunk at its node."""

    kind: EventLogKind
    chunk_id: str | None
    node_name: str | None
    message: str
    detail: Mapping[str, object]


@domain_model
@dataclass(frozen=True)
class FlowLanding:
    """A merge a turn recorded, as its flow's record says it: the ``landed`` fact on ``flow`` for ``submission_id``
    and ``chunk_id``, and ``event``, the ``flow-landed`` row written beside it."""

    flow: Flow
    submission_id: str
    chunk_id: str
    event: TurnEvent

    @classmethod
    def of(
        cls, flow: Flow, submission: Submission, *, project_id: str | None, node_name: str, revision: str
    ) -> FlowLanding:
        """The landing of ``submission``'s chunk on ``flow`` at ``revision``. ``members`` lists every chunk the landing
        carried — one, until a landing carries several."""
        chunk_id = submission.chunk_id
        return cls(
            flow=flow,
            submission_id=submission.submission_id,
            chunk_id=chunk_id,
            event=TurnEvent(
                kind="flow-landed",
                chunk_id=chunk_id,
                node_name=node_name,
                message=f"landed on {flow.branch} at {revision}",
                detail={
                    "flow_id": flow.flow_id,
                    "resource": flow.resource,
                    "repository_id": flow.repository_id,
                    "branch": flow.branch,
                    "revision": revision,
                    "chunk_id": chunk_id,
                    "project_id": project_id,
                    "members": [{"chunk_id": chunk_id, "project_id": project_id}],
                },
            ),
        )


@domain_model
@dataclass(frozen=True)
class LandingWrite:
    """What recording a turn's merge did: whether the fence would have refused it, and the ``flow-landed`` event row
    it wrote — ``None`` for a replay that found the marker written."""

    refused: bool
    event_id: int | None


@domain_model
@dataclass(frozen=True)
class AdmittedTurn:
    """A turn the tick started, and the submission it is a turn of."""

    turn: Turn
    submission: Submission


def is_stuck(outcomes: Sequence[str]) -> bool:
    """Whether a visit whose ended turns answered ``outcomes``, newest first, is stuck: its :data:`STUCK_AFTER` newest
    turns not ``interrupted`` all ended ``cut-off``. An interrupted turn neither counts nor resets the run."""
    counted = [outcome for outcome in outcomes if outcome != TURN_INTERRUPTED][:STUCK_AFTER]
    return len(counted) == STUCK_AFTER and all(outcome == TURN_CUT_OFF for outcome in counted)


def resources_moved(held: Sequence[FlowResource], resolved: Sequence[FlowResource], submitted: Sequence[str]) -> bool:
    """Whether a visit's resources moved under its submission: a resource still to land (``held``) it does not hold,
    or one it holds (``submitted``) the visit no longer resolves to (``resolved``, landed or not). A repository landed
    earlier in the visit keeps its flow until the verdict."""
    holds = set(submitted)
    return not {r.resource for r in held} <= holds or not holds <= {r.resource for r in resolved}


@domain_model
@dataclass(frozen=True)
class VisitStanding:
    """Where one visit's newest submission stands: ``turning`` while it has an open turn, and ``ahead`` the number of
    open submissions that arrived before it on each flow it holds, in ``submission.flows`` order."""

    submission: Submission
    turning: bool
    ahead: tuple[int, ...]


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


def held_resources(
    resolution: RepositoryResolution, landed: frozenset[str], spec: ActionSpec
) -> tuple[FlowResource, ...]:
    """The flows a visit holds: one per resolved repository still to land, on that record's base branch. A target
    is left out once its ``owner/repo`` is among ``landed`` (``LandedRepos.names``), and an action that does not land
    holds nothing."""
    if not spec.lands:
        return ()
    held: dict[FlowResource, None] = {}
    for repo, target in zip(resolution.qualified, resolution.targets, strict=True):
        if repo not in landed:
            held.setdefault(FlowResource(target.record, target.base_branch))
    return tuple(held)


def _stands_at(facts: ChunkFacts, submission: Submission) -> bool:
    """The chunk's newest movement enters the submission's node."""
    movement = facts.latest_movement()
    return movement is not None and movement.node_id == submission.node_id


def is_current(facts: ChunkFacts, submission: Submission) -> bool:
    """The submission's visit is the chunk's current one: it stands at the node, at the submission's epoch."""
    return _stands_at(facts, submission) and (facts.latest_epoch() or 0) == submission.epoch


def held_by_a_person(facts: ChunkFacts, submission: Submission) -> bool:
    """A person holds the chunk at the submission's node in a later epoch — escalated there, or paused there."""
    return (
        _stands_at(facts, submission)
        and (facts.latest_epoch() or 0) > submission.epoch
        and facts.status() in _HELD_BY_A_PERSON
    )


def withdrawal_for(facts: ChunkFacts, submission: Submission) -> Withdrawal | None:
    """Why ``submission`` leaves its flows, or ``None`` while it keeps its place — its visit current, or a person
    holding its chunk there at a later epoch. ``stopped`` or ``completed`` for a chunk ended by that lever,
    ``restarted`` for one restarted past the visit, ``superseded`` for any other move off it. The only judge of a
    closure's outcome: every writer that withdraws a submission asks here."""
    status = facts.status()
    if status is ChunkStatus.STOPPED:
        return "stopped"
    if status is ChunkStatus.DONE and facts.operator_completed:
        return "completed"
    if is_current(facts, submission) or held_by_a_person(facts, submission):
        return None
    if facts.restarted_past(submission.epoch):
        return "restarted"
    return "superseded"


def last_moved_at(facts: ChunkFacts) -> datetime | None:
    """The newest instant the chunk moved in a way its line converges on: a movement, a stop or completion, an
    escalation, a requeue, or a pause or resume. ``None`` while it has none."""
    instants = [
        *(t.recorded_at for t in facts.transitions),
        *(m.recorded_at for m in facts.migrations),
        *(r.recorded_at for r in facts.restarts),
        *(e.recorded_at for e in facts.escalations),
        *(q.requeued_at for q in facts.requeues),
        *(p.set_at for p in facts.pauses),
        *(at for at in (facts.stopped_at, facts.operator_completed_at) if at is not None),
    ]
    return max(instants, default=None)


@domain_model
@dataclass(frozen=True)
class FlowLine:
    """A tenant's open submissions judged against where their chunks stand: which are eligible, and which are next.
    ``facts`` is each submission's chunk's, by chunk id; ``policies`` each submission's node's poll cadence, by
    submission id; ``turning`` the submissions with an open turn."""

    submissions: tuple[Submission, ...]
    facts: Mapping[str, ChunkFacts]
    policies: Mapping[str, PollPolicy]
    turning: frozenset[str] = frozenset()

    def eligible(self, submission: Submission, *, now: datetime) -> bool:
        """Its chunk is ``delivering``, its visit is current and has no open turn, and its next poll is due: no
        poll row for the visit, or ``PollPolicy.due_at`` of the newest one has passed."""
        facts = self.facts[submission.chunk_id]
        if facts.status() is not ChunkStatus.DELIVERING or not is_current(facts, submission):
            return False
        if submission.submission_id in self.turning:
            return False
        polls = facts.hub_node_poll_history(node_id=submission.node_id, epoch=submission.epoch)
        return not polls or self.policies[submission.submission_id].due_at(polls[-1]) <= now

    def is_next(self, submission: Submission, *, now: datetime) -> bool:
        """It is eligible, and on every flow it holds no eligible submission arrived before it. A visit holding
        nothing is next whenever it is eligible."""
        if not self.eligible(submission, now=now):
            return False
        held = {flow.flow_id for flow in submission.flows}
        return not any(
            other.arrival < submission.arrival
            and held.intersection(flow.flow_id for flow in other.flows)
            and self.eligible(other, now=now)
            for other in self.submissions
        )
