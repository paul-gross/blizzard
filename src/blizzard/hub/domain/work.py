"""Work-lifecycle domain — the chunk, its facts, and its **derived** status.

The center of the model: per ``bzh:facts-not-status`` a chunk's status is never a
stored column, it is computed by :meth:`ChunkFacts.status` from the recorded facts.
The derivations are pure functions over already-loaded domain facts
(``bzh:domain-takes-objects``). Precedence is **first match wins**, top to bottom."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Executor, Graph


class ChunkStatus(StrEnum):
    """The derived chunk statuses. Never stored — always a query result."""

    NOT_READY = "not_ready"
    READY = "ready"
    RUNNING = "running"
    DELIVERING = "delivering"
    WAITING_ON_HUMAN = "waiting_on_human"
    NEEDS_HUMAN = "needs_human"
    PAUSED = "paused"
    STOPPED = "stopped"
    DONE = "done"

    @property
    def holds_claim(self) -> bool:
        """Whether a chunk at this status still holds the route it may be carrying (issue #140).
        Terminal outranks route liveness: a terminal transition from a runner node stamps no
        ``route.released``, so the raw route fact outlives it."""
        return self not in TERMINAL_STATUSES


# The two statuses a chunk never leaves — the one owner of "this chunk is finished",
# defined beside the enum it folds rather than re-spelled per call site.
TERMINAL_STATUSES = frozenset({ChunkStatus.STOPPED, ChunkStatus.DONE})


# --- Domain objects ---------------------------------------------------------


@dataclass(frozen=True)
class WorkRef:
    """One wrapped work item — ``{source, ref}``, superseding ``{provider, url}``.
    ``source`` names a configured ``[[work_source]]``; ``ref`` is that
    source's own item token (a GitHub issue number). Contents never stored."""

    source: str
    ref: str


class WorkItemCloseOutcome(StrEnum):
    """The result of one close attempt against a work item's source (issue #216).

    ``CLOSED``/``GONE`` are terminal; ``FAILED`` is retried on every later sweep until
    it converges to a terminal outcome."""

    CLOSED = "closed"
    GONE = "gone"
    FAILED = "failed"


@dataclass(frozen=True)
class ClosableWorkRef:
    """One ``(chunk_id, ref)`` pair a landed, non-grouped chunk still owes a closure
    attempt — :meth:`IReadChunkRepository.closable_work_refs`'s own row shape. Pairs,
    not a ``WorkRef``-keyed dict: two chunks can name the same ref, and a dict would
    silently drop one."""

    chunk_id: str
    ref: WorkRef


class MigrationMode(StrEnum):
    """How a chunk's intended migration fires at its next transition (issue #124).

    ``AUTO`` fires only when the transition's own destination node name also exists on
    the target graph; ``FORCED`` fires unconditionally onto the intent's ``node_name``."""

    AUTO = "auto"
    FORCED = "forced"


@dataclass(frozen=True)
class IntendedMigration:
    """A chunk's standing intent to move onto another graph (issue #124), consulted —
    never applied eagerly — at its next transition. ``node_name`` is required for
    :attr:`MigrationMode.FORCED` and ``None`` for :attr:`MigrationMode.AUTO`, whose
    landing name is the transition's own destination, resolved at consult time."""

    mode: MigrationMode
    graph_id: str
    node_name: str | None


@dataclass(frozen=True)
class Chunk:
    """The unit of work that travels the workflow graph."""

    chunk_id: str
    graph_id: str
    work_refs: list[WorkRef]
    minted_at: datetime
    # The chunk's **default** model preference and effort (issue #144) — what a surface
    # declaring neither inherits; empty/``None`` means *express no preference*.
    default_model: list[str] = field(default_factory=list)
    default_effort: str | None = None
    # The chunk's standing intent to migrate onto another graph at its next transition
    # (issue #124) — ``None`` while no intent is set.
    intended_migration: IntendedMigration | None = None


# --- Facts that feed the derivations ---------------------------------------
# Each is the domain-object form of a fact row; a hydrating repository fills them.


@dataclass(frozen=True)
class RouteCreatedFact:
    """A ``route.created`` fact — the claim.

    ``seq`` is the monotonic route-event tiebreak (see :meth:`ChunkFacts._has_live_route`): a
    per-chunk counter shared with :class:`RouteReleasedFact`, in real write order."""

    created_at: datetime
    seq: int = 0


@dataclass(frozen=True)
class RouteReleasedFact:
    """A ``route.released`` fact — forcible detach. ``seq`` — see :class:`RouteCreatedFact`."""

    released_at: datetime
    seq: int = 0


@dataclass(frozen=True)
class RouteTokenMintedFact:
    """A ``route_token_minted`` fact — the route capability token, hashed (issue #84a).
    Appended, never rewritten (``bzh:facts-not-status``). ``token_hash`` is the sha256
    hex digest only. ``seq`` shares the per-chunk counter :class:`RouteCreatedFact` uses,
    so it totally orders against a create/release even on a timestamp tie."""

    token_hash: str
    minted_at: datetime
    seq: int = 0


@dataclass(frozen=True)
class PrOpenedFact:
    """A ``pr.opened`` fact — the open-pr deliver mode's park record. One per repo whose
    branch got a PR instead of a merge; it carries no terminal weight, deriving
    ``delivering`` while no ``pr.closed`` matches. ``repo`` is also the skip-set that
    keeps a redelivery from opening a duplicate PR."""

    repo: str
    number: int
    url: str
    commit_hash: str
    opened_at: datetime


@dataclass(frozen=True)
class LeaseFact:
    """A ``lease.minted`` fact reported up from a runner."""

    epoch: int
    minted_at: datetime


@dataclass(frozen=True)
class TransitionFact:
    """A ``transition.recorded`` fact with its target node's executor, resolved by the
    hydrating repository so the derivation stays a pure function. ``from_node_id`` and
    ``choice_name`` describe the edge taken. ``graph_id`` is the graph the transition
    happened in (issue #90), so node names resolve against it, not the current pin."""

    to_node_id: str
    to_node_executor: Executor
    epoch: int
    recorded_at: datetime
    from_node_id: str | None = None
    choice_name: str | None = None
    graph_id: str | None = None


@dataclass(frozen=True)
class EscalationFact:
    """An ``escalation.recorded`` fact — the system ran out of moves on this chunk.
    Carries the takeover command and its wrapped equivalent. Wrapped-vs-raw rules:
    `blizzard-context:/domain/humans.md` §Escalation. The status derivation keys only
    on ``(epoch, recorded_at)`` supersession."""

    epoch: int
    recorded_at: datetime
    takeover_command: str = ""
    wrapped_takeover_command: str = ""


@dataclass(frozen=True)
class QuestionFact:
    """A ``question.asked`` row and whether it has been answered. Open/answered is
    **derived**: a question is open exactly while no ``question.answered`` row exists.
    ``answered`` is resolved by the hydrating repository so the derivation stays a pure
    function; ``question_id`` and ``asked_at`` order a chunk's asks."""

    question_id: str
    asked_at: datetime
    answered: bool = False


@dataclass(frozen=True)
class DecisionFact:
    """A gate's ``decision.submitted`` row and whether a transition references it. An
    **open** decision — one no transition resolves — is what ``waiting_on_human``
    derives from. ``resolved`` is computed by the hydrating repository so the
    derivation reads a plain boolean."""

    decision_id: str
    submitted_at: datetime
    resolved: bool = False


@dataclass(frozen=True)
class BounceFact:
    """A ``chunk_bounces`` row — one delivery kick-back (#64). Contention, not failure: a
    bounce consumes no node retry, and only crossing the node's ``bounce_cap`` escalates.
    ``(chunk_id, epoch)`` is the natural key guarding against a redelivery replay
    double-counting; ``envelope`` is the opaque JSON kick-back payload."""

    epoch: int
    cause: str
    envelope: str
    recorded_at: datetime


@dataclass(frozen=True)
class HubNodePollFact:
    """A ``hub_node_poll`` row — one pending-poll attempt at a hub command node (#66).
    Append-only. ``epoch`` is the arrival epoch of the current visit to ``node_id``, not
    a fresh one per poll. Pending-ness (:meth:`ChunkFacts.hub_node_pending`) derives from these rows
    plus the newest transition, so a ``kill -9`` between polls resumes from the store."""

    node_id: str
    epoch: int
    polled_at: datetime


class MigrationSource(StrEnum):
    """What moved a chunk onto another graph — a migration's attribution (issue #164).

    Three paths reach the same :meth:`IWriteChunkRepository.record_migration`, and without
    a discriminator their facts are byte-identical in history."""

    #: A judgement choice whose ``to:`` named ``graph:<name>`` (issue #90).
    AUTHORED_EDGE = "authored-edge"
    #: The chunk's standing ``intended_migration``, set by an operator (issue #124).
    INTENT = "intent"
    #: The standing follow-latest policy (issue #164) — nobody asked for this move.
    FOLLOW_LATEST = "follow-latest"


@dataclass(frozen=True)
class MigrationFact:
    """A ``chunk_migrations`` fact — a cross-graph migration re-pinned the chunk (issue
    #90). Its own recorded fact, **never a transition** (``bzh:migration-not-transition``).
    ``landed_node_executor`` is resolved at read time against ``to_graph_id``; ``source``
    (issue #164) attributes the move, and is ``None`` on a row predating it."""

    from_node_id: str | None
    from_graph_id: str
    to_graph_id: str
    landed_node_id: str | None
    choice_name: str | None
    model: str | None
    epoch: int
    recorded_at: datetime
    landed_node_executor: Executor = Executor.RUNNER
    source: MigrationSource | None = None

    @staticmethod
    def landing_node(target_graph: Graph, from_node_name: str | None) -> str:
        """The node a migration lands on in ``target_graph`` — name-match-else-entry (issue #90).

        ``bzh:migration-not-transition``'s landing rule. A pure function of the passed-in
        graph (``bzh:domain-takes-objects``)."""
        if from_node_name is not None:
            node = target_graph.node_by_name(from_node_name)
            if node is not None:
                return node.node_id
        return target_graph.entry_node_id


@dataclass(frozen=True)
class RequeueFact:
    """A ``requeue.recorded`` fact — closes an open escalation by supersession."""

    requeued_at: datetime


@dataclass(frozen=True)
class PauseFact:
    """A ``chunk.paused``/``chunk.resumed`` fact — newest-fact-wins (issue #46)."""

    paused: bool
    set_at: datetime
    set_by: str


@dataclass(frozen=True)
class UsageFact:
    """A ``usage.recorded`` fact — one harness invocation's usage/cost telemetry (issue #59).
    Deliberately **not** epoch-fenced: a row whose epoch trails the chunk's latest is real
    spend by a fenced-out zombie attempt and must still be summed, never dropped. The
    chunk-level total (:meth:`ChunkFacts.usage_total`) sums every row regardless of epoch."""

    node_id: str
    epoch: int
    kind: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float | None
    recorded_at: datetime


@dataclass(frozen=True)
class EventRow:
    """One ``event_log`` row — a durable, typed, severity-ranked operational fact (issue
    #125). ``chunk_id`` is ``None`` for a runner-scoped event; ``detail`` is the
    event-specific payload, already decoded from JSON. A negative ``id`` and a ``None``
    ``runner_id`` mark a row :class:`EventFeed` synthesized rather than read."""

    id: int
    recorded_at: datetime
    severity: str
    kind: str
    runner_id: str | None
    chunk_id: str | None
    lease_id: str | None
    node_name: str | None
    message: str
    detail: dict | None


@dataclass(frozen=True)
class EscalationOpen:
    """One fleet-wide **open** escalation — the input :class:`EventFeed` folds into the
    unified event feed (issue #125). Carries its own ``chunk_id``, since the read it
    comes from spans every chunk at once."""

    chunk_id: str
    recorded_at: datetime
    takeover_command: str


#: Default cap on ``list_events`` — an unbounded read of an append-only table is an unbounded response.
DEFAULT_EVENT_LIST_LIMIT = 200

_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class EventFeed:
    """``event_log`` rows unified with every currently-open escalation (issue #125).

    Sorted severity-then-recency: critical before warning before info, newest
    ``recorded_at`` first within a band."""

    rows: list[EventRow]

    @classmethod
    def of(cls, events: list[EventRow], escalations: list[EscalationOpen]) -> EventFeed:
        projected = [cls._projected(i, esc) for i, esc in enumerate(escalations)]
        merged = [*events, *projected]
        return cls(
            sorted(
                merged, key=lambda e: (_SEVERITY_RANK.get(e.severity, len(_SEVERITY_RANK)), -e.recorded_at.timestamp())
            )
        )

    @staticmethod
    def _projected(index: int, esc: EscalationOpen) -> EventRow:
        """One open escalation as a synthetic row carrying a **negative** ``id`` — it is
        not an ``event_log`` row."""
        return EventRow(
            id=-(index + 1),
            recorded_at=esc.recorded_at,
            severity="critical",
            kind="needs-human",
            runner_id=None,
            chunk_id=esc.chunk_id,
            lease_id=None,
            node_name=None,
            # `esc.takeover_command` is not always a resume command, so this promises a
            # way to proceed only when one exists (`blizzard-context:/domain/humans.md`).
            message=(
                f"chunk {esc.chunk_id} needs a human — see the chunk's escalation for how to proceed"
                if esc.takeover_command
                else f"chunk {esc.chunk_id} needs a human"
            ),
            detail=None,
        )


@dataclass(frozen=True)
class ActivityRow:
    """One row of the activity feed (issue #213) — a historical fact reshaped into the
    same vocabulary a live SSE frame carries. ``type`` mirrors a frame-type constant as a
    plain string (``bzh:domain-core``); ``key`` is a table-qualified natural key used only
    as the sort tiebreak; ``at`` is the fact's own recorded instant."""

    type: str
    key: str
    at: datetime
    # chunk-changed
    chunk_id: str | None = None
    status: str | None = None
    prev_status: str | None = None
    node: str | None = None
    prev_node: str | None = None
    runner_id: str | None = None
    cause: str | None = None
    graph_id: str | None = None
    # event-logged
    severity: str | None = None
    kind: str | None = None
    # runner-changed
    by: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ActivityFeed:
    """The activity feed's three already-bounded per-source reads, merged (issue #213).

    Merge only: sorts by ``(at desc, key desc)`` — ``key`` breaking an exact-instant tie
    — and caps to ``limit``."""

    rows: list[ActivityRow]

    @classmethod
    def of(
        cls,
        chunk_changed: Sequence[ActivityRow],
        events: Sequence[EventRow],
        runner_changed: Sequence[ActivityRow],
        *,
        limit: int,
    ) -> ActivityFeed:
        merged = [*chunk_changed, *(cls._of_event(e) for e in events), *runner_changed]
        merged.sort(key=lambda row: (row.at, row.key), reverse=True)
        return cls(merged[:limit])

    @staticmethod
    def _of_event(row: EventRow) -> ActivityRow:
        """One ``event_log`` row reshaped into the feed's common row type — its
        ``event-logged`` half."""
        return ActivityRow(
            type="event-logged",
            key=f"event_log:{row.id}",
            at=row.recorded_at,
            chunk_id=row.chunk_id,
            runner_id=row.runner_id,
            severity=row.severity,
            kind=row.kind,
        )


@dataclass(frozen=True)
class DecisionChoice:
    """One selectable gate outcome."""

    name: str
    description: str


@dataclass(frozen=True)
class DecisionRow:
    """A gate decision in full — the surfacing/read model.

    Resolution state is **derived**: ``resolved_choice`` is set once a resolution row
    exists, and ``transitioned`` is true once a transition references this decision."""

    decision_id: str
    chunk_id: str
    node_id: str
    node_name: str
    epoch: int
    choices: list[DecisionChoice]
    submitted_at: datetime
    resolved_choice: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    transitioned: bool = False

    @property
    def resolved(self) -> bool:
        return self.resolved_choice is not None


@dataclass(frozen=True)
class ChunkFacts:
    """Every fact a chunk's status derives from, already loaded. The derivation is a
    pure function of this aggregate — the unit tests build it directly, no store."""

    minted: bool
    promoted: bool = False
    stopped: bool = False
    # ``chunk.stopped``'s own instant (issue #173); ``None`` exactly when not stopped.
    stopped_at: datetime | None = None
    # ``chunk.completed`` — an operator's manual completion (issue #294), named for the
    # operator rather than ``completed``/``completed_at`` since :meth:`completed_at`
    # already names the render-only derived instant.
    operator_completed: bool = False
    operator_completed_at: datetime | None = None
    # ``delivery.landed`` — the whole-chunk terminal fact, informational only
    # (``bzh:facts-not-status``): DONE derives from the terminal transition, not this.
    delivery_landed: bool = False
    # The chunk's per-repo ``delivery.repo_landed`` facts, independent of whether delivery
    # has reached a terminal transition; the derivation reads only non-emptiness.
    landed_repos: frozenset[str] = field(default_factory=frozenset)
    pr_closed: bool = False
    # The newest ``delivery_pr_closed.closed_at`` across every repo's row (issue #173) — a
    # multi-repo chunk in open-PR mode can carry several.
    pr_closed_at: datetime | None = None
    escalations: list[EscalationFact] = field(default_factory=list)
    leases: list[LeaseFact] = field(default_factory=list)
    transitions: list[TransitionFact] = field(default_factory=list)
    routes_created: list[RouteCreatedFact] = field(default_factory=list)
    routes_released: list[RouteReleasedFact] = field(default_factory=list)
    route_tokens_minted: list[RouteTokenMintedFact] = field(default_factory=list)
    questions: list[QuestionFact] = field(default_factory=list)
    decisions: list[DecisionFact] = field(default_factory=list)
    requeues: list[RequeueFact] = field(default_factory=list)
    # The chunk's cross-graph migration facts (issue #90) — each re-pins the chunk and
    # re-queues it, superseding an earlier transition for the terminal/hub-node checks.
    migrations: list[MigrationFact] = field(default_factory=list)
    pr_opened: list[PrOpenedFact] = field(default_factory=list)
    pauses: list[PauseFact] = field(default_factory=list)
    usage: list[UsageFact] = field(default_factory=list)
    # The chunk's recorded delivery kick-backs (#64) — feeds :meth:`ChunkFacts.bounce_count` /
    # :meth:`ChunkFacts.bounces_over_cap` and the chunk-detail bounce history. Never a status.
    bounces: list[BounceFact] = field(default_factory=list)
    # The chunk's recorded hub-node poll attempts (#66) — feeds :meth:`ChunkFacts.hub_node_pending`.
    # Never a status: pending is a facet of ``delivering``.
    hub_node_polls: list[HubNodePollFact] = field(default_factory=list)

    def newest_transition(self) -> TransitionFact | None:
        """The chunk's newest accepted transition — its current node derives from this.

        Ordered by ``(recorded_at, epoch)``: the fencing epoch breaks a tie between two
        transitions stamped at the same instant."""
        if not self.transitions:
            return None
        return max(self.transitions, key=lambda t: (t.recorded_at, t.epoch))

    def transition_history(self) -> list[TransitionFact]:
        """The chunk's accepted transitions in the order they were recorded (oldest first).

        Ordered by the same key ``newest_transition`` selects the tail of, so "the last
        entry is the current node" holds by construction."""
        return sorted(self.transitions, key=lambda t: (t.recorded_at, t.epoch))

    def newest_migration(self) -> MigrationFact | None:
        """The chunk's newest cross-graph migration fact, or ``None`` (issue #90).

        Ordered by ``(recorded_at, epoch)`` — the same key ``newest_transition`` uses."""
        if not self.migrations:
            return None
        return max(self.migrations, key=lambda m: (m.recorded_at, m.epoch))

    def latest_epoch(self) -> int | None:
        """The chunk's latest fencing epoch — its newest lease's, else ``None``."""
        if not self.leases:
            return None
        return max(lease.epoch for lease in self.leases)

    def current_node_id(self) -> str | None:
        """The chunk's current node id — the newest movement fact's target, else ``None``.

        Normally the newest transition's ``to_node_id``; when a **migration** is the latest
        movement (issue #90), the migration's ``landed_node_id`` instead. ``None`` means the
        chunk has not yet moved, and the caller resolves the pinned graph's entry node."""
        if self._latest_movement_is_migration():
            migration = self.newest_migration()
            assert migration is not None  # _latest_movement_is_migration guarantees it
            return migration.landed_node_id
        transition = self.newest_transition()
        return transition.to_node_id if transition is not None else None

    def newest_transition_is_terminal(self) -> bool:
        """The newest accepted transition's target is the reserved terminal (``done``, #63).

        The **sole** DONE trigger — reaching the terminal, not any landed/closed fact. A
        later migration supersedes the transition entirely (issue #90): a re-queued chunk
        is never DONE off a pre-migration terminal."""
        if self._latest_movement_is_migration():
            return False
        transition = self.newest_transition()
        return transition is not None and transition.to_node_id == RESERVED_TERMINAL

    def _latest_movement_is_migration(self) -> bool:
        """The chunk's newest movement fact is a migration, not a transition (issue #90).

        A migration re-queues the chunk, so once it is the latest movement the pre-migration
        transition's terminal/hub identity is superseded. Ties go to the migration — it is
        recorded *after* the transition that brought the chunk to the node it leaves."""
        migration = self.newest_migration()
        if migration is None:
            return False
        transition = self.newest_transition()
        if transition is None:
            return True
        return (migration.recorded_at, migration.epoch) >= (transition.recorded_at, transition.epoch)

    def _newest_transition_enters_hub_node(self) -> bool:
        """The newest accepted transition's target is a hub-executed node.

        A later migration supersedes it (issue #90), and that landing node can itself be
        hub-executed (issue #111), deriving ``delivering`` rather than ``ready``."""
        if self._latest_movement_is_migration():
            migration = self.newest_migration()
            return migration is not None and migration.landed_node_executor is Executor.HUB
        transition = self.newest_transition()
        return transition is not None and transition.to_node_executor is Executor.HUB

    def _operator_completion_outranks_stop(self) -> bool:
        """A ``chunk.completed`` fact outranks the stop it follows (issue #294) — the one
        way a stopped chunk still reaches ``done``.

        Ties go to the completion, the same convention :meth:`_latest_movement_is_migration`
        states for its own tie: the completion is recorded *after* the stop it supersedes,
        so ``>=`` against ``stopped_at``, not ``>``."""
        if not self.operator_completed:
            return False
        if not self.stopped:
            return True
        assert self.operator_completed_at is not None  # invariant: set iff operator_completed
        assert self.stopped_at is not None  # invariant: set iff stopped
        return self.operator_completed_at >= self.stopped_at

    def status(self) -> ChunkStatus:
        """Derive a chunk's single status from its facts, first match wins.

        ``done`` is the **only** terminal (#63) and derives from *reaching* the terminal
        transition, from an operator's manual completion (issue #294), or from the open-pr
        mode's own terminal fact — not from the landed fact — an authored ``merged -> <node>``
        edge can land every repo and keep the chunk running in a post-merge node."""
        if self.stopped and not self._operator_completion_outranks_stop():
            return ChunkStatus.STOPPED
        if self.operator_completed or self.newest_transition_is_terminal() or self.pr_closed:
            # ``pr.closed`` is the open-pr mode's terminal fact (merged or closed unmerged); its
            # finalize lands the terminal transition too, but the check keeps this legible.
            return ChunkStatus.DONE
        if self._has_open_escalation():
            return ChunkStatus.NEEDS_HUMAN
        if self._is_waiting_on_human():
            # An open question or an open decision (gate); the
            # reap clock is stopped and the answer/resolution flips it back.
            return ChunkStatus.WAITING_ON_HUMAN
        if self._is_paused():
            # Below the human-gated states (a chunk both parked on a question and paused
            # is still, first, waiting on a human) and above delivering/running (issue #46).
            return ChunkStatus.PAUSED
        if self._newest_transition_enters_hub_node():
            return ChunkStatus.DELIVERING
        if self._has_live_route():
            return ChunkStatus.RUNNING
        if not self.promoted:
            # An un-promoted chunk rests ``not_ready`` — visible but never claimed. Below every
            # post-claim state, so only a fresh chunk with no live route lands here.
            return ChunkStatus.NOT_READY
        return ChunkStatus.READY

    def completed_at(self) -> datetime | None:
        """The instant a terminal chunk finished, or ``None`` (issue #173) — render-only,
        never a status. Mirrors ``status``'s branch order (issue #294's operator completion
        included) so the two never disagree, taking the **later** of the terminal transition
        and ``pr_closed_at`` in open-PR mode, where closing every repo's PR can lag the
        terminal transition."""
        if self.stopped and not self._operator_completion_outranks_stop():
            return self.stopped_at
        if self.operator_completed:
            return self.operator_completed_at
        terminal_transition = self.newest_transition() if self.newest_transition_is_terminal() else None
        if terminal_transition is None:
            return self.pr_closed_at if self.pr_closed else None
        if self.pr_closed_at is not None:
            return max(terminal_transition.recorded_at, self.pr_closed_at)
        return terminal_transition.recorded_at

    def open_escalation(self) -> EscalationFact | None:
        """The newest escalation nothing later superseded, or ``None``.

        Closed by supersession, never a resolution fact: a later lease mint or
        ``requeue.recorded`` hands the work back to the fleet, and a later **completion** —
        stopped or done — is the chunk ending without one (#292, #293)."""
        if not self.escalations:
            return None
        newest = max(self.escalations, key=lambda e: e.recorded_at)
        superseding = (
            *(lease.minted_at for lease in self.leases),
            *(rq.requeued_at for rq in self.requeues),
            self.completed_at(),
        )
        return None if any(at is not None and at > newest.recorded_at for at in superseding) else newest

    def open_questions(self) -> list[QuestionFact]:
        """The chunk's unanswered questions, oldest first.

        A question is open exactly while no ``question.answered`` row exists; an answer
        flips it out of ``waiting_on_human``, and non-emptiness is the derivation input."""
        return sorted((q for q in self.questions if not q.answered), key=lambda q: (q.asked_at, q.question_id))

    def open_decision(self) -> DecisionFact | None:
        """The newest gate decision no resolution has flipped off, or ``None``.

        A decision is open while it carries no resolution row. Once resolved,
        ``waiting_on_human`` drops away. Pending-ness is derived, never stored."""
        unresolved = [d for d in self.decisions if not d.resolved]
        if not unresolved:
            return None
        return max(unresolved, key=lambda d: d.submitted_at)

    def has_open_decision(self) -> bool:
        """True iff a gate's decision is unresolved — no resolution flips it off."""
        return self.open_decision() is not None

    def open_pause(self) -> PauseFact | None:
        """The newest pause fact iff it currently reads paused, else ``None`` (issue #46).

        Reads the fact directly rather than the derived status: PAUSED sits below the
        human-gated states, so a status-keyed reader would miss a chunk that is paused
        *and* parked on a question."""
        return self.pauses[-1] if self.pauses and self.pauses[-1].paused else None

    def awaiting_external_merge(self) -> bool:
        """A ``delivering`` chunk parked on an open PR — ``pr.opened`` without ``pr.closed``.

        Not a distinct status: the chunk still derives ``delivering``. A **detail** that
        distinguishes an open-pr park from an in-flight merge."""
        return bool(self.pr_opened) and not self.pr_closed

    def _has_open_escalation(self) -> bool:
        """An escalation nothing later superseded — supersession, not resolution."""
        return self.open_escalation() is not None

    def _is_waiting_on_human(self) -> bool:
        """An open question or an open decision parks the chunk."""
        return bool(self.open_questions()) or self.has_open_decision()

    def _is_paused(self) -> bool:
        """Paused derives from the newest pause fact, newest-fact-wins (issue #46)."""
        return self.open_pause() is not None

    @property
    def routes(self) -> RouteHistory:
        """The chunk's route facts as the object that derives their liveness."""
        return RouteHistory.of(self)

    def _has_live_route(self) -> bool:
        """A ``route.created`` with no later ``route.released``."""
        return self.routes.newest is not None

    def has_landed_repos(self, artifacts: Sequence[ArtifactRow] = ()) -> bool:
        """True iff any repo has landed for this chunk — informational, never a status (#63).

        ``artifacts`` carries the generic ``merged/<repo>`` marker convention (#67) — the
        current landing truth; the fact inputs are read alongside for back-compat, so a
        historical chunk still reads landed."""
        return self.delivery_landed or bool(self.landed_repos) or bool(LandedRepos.of(artifacts).names)

    def bounce_count(self) -> int:
        """The chunk's total recorded delivery kick-backs (#64) — informational.

        Feeds the cap check (``bounces_over_cap``) and the chunk-detail bounce history;
        never itself a status — a bounce is contention, not failure."""
        return len(self.bounces)

    def bounces_over_cap(self, cap: int) -> bool:
        """True once the chunk's bounce count has **crossed** ``cap`` (#64).

        Crossed, not reached: a node whose ``bounce_cap`` is 5 tolerates 5 kick-backs
        before this flips True on the 6th — the cap counts bounces a chunk survives before
        escalating, not a zero-indexed budget."""
        return self.bounce_count() > cap

    def hub_node_poll_history(self, *, node_id: str, epoch: int) -> list[HubNodePollFact]:
        """A hub node's poll attempts for one (node, epoch) visit, oldest first (#66).

        The earliest entry bounds ``poll_timeout``, the newest gates ``poll_interval`` —
        read off this history rather than in-memory state, so a ``kill -9`` resumes here."""
        return sorted(
            (p for p in self.hub_node_polls if p.node_id == node_id and p.epoch == epoch), key=lambda p: p.polled_at
        )

    def hub_node_pending(self) -> HubNodePollFact | None:
        """The chunk's in-progress hub-node poll, or ``None`` — chunk-detail honesty (#66).

        Not a distinct status: the chunk still derives ``delivering``. A poll fact recorded
        for the newest transition's ``(to_node_id, epoch)`` with no later transition means
        the node is still waiting on external state."""
        transition = self.newest_transition()
        if transition is None or transition.to_node_executor is not Executor.HUB:
            return None
        history = self.hub_node_poll_history(node_id=transition.to_node_id, epoch=transition.epoch)
        return history[-1] if history else None

    def usage_total(self) -> UsageTotal:
        """Sum a chunk's usage facts into its derived total — tokens by class + cost.

        Deliberately unfenced by epoch (unlike the status derivations): every recorded
        usage row is real spend, summed regardless of which epoch minted it."""
        return UsageTotal.of(self.usage)


# --- The derivation queries -----------------------------------------


_MARKER_PREFIX = "merged/"


@dataclass(frozen=True)
class LandedRepos:
    """Repos landed via a hub command node's ``merged/<repo>`` marker artifact (#67).

    No engine code names a "deliver" node, so a chunk's landed detail is read off its
    own node artifacts rather than a privileged fact family."""

    names: frozenset[str]

    @classmethod
    def of(cls, artifacts: Sequence[ArtifactRow]) -> LandedRepos:
        return cls(
            frozenset(a.name.removeprefix(_MARKER_PREFIX) for a in artifacts if a.name.startswith(_MARKER_PREFIX))
        )


@dataclass(frozen=True)
class RouteHistory:
    """A chunk's route facts and the liveness they derive (issue #41)."""

    created: list[RouteCreatedFact] = field(default_factory=list)
    released: list[RouteReleasedFact] = field(default_factory=list)
    tokens_minted: list[RouteTokenMintedFact] = field(default_factory=list)

    @classmethod
    def of(cls, facts: ChunkFacts) -> RouteHistory:
        return cls(facts.routes_created, facts.routes_released, facts.route_tokens_minted)

    @property
    def newest(self) -> RouteCreatedFact | None:
        """The newest ``route.created`` fact still live, or ``None`` if released.

        The single tie-break route liveness resolves against, ``(timestamp, seq)``, where
        ``seq`` is a per-chunk counter assigned in real write order (pinned by
        ``test_reclaimed_after_release_is_running_again``)."""
        if not self.created:
            return None
        newest_created = max(self.created, key=lambda r: (r.created_at, r.seq))
        key = (newest_created.created_at, newest_created.seq)
        if any((rel.released_at, rel.seq) > key for rel in self.released):
            return None
        return newest_created

    @property
    def newest_token(self) -> RouteTokenMintedFact | None:
        """The chunk's live route capability token, or ``None`` if unclaimed/released (issue #84a).

        The newest one minted at or after :attr:`newest`'s own ``seq`` — that lower bound
        alone scopes the search to the live acquisition. Newest-fact-wins is what makes a
        re-key supersede the prior token with no revocation."""
        live = self.newest
        if live is None:
            return None
        candidates = [t for t in self.tokens_minted if t.seq >= live.seq]
        if not candidates:
            return None
        return max(candidates, key=lambda t: (t.minted_at, t.seq))


@dataclass(frozen=True)
class ChunkChange:
    """A ``chunk-changed`` frame's derived content (issue #212) — the current status
    (derived the same way every status read is, :meth:`ChunkFacts.status`), the
    prev/current node names, the graph id, and the caller-supplied prev-status/runner/cause
    passed straight through."""

    status: str
    prev_status: str | None
    node: str | None
    prev_node: str | None
    runner_id: str | None
    cause: str | None
    graph_id: str

    @classmethod
    def of(
        cls,
        chunk: Chunk,
        graph: Graph,
        facts: ChunkFacts,
        *,
        prev_status: str | None,
        runner_id: str | None,
        cause: str | None,
        from_graph: Graph | None = None,
    ) -> ChunkChange:
        """Derive the frame's content from already-loaded objects
        (``bzh:domain-takes-objects``) — no store, no ids resolved here. ``graph`` must be
        the chunk's *post-mutation* pin. ``prev_node`` resolves against ``from_graph`` when
        the newest transition's own ``graph_id`` differs, honoring the same
        ``graph_id``-provenance ``ChunkFacts.transition_history`` does."""
        assert chunk.graph_id == graph.graph_id, "graph must be the chunk's post-mutation pin"

        current_id = facts.current_node_id() or graph.entry_node_id
        current = graph.node_by_id(current_id)
        node = current.name if current is not None else None

        prev_node: str | None = None
        transition = facts.newest_transition()
        if transition is not None and transition.from_node_id is not None:
            target_graph = graph
            if transition.graph_id is not None and transition.graph_id != graph.graph_id and from_graph is not None:
                target_graph = from_graph
            from_node = target_graph.node_by_id(transition.from_node_id)
            prev_node = from_node.name if from_node is not None else None

        return cls(
            status=facts.status().value,
            prev_status=prev_status,
            node=node,
            prev_node=prev_node,
            runner_id=runner_id,
            cause=cause,
            graph_id=graph.graph_id,
        )


@dataclass(frozen=True)
class UsageTotal:
    """A usage/cost total summed at read time, never a stored column. **The one canonical
    owner of the lower-bound + PARTIAL cost contract** (``canon:one-owner``): token counts
    are exact; ``cost_usd`` sums only the rows that carry one, so ``cost_partial`` (True
    iff any summed row lacked one) marks ``cost_usd`` a lower bound, to surface PARTIAL."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool

    @classmethod
    def of(cls, rows: list[UsageFact]) -> UsageTotal:
        """Sum ``rows`` into one total — one chunk's own facts, or an arbitrary set
        (the fleet spend-since window, issue #60)."""
        return cls(
            input_tokens=sum(u.input_tokens for u in rows),
            output_tokens=sum(u.output_tokens for u in rows),
            cache_read_tokens=sum(u.cache_read_tokens for u in rows),
            cache_create_tokens=sum(u.cache_create_tokens for u in rows),
            cost_usd=sum(u.cost_usd for u in rows if u.cost_usd is not None),
            cost_partial=any(u.cost_usd is None for u in rows),
        )

    @classmethod
    def of_grouped_sums(
        cls,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_create_tokens: int,
        cost_usd_sum: float,
        null_cost_rows: int,
    ) -> UsageTotal:
        """Build from sums a caller already grouped in SQL (blizzard#256 D6), applying
        this same lower-bound + PARTIAL contract over them rather than a second,
        independent one: ``cost_usd_sum`` is the caller's own skip-null sum (e.g.
        ``COALESCE(SUM(cost_usd), 0)`` over the group's non-null rows), and
        ``null_cost_rows`` is how many of the group's rows lacked a cost envelope."""
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_create_tokens=cache_create_tokens,
            cost_usd=cost_usd_sum,
            cost_partial=null_cost_rows > 0,
        )


@dataclass(frozen=True)
class FleetSummary:
    """Fleet-pulse counts (issue #76) — every chunk's derived status folded to four
    buckets. Derived, never stored, same as the per-chunk status it counts over."""

    ready: int = 0
    running: int = 0
    waiting: int = 0
    needs: int = 0

    @classmethod
    def of(cls, statuses: Iterable[ChunkStatus]) -> FleetSummary:
        """The one canonical statement of the fold: ``ready`` counts ``ready``; ``running``
        counts ``running`` + ``delivering``; ``waiting`` counts ``waiting_on_human`` +
        ``paused``; ``needs`` counts ``needs_human``. Every other status counts toward none."""
        ready = running = waiting = needs = 0
        for st in statuses:
            if st is ChunkStatus.READY:
                ready += 1
            elif st in (ChunkStatus.RUNNING, ChunkStatus.DELIVERING):
                running += 1
            elif st in (ChunkStatus.WAITING_ON_HUMAN, ChunkStatus.PAUSED):
                waiting += 1
            elif st is ChunkStatus.NEEDS_HUMAN:
                needs += 1
        return cls(ready=ready, running=running, waiting=waiting, needs=needs)


# --- Question rows (the ask/answer rendezvous) -------------------------------


@dataclass(frozen=True)
class QuestionRow:
    """A durable question row with its derived answer *and delivery* state. Every state
    here is **derived**: answered exactly while an answer row exists (the winning
    first-write-wins CAS row), delivered exactly while an ``answer_deliveries`` row
    exists (issue #165) — answered says a human decided, delivered says the agent heard."""

    question_id: str
    chunk_id: str
    node_id: str | None
    session_id: str | None
    runner_id: str
    epoch: int
    question: str
    options: list[str]
    asked_at: datetime
    answered: bool = False
    answer: str | None = None
    answered_by: str | None = None
    answered_at: datetime | None = None
    delivered: bool = False
    delivered_at: datetime | None = None


@dataclass(frozen=True)
class AnswerOutcome:
    """The result of an answer write — first-write-wins CAS. ``won`` is True for the
    write that landed the row; a later writer gets ``won=False`` with the **winning**
    row's ``answer``/``answered_by``, so the loser is told who already answered."""

    won: bool
    question_id: str
    answer: str
    answered_by: str
    answered_at: datetime


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


class IReadChunkRepository(Protocol):
    """Read-only chunk access. Controllers at the edges depend on this variant."""

    def get(self, chunk_id: str) -> Chunk | None: ...
    def load_facts(self, chunk_id: str) -> ChunkFacts | None: ...
    def get_question(self, question_id: str) -> QuestionRow | None:
        """One question row with its derived answer state, or None."""
        ...

    def list_open_questions(self) -> list[QuestionRow]:
        """Every unanswered question across the fleet — the ``hub status`` surface."""
        ...

    def load_questions(self, chunk_id: str) -> list[QuestionRow]:
        """A chunk's questions, open and answered — the chunk-detail surface."""
        ...

    def load_artifacts(self, chunk_id: str) -> list[ArtifactRow]:
        """Every artifact row of a chunk; the caller resolves latest-by-epoch."""
        ...

    def route_of(self, chunk_id: str) -> Route | None:
        """The chunk's live route (runner/workspace/envs), or None if unclaimed/released."""
        ...

    def list_ready(self) -> list[Chunk]: ...
    def list_all(self) -> list[Chunk]: ...
    def queue_positions(self) -> dict[str, float]:
        """The newest explicit ready-queue position per chunk — the order peek honours."""
        ...

    def promoted_ats(self) -> dict[str, datetime]:
        """Each promoted chunk's ``chunk_promoted.promoted_at`` — the ready-queue's
        fallback sort instant (issue #137) once a chunk has never had an explicit
        position stamped, superseding a never-promoted chunk's own ``minted_at``."""
        ...

    def find_live_holder(self, pointer: WorkRef) -> str | None:
        """The chunk_id of a live (non-terminal) chunk holding ``pointer``, or None."""
        ...

    def live_work_refs(self) -> dict[WorkRef, ChunkStatus]:
        """Every work ref held by a live (non-terminal) chunk, with that chunk's
        derived status — the inverse of :meth:`find_live_holder`, for the
        forge-status reconciler's desired-state sweep (issue #179)."""
        ...

    def closable_work_refs(self) -> list[ClosableWorkRef]:
        """Every ``(chunk_id, ref)`` pair a landed, non-grouped chunk still owes a closure
        attempt — :meth:`ChunkFacts.has_landed_repos` is the sole landing gate, so a chunk that landed
        and was *later* stopped still closes. A ref already carrying a terminal
        (``closed``/``gone``) closure fact is excluded; one carrying only ``failed`` is not."""
        ...

    def accepted_transition_target(self, chunk_id: str, *, from_node_id: str, epoch: int) -> str | None:
        """The ``to_node_id`` of an already-accepted transition out of ``from_node_id`` at
        ``epoch`` — the idempotency probe for a re-applied completion, or None."""
        ...

    def accepted_migration(self, chunk_id: str, *, from_node_id: str, epoch: int) -> bool:
        """True iff a cross-graph migration is already recorded for ``(chunk_id,
        from_node_id, epoch)`` (issue #90) — the replay probe for a re-applied cross-graph
        completion. A migration writes no transition, so :meth:`accepted_transition_target`
        never sees it; this is its counterpart."""
        ...

    def landed_repos(self, chunk_id: str) -> set[str]:
        """The repos already landed for a chunk — the delivery reconciliation skip-set."""
        ...

    def runner_high_water(self, runner_id: str) -> int:
        """The greatest per-runner seq the hub has already applied, or 0."""
        ...

    def get_decision(self, decision_id: str) -> DecisionRow | None:
        """One gate decision in full, with derived resolution/transition state."""
        ...

    def find_decision(self, chunk_id: str, *, node_id: str, epoch: int) -> DecisionRow | None:
        """The decision already open for a (chunk, node, epoch) — the idempotency probe
        for a re-submitted runner-config gate decision (a lost-ack replay)."""
        ...

    def decision_for_chunk(self, chunk_id: str) -> DecisionRow | None:
        """The chunk's newest not-yet-transitioned decision — what the board/runner act on."""
        ...

    def list_open_decisions(self) -> list[DecisionRow]:
        """Every unresolved decision across the fleet — the ``blizzard hub decisions`` view."""
        ...

    def usage_since(self, since: datetime, *, until: datetime | None = None) -> list[UsageFact]:
        """Every usage fact recorded at or after ``since`` — and, when ``until`` is given,
        strictly before it — across every chunk (issue #60, issue #183). ``since`` is
        inclusive and ``until`` exclusive, so adjacent windows sharing a boundary instant
        neither double-count nor drop a fact at it. Omitting ``until`` is the original
        open-ended tail. The caller derives the total via :meth:`UsageTotal.of`."""
        ...

    def list_events(
        self,
        *,
        severity: str | None = None,
        runner_id: str | None = None,
        chunk_id: str | None = None,
        since: datetime | None = None,
        limit: int = DEFAULT_EVENT_LIST_LIMIT,
    ) -> list[EventRow]:
        """The operational event log, newest-first (``recorded_at`` desc, ``id`` desc
        tiebreak), filtered by whichever of ``severity``/``runner_id``/``chunk_id``/
        ``since`` is given and bounded by ``limit`` — ``GET /api/events``'s own-table
        half (issue #125); the caller unifies it with :meth:`list_open_escalations` via
        :class:`EventFeed`."""
        ...

    def list_open_escalations(self) -> list[EscalationOpen]:
        """Every currently-open escalation, **fleet-wide** (issue #125).

        Each decided by :meth:`ChunkFacts.open_escalation` — the rule's one implementation
        (#293). Low-volume, so the candidate scan is full."""
        ...

    def activity_facts_since(self, since: datetime, *, limit: int) -> list[ActivityRow]:
        """Every ``chunk-changed``-shaped activity row across every mapped cause's fact
        table, at or after ``since`` (issue #213, AC4). ``edited`` is deliberately
        unrepresented: a chunk edit writes no fact row — a documented exclusion, not a
        gap. Each source table is read with its own bounded ``ORDER BY … LIMIT``, so this
        returns rows unsorted across sources."""
        ...


class IWriteChunkRepository(IReadChunkRepository, Protocol):
    """Read-write chunk access. Only the domain layer depends on this variant."""

    def mint(self, chunk: Chunk) -> None: ...
    def record_promote(self, chunk_id: str, *, at: datetime) -> int | None:
        """Record a ``chunk.promoted`` fact — flips ``not_ready`` to ``ready``.

        Idempotent: a chunk already promoted keeps its first row, so a re-promote writes
        nothing. Returns the freshly-written ``chunk_promoted.id``, or ``None`` on that
        no-op replay — there is no fresh row to name."""
        ...

    def record_lease(self, chunk_id: str, *, epoch: int, runner_id: str, at: datetime) -> None: ...
    def set_runner_high_water(self, runner_id: str, *, seq: int, at: datetime) -> None:
        """Advance a runner's applied-seq high-water mark (upsert)."""
        ...

    def record_route(self, route: Route, *, token_hash: str, at: datetime) -> str:
        """Record the route **and** mint its capability token's fact, atomically (issue #84a).

        ``token_hash`` is the sha256 digest of the plaintext token, already hashed by the
        caller (``bzh:domain-takes-objects``); the token fact lands in the same store
        write, never as a column on the route fact. Returns the minted ``route_id``."""
        ...

    def record_route_released(self, chunk_id: str, *, at: datetime) -> int:
        """Append the ``route.released`` fact. Returns the freshly-written
        ``route_released.id`` (issue #213's activity-feed key)."""
        ...

    def record_route_token(self, chunk_id: str, *, token_hash: str, at: datetime) -> None:
        """Append a fresh :class:`RouteTokenMintedFact` for the chunk's route — the re-key
        path (issue #84b). Never mutates the prior token fact (``bzh:facts-not-status``):
        :attr:`RouteHistory.newest_token` supersedes it with no separate revocation step."""
        ...

    def record_transition(
        self,
        *,
        transition_id: str,
        chunk_id: str,
        from_node_id: str | None,
        to_node_id: str,
        choice_name: str | None,
        epoch: int,
        runner_id: str,
        at: datetime,
        artifacts: list[ArtifactRow],
        decision_id: str | None = None,
    ) -> None:
        """One node-step's transition and its artifacts, written atomically.

        ``decision_id`` is set only on a gate-resolving transition — the Decision this
        transition resolves; ordinary transitions leave it ``None``."""
        ...

    def record_delivery_repo_landed(self, chunk_id: str, *, repo: str, commit_hash: str, at: datetime) -> None: ...
    def record_delivery_landed(self, chunk_id: str, *, at: datetime) -> None: ...

    def record_work_item_closure(
        self, chunk_id: str, *, pointer: WorkRef, outcome: WorkItemCloseOutcome, reason: str | None, at: datetime
    ) -> bool:
        """Append one closure-attempt outcome fact, idempotent per ``(chunk_id,
        pointer.source, pointer.ref, outcome)``. ``reason`` carries the failure/gone
        detail; ``None`` for ``closed``. Returns True iff it wrote a fresh row."""
        ...

    def finalize_delivery(
        self,
        chunk_id: str,
        *,
        from_node_id: str,
        to_node_id: str,
        choice_name: str,
        epoch: int,
        runner_id: str,
        transition_id: str,
        at: datetime,
    ) -> bool:
        """Land the terminal delivery atomically and idempotently — one transaction, a
        no-op if already landed (crash recovery). Returns True iff it wrote."""
        ...

    def record_bounce(self, chunk_id: str, *, epoch: int, cause: str, envelope: str, at: datetime) -> bool:
        """Record one delivery kick-back (#64), idempotent by ``(chunk_id, epoch)``.

        Append-only, and the sole input :meth:`ChunkFacts.bounce_count` derives from; the natural key
        makes a redelivery replay after a ``kill -9`` re-enter harmlessly rather than
        double-count. Returns True iff it wrote."""
        ...

    def record_bounce_escalation(
        self, chunk_id: str, *, epoch: int, runner_id: str, takeover_command: str, at: datetime
    ) -> bool:
        """Escalate a chunk whose bounce count crossed its node's cap (#64), atomically and
        idempotently. The hub lease and the escalation fact land in one transaction, guarded
        by the escalation's existence at this epoch. No transition is recorded: the chunk's
        held route and stuck node are untouched. Returns True iff it wrote."""
        ...

    def record_escalation(
        self,
        chunk_id: str,
        *,
        epoch: int,
        takeover_command: str,
        at: datetime,
        decision_id: str | None = None,
        wrapped_takeover_command: str = "",
    ) -> int:
        """Record an ``escalation.recorded`` fact — the chunk derives ``needs_human``
        until something supersedes it. The takeover command rides along so the
        parked session is resumable (`blizzard-context:/domain/humans.md`). ``decision_id``,
        when set, closes a gate decision no transition or migration will (issue #110)."""
        ...

    def record_usage(
        self,
        chunk_id: str,
        *,
        node_id: str,
        epoch: int,
        runner_id: str,
        kind: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_create_tokens: int,
        cost_usd: float | None,
        at: datetime,
    ) -> None:
        """Append one ``usage.recorded`` fact (issue #59) — never a stored aggregate.

        Deliberately **not** epoch-fenced: called for every landed usage fact regardless
        of whether ``epoch`` is the chunk's latest, since it is real spend either way.
        Idempotency rides the caller's own applied-seq high-water mark."""
        ...

    def record_event(
        self,
        *,
        severity: str,
        kind: str,
        runner_id: str,
        chunk_id: str | None,
        lease_id: str | None,
        node_name: str | None,
        message: str,
        detail: dict | None,
        at: datetime,
    ) -> int:
        """Append one ``event_log`` row (issue #125) — never mutated once written.

        ``chunk_id`` is ``None`` for a runner-scoped event; ``detail`` is an opaque
        event-specific payload, serialized to JSON text by the store. Returns the
        freshly-written ``event_log.id``."""
        ...

    def record_question(
        self,
        *,
        question_id: str,
        chunk_id: str,
        node_id: str | None,
        session_id: str | None,
        runner_id: str,
        epoch: int,
        question: str,
        options: list[str],
        asked_at: datetime,
    ) -> None:
        """Land a ``question.asked`` row — the chunk derives ``waiting_on_human``.

        Runner-authored, forwarded up the outbound buffer; the row is the durable
        rendezvous the answer keys off. Idempotent by ``question_id`` (a store-and-forward
        replay re-lands the same id harmlessly)."""
        ...

    def answer_question(self, question_id: str, *, answer: str, answered_by: str, at: datetime) -> AnswerOutcome:
        """First-write-wins CAS on the answer row.

        Exactly one answer row ever exists: the first write wins (``won=True``); a
        racing second write loses (``won=False``) and is handed the winning row. This
        row alone flips the chunk out of ``waiting_on_human``."""
        ...

    def record_answer_delivered(self, *, question_id: str, chunk_id: str, at: datetime) -> None:
        """Record an ``answer.delivered`` fact — the resume-with-answer ran.

        Detail only: the status already flipped at ``question.answered``, so no status
        derives from this."""
        ...

    def record_decision(
        self,
        *,
        decision_id: str,
        chunk_id: str,
        node_id: str,
        node_name: str,
        epoch: int,
        choices: list[DecisionChoice],
        at: datetime,
        artifacts: list[ArtifactRow],
    ) -> None:
        """Open a gate decision, committing any step artifacts atomically.

        A graph gate passes no artifacts (they landed with the arriving transition); a
        runner-config gate carries the gated step's artifacts here, exactly where the
        step's transition would have written them."""
        ...

    def record_decision_resolution(self, decision_id: str, *, choice: str, resolved_by: str, at: datetime) -> bool:
        """First-write-wins CAS: record the person's choice, or return ``False`` if
        the decision was already resolved (the loser is told who won)."""
        ...

    def record_requeue(self, chunk_id: str, *, at: datetime) -> int:
        """Record a ``requeue.recorded`` fact — supersedes an open escalation.

        Returns the freshly-written ``requeues.id`` (issue #213's activity-feed key)."""
        ...

    def record_migration(
        self,
        chunk_id: str,
        *,
        from_node_id: str | None,
        from_graph_id: str,
        to_graph_id: str,
        landed_node_id: str | None,
        choice_name: str | None,
        decision_id: str | None = None,
        model: str | None,
        epoch: int,
        at: datetime,
        artifacts: list[ArtifactRow],
        source: MigrationSource,
        release_route: bool = True,
        clear_intent: bool = False,
        migration_id: str | None = None,
    ) -> str | None:
        """Record a cross-graph migration atomically and idempotently (issue #90). One
        transaction: the ``chunk_migrations`` fact, the ``chunks.graph_id`` re-pin, the
        route release (unless ``release_route`` is ``False``), the submitting step's
        ``artifacts``, and — when ``clear_intent`` — the intent clear, so a durable fact
        implies a durably-cleared intent. Returns the ``migration_id``, ``None`` on replay."""
        ...

    def record_queue_position(self, chunk_id: str, *, position: float, at: datetime) -> None:
        """Append a ready chunk's new queue position; order derives."""
        ...

    def add_work_refs(self, chunk_id: str, pointers: list[WorkRef], *, at: datetime) -> None:
        """Fold work refs into a group survivor, de-duped by (source, ref)."""
        ...

    def record_grouped(self, chunk_id: str, *, grouped_into: str, at: datetime) -> int:
        """Record ``chunk.grouped`` — the merged-away chunk becomes ephemeral.

        Returns the freshly-written ``chunk_grouped.id`` (issue #213's activity-feed key)."""
        ...

    def record_pause(self, chunk_id: str, *, paused: bool, by: str, at: datetime) -> int:
        """Append a ``chunk.paused``/``chunk.resumed`` fact — newest-fact-wins (issue #46).

        Always writes a fresh row (never a no-op — "newest fact wins" reads, it does not
        skip writes), so the ``chunk_pause_facts.id`` comes back unconditionally."""
        ...

    def record_stop(self, chunk_id: str, *, by: str, at: datetime) -> int:
        """Append the ``chunk.stopped`` fact — terminal operator abandonment (issue #118) —
        and, atomically in the same store transaction, release any live route and any held
        fleet-wide hub-exec slot. Returns the freshly-written ``chunk_stopped.id``, not the
        ``route_released.id`` this same transaction may also write."""
        ...

    def record_completion(self, chunk_id: str, *, by: str, at: datetime) -> int:
        """Append the ``chunk.completed`` fact — an operator's manual completion, including
        from ``stopped`` (issue #294) — and, atomically in the same store transaction,
        release any live route and any held fleet-wide hub-exec slot, mirroring
        :meth:`record_stop`. The caller (:class:`~blizzard.hub.domain.complete.CompleteService`)
        has already checked the chunk is not already ``done`` — this always writes a fresh
        row. Returns the freshly-written ``chunk_completed.id``."""
        ...

    def set_graph(self, chunk_id: str, *, graph_id: str) -> None:
        """Repin a not-ready or ready-unclaimed chunk to a different workflow graph (issue #27, #120).

        A plain column overwrite, not an append-only fact: ``graph_id`` was already a
        mint-time column with no fact log behind it. The caller has already checked the
        chunk is still unclaimed, under the claim lock (issue #120)."""
        ...

    def set_defaults(self, chunk_id: str, *, default_model: list[str], default_effort: str | None) -> None:
        """Repin a not-ready or ready-unclaimed chunk's default model/effort (issue #144)
        — see :meth:`set_graph`. Both together in one write, never one at a time, so the
        pair cannot be left half-applied at a crash. An empty list / ``None`` is a real
        value — *express no preference*, the minted state — not "leave unchanged"."""
        ...

    def set_intended_migration(self, chunk_id: str, *, intended: IntendedMigration | None) -> None:
        """Set, overwrite, or clear a chunk's standing migration intent (issue #124).
        A plain column overwrite, not an append-only fact — the same shape :meth:`set_graph`
        carries. ``intended=None`` clears it; a non-``None`` value overwrites. Carries no
        timestamp — the column records no ``at``, unlike this repository's other writes."""
        ...

    # --- The generic hub command node (#65) ---------------------------------

    def acquire_hub_exec_slot(self, chunk_id: str, *, node_id: str, at: datetime, stale_after: timedelta) -> str | None:
        """Acquire the fleet-wide hub-execution serialization slot, or ``None`` if busy.
        A FACT-based lease (``bzh:facts-not-status``), not an in-process lock: insert-if-
        none-live in one transaction. Reentrant for the chunk that already holds it; a slot
        held by another defers unless older than ``stale_after``, when it is reclaimed."""
        ...

    def release_hub_exec_slot(self, chunk_id: str, *, at: datetime) -> None:
        """Release ``chunk_id``'s live hub-execution slot, if any — idempotent."""
        ...

    def count_live_hub_exec_slots(self) -> int:
        """The number of currently-live hub-execution slots — the invariant checker's
        ``hub:one-live-exec-slot`` probe (should never exceed 1)."""
        ...

    def has_hub_artifact(self, chunk_id: str, *, node_id: str, epoch: int, name: str) -> bool:
        """True iff a marker/log artifact named ``name`` is already recorded for this
        exact (chunk, node, epoch) — the ``produces:`` re-run skip probe (#65)."""
        ...

    def record_hub_artifact(
        self, chunk_id: str, *, node_id: str, node_name: str, epoch: int, name: str, content: str, at: datetime
    ) -> bool:
        """Append one hub-node progress artifact OUTSIDE a transition (#65).

        Idempotent per ``(chunk, node, name, epoch)`` natural key: a re-run that already
        recorded this artifact writes nothing a second time. Ordinary artifact rows,
        durable exactly like a worker-produced one. Returns True iff it wrote."""
        ...

    def record_hub_step_transition(
        self,
        chunk_id: str,
        *,
        from_node_id: str,
        to_node_id: str,
        choice_name: str,
        epoch: int,
        runner_id: str,
        transition_id: str,
        at: datetime,
        artifacts: list[ArtifactRow],
        release_route: bool,
    ) -> bool:
        """Record a generic hub command node's exit transition, atomically and idempotently
        (#65). The hub lease and the transition land in one transaction; ``release_route``
        is True only when ``to_node_id`` is the reserved terminal, writing the route release
        alongside. Guarded by the transition's existence at ``(chunk_id, from_node_id,
        epoch)``, so a redelivery replay after a ``kill -9`` re-enters harmlessly."""
        ...

    def record_hub_node_poll(self, chunk_id: str, *, node_id: str, epoch: int, at: datetime) -> None:
        """Append one pending-poll-attempt fact (#66) — never a transition.

        Append-only: an at-least-once poll attempt is harmless to record twice — it only
        widens the interval/timeout gating's read — so this carries no idempotency guard."""
        ...
