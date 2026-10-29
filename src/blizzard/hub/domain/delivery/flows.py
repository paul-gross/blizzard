"""Flows — each branch's delivery line, and the visits standing in it.

A flow is one branch a hub node lands on, named by its resource ``repo:<repository_id>/<branch>``. A visit to a hub node
that names an action is submitted to the flows of the branches it lands on, in the tenant's arrival order, and closed
once it leaves the node. Whether a submission is open, or a flow busy, is derived from the rows
(``bzh:facts-not-status``), never stored."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Literal, get_args

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.hub_event_types import FlowChangeKind
from blizzard.foundation.roles import domain_model
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.chunk.flow_writes import FlowFactWritten
from blizzard.hub.domain.chunk.model import ChunkFacts, HubNodePollFact
from blizzard.hub.domain.config.landing_policies import DEFAULT_LANDING_POLICY, LandingPolicy
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryResolution
from blizzard.hub.domain.graph.actions import RESERVED_WAITS, ActionSpec
from blizzard.hub.domain.graph.model import Node

#: Why a submission left its flows: the lever that ended its chunk, or any other move off the visit.
type Withdrawal = Literal["stopped", "restarted", "completed", "superseded"]
#: What a flow's record says happened — the kinds a ``flow-changed`` frame names, one frame per fact.
type FlowFactKind = FlowChangeKind
#: Where an open submission stands on a flow it holds: taking its turn, next to take one, eligible behind another,
#: waiting on its next poll, or passed over while its chunk is not ``delivering`` or its visit not current.
type FlowPlaceState = Literal["landing", "next", "waiting", "polling", "passed-over"]
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
class SubmissionClosure:
    """How a submission left its flows: a verdict's choice or a :data:`Withdrawal`, and when."""

    outcome: str
    closed_at: datetime


@domain_model
@dataclass(frozen=True)
class SubmissionRecord:
    """One submission of a chunk's visit as a reader sees it, without the flows it holds. ``closure`` is ``None``
    while the submission is open."""

    submission_id: str
    node_id: str
    epoch: int
    generation: int
    arrival: int
    submitted_at: datetime
    closure: SubmissionClosure | None


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
    """One entry in a flow's append-only record; ``id`` orders the record, newest highest."""

    id: int
    flow_id: str
    submission_id: str
    chunk_id: str
    kind: FlowFactKind
    at: datetime
    outcome: str | None = None
    revision: str | None = None


@domain_model
@dataclass(frozen=True)
class FlowOverview:
    """A flow with the newest of its record: its newest fact's instant, its last verdict — the newest ``turn-ended``
    fact whose outcome closed its submission and is not one of :data:`NOT_VERDICTS` — and its last landing, each
    ``None`` while the record holds none."""

    flow: Flow
    newest_at: datetime | None = None
    last_verdict: FlowFact | None = None
    last_landing: FlowFact | None = None

    def is_active(self, *, in_line: bool, since: datetime) -> bool:
        """Whether a board shows the flow: a submission is open on it, or its record moved at or after ``since``."""
        return in_line or (self.newest_at is not None and self.newest_at >= since)


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
    """What recording a turn's merge did: whether the fence would have refused it, the ``flow-landed`` event row
    it wrote — ``None`` for a replay that found the marker written — and the ``landed`` fact it wrote, none on a
    replay."""

    refused: bool
    event_id: int | None
    written: tuple[FlowFactWritten, ...] = ()


@domain_model
@dataclass(frozen=True)
class LandingTerms:
    """What one repository a visit holds lands by, taken at the visit's first turn and kept for the visit: its policy,
    its park timeout, and its position in the project's landing order — ``None`` when unranked. ``landing_policy`` is
    the stored name, read open."""

    repository_id: str
    landing_policy: str
    park_timeout_hours: int | None = None
    landing_position: float | None = None

    @classmethod
    def candidate(
        cls, repository_id: str, record: ConfiguredRepository | None, *, position: float | None
    ) -> LandingTerms:
        """The terms ``repository_id`` would take now: its record's policy and timeout, the default policy for a
        repository with no record, and ``position`` in its project's order."""
        if record is None:
            return cls(repository_id, str(DEFAULT_LANDING_POLICY), None, position)
        fields = record.fields
        return cls(repository_id, str(fields.landing_policy), fields.park_timeout_hours, position)


@domain_model
@dataclass(frozen=True)
class AdmittedTurn:
    """A turn the tick started, and the submission it is a turn of."""

    turn: Turn
    submission: Submission

    @property
    def written(self) -> tuple[FlowFactWritten, ...]:
        """The ``turn-started`` fact the turn's start wrote on each flow its submission holds."""
        return written_on(self.submission, kind="turn-started")


def written_on(
    submission: Submission, *, kind: FlowFactKind, outcome: str | None = None
) -> tuple[FlowFactWritten, ...]:
    """The fact of ``kind`` a write appends on each flow ``submission`` holds."""
    return FlowFactWritten.each(
        (flow.flow_id for flow in submission.flows),
        kind=kind,
        submission_id=submission.submission_id,
        chunk_id=submission.chunk_id,
        outcome=outcome,
    )


#: The turn outcomes and withdrawals that are never a verdict on a submission.
NOT_VERDICTS = frozenset(
    {
        TURN_INTERRUPTED,
        TURN_CUT_OFF,
        TURN_ESCALATED,
        *RESERVED_WAITS,
        *get_args(Withdrawal.__value__),
    }
)


def stamp_terms(
    resolution: RepositoryResolution,
    terms: Mapping[str, LandingTerms],
    positions: Mapping[str, float | None] = MappingProxyType({}),
) -> RepositoryResolution:
    """``resolution`` with each target's policy and landing position the ones the visit's terms hold for its record; a
    target with no terms keeps its record's policy and takes its position from ``positions``, the project's order
    as read now."""
    return replace(
        resolution,
        targets=tuple(
            replace(
                target,
                landing_policy=LandingPolicy(taken.landing_policy),
                landing_position=taken.landing_position,
            )
            if (taken := terms.get(target.record)) is not None
            else replace(target, landing_position=positions.get(target.record))
            for target in resolution.targets
        ),
    )


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


def describe_standing(
    facts: ChunkFacts,
    node: Node,
    standing: VisitStanding | None,
    *,
    epoch: int,
    now: datetime,
    flow_name: Callable[[Flow], str],
) -> str:
    """Where a chunk's visit to ``node`` at ``epoch`` stands, as an operator reads it: held by a person, waiting to be
    submitted, taking its turn, waiting on its next poll, or its place on each flow it holds, each named by
    ``flow_name``."""
    if facts.open_escalation() is not None:
        return "escalated — awaiting a person"
    if facts.status() is not ChunkStatus.DELIVERING:
        return f"{facts.status().value} — takes no turn until it is delivering"
    if standing is None:
        return "waiting to be submitted to its flows"
    names = [flow_name(flow) for flow in standing.submission.flows]
    if standing.turning:
        return f"landing on {', '.join(names)}" if names else "taking its turn"
    polls = facts.hub_node_poll_history(node_id=node.node_id, epoch=epoch)
    due = PollPolicy.of(node).due_at(polls[-1]) if polls else None
    if due is not None and due > now:
        return f"pending — next poll at {iso_utc(due)}"
    if not names:
        return "next to take its turn"
    return ", ".join(f"{_ordinal(ahead + 1)} on {name}" for name, ahead in zip(names, standing.ahead, strict=True))


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


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


def may_pass(facts: ChunkFacts, submission: Submission) -> bool:
    """The submission's visit may take a pass: its chunk is ``delivering`` and the visit is its current one."""
    return facts.status() is ChunkStatus.DELIVERING and is_current(facts, submission)


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
        if not may_pass(self.facts[submission.chunk_id], submission):
            return False
        if submission.submission_id in self.turning:
            return False
        due = self._due_at(submission)
        return due is None or due <= now

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

    def places(self, *, now: datetime) -> tuple[FlowPlace, ...]:
        """Every place the line's submissions hold, in arrival order, each submission's in ``flows`` order."""
        return tuple(place for submission in self.submissions for place in self.places_of(submission, now=now))

    def places_of(self, submission: Submission, *, now: datetime) -> tuple[FlowPlace, ...]:
        """The submission's place on each flow it holds. Its state is one across its flows: ``landing`` while it has
        an open turn, ``passed-over`` while it may not pass, ``polling`` until its next poll is due, ``next`` when
        :meth:`is_next`, else ``waiting``. Its position on a flow is one when landing or next, else one plus the
        eligible submissions that arrived before it there."""
        state, due = self._state(submission, now=now)
        return tuple(
            FlowPlace(
                submission=submission,
                flow=flow,
                state=state,
                position=1 if state in ("landing", "next") else 1 + self._eligible_ahead(submission, flow, now=now),
                due_at=due,
            )
            for flow in submission.flows
        )

    def _state(self, submission: Submission, *, now: datetime) -> tuple[FlowPlaceState, datetime | None]:
        if submission.submission_id in self.turning:
            return "landing", None
        if not may_pass(self.facts[submission.chunk_id], submission):
            return "passed-over", None
        due = self._due_at(submission)
        if due is not None and due > now:
            return "polling", due
        return ("next" if self.is_next(submission, now=now) else "waiting"), None

    def _eligible_ahead(self, submission: Submission, flow: Flow, *, now: datetime) -> int:
        return sum(
            1
            for other in self.submissions
            if other.arrival < submission.arrival
            and any(held.flow_id == flow.flow_id for held in other.flows)
            and self.eligible(other, now=now)
        )

    def _due_at(self, submission: Submission) -> datetime | None:
        """When the visit's next poll is due, or ``None`` while it has no poll row."""
        polls = self.facts[submission.chunk_id].hub_node_poll_history(
            node_id=submission.node_id, epoch=submission.epoch
        )
        return self.policies[submission.submission_id].due_at(polls[-1]) if polls else None


@domain_model
@dataclass(frozen=True)
class FlowPlace:
    """One open submission's place on one flow it holds, as :meth:`FlowLine.places_of` judges it; ``due_at`` is when
    its next poll is due while it is ``polling``, else ``None``."""

    submission: Submission
    flow: Flow
    state: FlowPlaceState
    position: int
    due_at: datetime | None = None
