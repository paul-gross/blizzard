"""Work-lifecycle domain — the chunk, its facts, and its **derived** status.

The center of the model: per ``bzh:facts-not-status`` a chunk's status is never a
stored column, it is computed by :meth:`ChunkFacts.status` from the recorded facts.
The derivations are pure functions over already-loaded domain facts
(``bzh:domain-takes-objects``). Precedence is **first match wins**, top to bottom."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.chunk_migration import MigrationMode
from blizzard.foundation.chunk_status import PRE_CLAIM_STATUSES, TERMINAL_STATUSES, ChunkStatus
from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind, EventLogSeverity
from blizzard.foundation.hub_event_types import ActivityChunkChangeCause
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.migration_source import MigrationSource
from blizzard.foundation.node_steps import Executor
from blizzard.foundation.roles import domain_model
from blizzard.foundation.work_items import WorkItemClosure
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL, Graph, Node
from blizzard.hub.domain.runners.registration import RecordedPause

if TYPE_CHECKING:
    # Deferred: ``ports.exclusive`` imports this module's own ``Chunk``/``ChunkFacts``/
    # ``DependencyEdge`` — a runtime import here would cycle back.
    from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead

# --- Domain objects ---------------------------------------------------------


@domain_model
@dataclass(frozen=True)
class WorkRef:
    """One wrapped work item — ``{source, ref}``, superseding ``{provider, url}``.
    ``source`` names a configured work source; ``ref`` is that
    source's own item token (a GitHub issue number). Contents never stored."""

    source: str
    ref: str


class WorkItemAuthorKind(StrEnum):
    """Who filed a hub-owned work item — a hub user by id, or the fleet
    itself. Persisted as ``work_items.author_kind`` plus a JSON payload
    (``bzh:sql-portable``), never a DB enum."""

    USER = "user"
    FLEET = "fleet"


@domain_model
@dataclass(frozen=True)
class WorkItemAuthor:
    """One hub-owned work item's author — the variant :class:`WorkItemAuthorKind`
    discriminates. ``user_id`` is set only for :attr:`WorkItemAuthorKind.USER`;
    ``runner_id``/``chunk_id``/``node_name`` — the proposing runner, chunk, and node —
    are set only for :attr:`WorkItemAuthorKind.FLEET`."""

    kind: WorkItemAuthorKind
    user_id: str | None = None
    runner_id: str | None = None
    chunk_id: str | None = None
    node_name: str | None = None

    @classmethod
    def user(cls, user_id: str) -> WorkItemAuthor:
        return cls(kind=WorkItemAuthorKind.USER, user_id=user_id)

    @classmethod
    def fleet(cls, *, runner_id: str, chunk_id: str, node_name: str) -> WorkItemAuthor:
        return cls(kind=WorkItemAuthorKind.FLEET, runner_id=runner_id, chunk_id=chunk_id, node_name=node_name)


@domain_model
@dataclass(frozen=True)
class HubWorkItem:
    """One hub-owned work item — the ``work_items`` row. A mutable
    entity, not a fact: title/body/edited_at change in place, and
    ``closed_at``/``closure`` are unset while open, set together once when it closes."""

    work_item_id: str
    source: str
    ref: str
    title: str
    body: str
    author: WorkItemAuthor
    stated_priority: str | None
    created_at: datetime
    edited_at: datetime
    closed_at: datetime | None = None
    closure: WorkItemClosure | None = None
    # A routine run's own indexed values — ``None`` for every item but a
    # run's own.
    routine_name: str | None = None
    scope_slug: str | None = None
    run_mode: str | None = None

    @property
    def pointer(self) -> WorkRef:
        return WorkRef(source=self.source, ref=self.ref)


class WorkItemCloseOutcome(StrEnum):
    """The result of one close attempt against a work item's source.

    ``CLOSED``/``GONE`` are terminal; ``FAILED`` is retried on every later sweep until
    it converges to a terminal outcome."""

    CLOSED = "closed"
    GONE = "gone"
    FAILED = "failed"


@domain_model
@dataclass(frozen=True)
class PendingCloseIntent:
    """One ``(chunk_id, ref)`` pair carrying a pending ``close_intents`` row
    — :meth:`~blizzard.hub.domain.chunk.ports.delivery.IReadChunkDeliveryRepository.pending_close_intents`'s
    own row shape. Pairs, not a ``WorkRef``-keyed dict: two chunks can name the same ref, and a
    dict would silently drop one.

    ``intent_id`` is the backing ``close_intents.id`` — the key a
    skipped attempt's own ``close_intent_attempts`` row is recorded against.
    ``attempt_count`` and ``last_attempt_at`` are that intent's backoff history (``0`` and
    ``None`` with no attempt), the input to ``close_intent_is_due``. All three are excluded
    from equality, so two instances compare equal by ``(chunk_id, ref)`` alone; each
    defaults to its unset value."""

    chunk_id: str
    ref: WorkRef
    intent_id: int = field(default=0, compare=False)
    attempt_count: int = field(default=0, compare=False)
    last_attempt_at: datetime | None = field(default=None, compare=False)


@domain_model
@dataclass(frozen=True)
class IntendedMigration:
    """A chunk's standing intent to move onto another graph, consulted —
    never applied eagerly — at its next transition. ``node_name`` is required for
    :attr:`MigrationMode.FORCED` and ``None`` for :attr:`MigrationMode.AUTO`, whose
    landing name is the transition's own destination, resolved at consult time."""

    mode: MigrationMode
    graph_id: str
    node_name: str | None

    @classmethod
    def toward(cls, graph_id: str, node_name: str | None) -> IntendedMigration:
        """The intent toward ``graph_id``: :attr:`MigrationMode.FORCED` onto a named node,
        else :attr:`MigrationMode.AUTO` — the mode is whether a node is named."""
        mode = MigrationMode.FORCED if node_name is not None else MigrationMode.AUTO
        return cls(mode=mode, graph_id=graph_id, node_name=node_name)


@domain_model
@dataclass(frozen=True)
class Chunk:
    """The unit of work that travels the workflow graph."""

    chunk_id: str
    graph_id: str
    work_refs: list[WorkRef]
    minted_at: datetime
    # The chunk's **default** model preference and effort — what a surface
    # declaring neither inherits; empty/``None`` means *express no preference*.
    default_model: list[str] = field(default_factory=list)
    default_effort: str | None = None
    # The chunk's default harness preference, `default_model`'s shape: empty is no preference.
    default_harnesses: list[str] = field(default_factory=list)
    # The chunk's standing intent to migrate onto another graph at its next transition
    # — ``None`` while no intent is set.
    intended_migration: IntendedMigration | None = None

    def originating_ref(self) -> WorkRef | None:
        """The work ref the chunk was minted for — its first; a later fold appends refs after
        it. ``None`` for a chunk holding no work ref."""
        return self.work_refs[0] if self.work_refs else None


def mint_chunk(
    work_refs: Sequence[WorkRef],
    *,
    graph_id: str,
    at: datetime,
    default_model: list[str] | None = None,
    default_effort: str | None = None,
    default_harnesses: list[str] | None = None,
) -> Chunk:
    """Mint a resting chunk pinned to ``graph_id`` holding ``work_refs``, timestamped at
    the caller's own already-stamped ``at`` (``bzh:injected-clock``). Every call site but
    a routine run's own passes neither preference — the empty-preference policy given
    one home here; a routine run is the first to source one, from its own
    routine's defaults."""
    return Chunk(
        chunk_id=Id.mint_at(IdPrefix.CHUNK, at).value,
        graph_id=graph_id,
        work_refs=list(work_refs),
        minted_at=at,
        default_model=list(default_model or []),
        default_effort=default_effort,
        default_harnesses=list(default_harnesses or []),
    )


@domain_model
@dataclass(frozen=True)
class DependencyEdge:
    """One ``chunk_dependencies`` row (shape: ``src/blizzard/hub/store/schema.py``) — a declared
    dependent-on-prerequisite edge. Loaded through its own seam
    (:mod:`~blizzard.hub.domain.chunk.ports.dependencies`), never folded into :class:`ChunkFacts`
    — an edge is a relation between two chunks, not an input to either one's own status."""

    dependency_id: str
    dependent_chunk_id: str
    prerequisite_chunk_id: str
    declared_at: datetime
    declared_by: str
    released_at: datetime | None = None
    released_by: str | None = None

    @property
    def standing(self) -> bool:  # ast-grep-ignore: bzh:property-delegates
        """``True`` while the edge is unreleased."""
        return self.released_at is None

    @staticmethod
    def met_by(prerequisite_status: ChunkStatus | None) -> bool:
        """Whether a prerequisite at ``prerequisite_status`` meets the edge: only ``done`` does. An
        absent prerequisite (``None``) reads ``not_ready``, unmet."""
        return prerequisite_status is ChunkStatus.DONE


# --- Facts that feed the derivations ---------------------------------------
# Each is the domain-object form of a fact row; a hydrating repository fills them.


@domain_model
@dataclass(frozen=True)
class RouteCreatedFact:
    """A ``route.created`` fact — the claim.

    ``seq`` is the monotonic route-event tiebreak (see :meth:`ChunkFacts._has_live_route`): a
    per-chunk counter shared with :class:`RouteReleasedFact`, in real write order."""

    created_at: datetime
    seq: int = 0


@domain_model
@dataclass(frozen=True)
class RouteReleasedFact:
    """A ``route.released`` fact — forcible detach. ``seq`` — see :class:`RouteCreatedFact`."""

    released_at: datetime
    seq: int = 0


@domain_model
@dataclass(frozen=True)
class RouteTokenMintedFact:
    """A ``route_token_minted`` fact — the route capability token, hashed.
    Appended, never rewritten (``bzh:facts-not-status``). ``token_hash`` is the sha256
    hex digest only. ``seq`` shares the per-chunk counter :class:`RouteCreatedFact` uses,
    so it totally orders against a create/release even on a timestamp tie."""

    token_hash: str
    minted_at: datetime
    seq: int = 0


@domain_model
@dataclass(frozen=True)
class LeaseFact:
    """A ``lease.minted`` fact reported up from a runner."""

    epoch: int
    minted_at: datetime


@domain_model
@dataclass(frozen=True)
class EpochOwnerFact:
    """One fencing epoch's recorded owner — ``runner_id`` ``None`` meaning the hub."""

    epoch: int
    runner_id: str | None


@domain_model
@dataclass(frozen=True)
class TransitionFact:
    """A ``transition.recorded`` fact with its target node's executor, resolved by the
    hydrating repository so the derivation stays a pure function. ``from_node_id`` and
    ``choice_name`` describe the edge taken. ``graph_id`` is the graph the transition
    happened in, so node names resolve against it, not the current pin."""

    to_node_id: str
    to_node_executor: Executor
    epoch: int
    recorded_at: datetime
    from_node_id: str | None = None
    choice_name: str | None = None
    graph_id: str | None = None


@domain_model
@dataclass(frozen=True)
class EscalationFact:
    """An ``escalation.recorded`` fact — the system ran out of moves on this chunk.
    Carries the takeover command and its wrapped equivalent. Wrapped-vs-raw rules:
    `blizzard-context:/domain/humans/escalation.md` §The commands an escalation carries. The status derivation keys only
    on ``(epoch, recorded_at)`` supersession."""

    epoch: int
    recorded_at: datetime
    takeover_command: str = ""
    wrapped_takeover_command: str = ""
    cause: str | None = None
    detail: str | None = None


@domain_model
@dataclass(frozen=True)
class QuestionFact:
    """A ``question.asked`` row and whether it has been answered. Open/answered is
    **derived**: a question is open exactly while no ``question.answered`` row exists.
    ``answered`` is resolved by the hydrating repository so the derivation stays a pure
    function; ``question_id`` and ``asked_at`` order a chunk's asks."""

    question_id: str
    asked_at: datetime
    answered: bool = False
    #: The attempt epoch the question was asked at; ``None`` when the hydrating read omits it.
    epoch: int | None = None


@domain_model
@dataclass(frozen=True)
class DecisionFact:
    """A gate's ``decision.submitted`` row and whether anything has closed it. An **open**
    decision — carrying neither its own resolution row nor a restart that superseded it
    (#370) — is what ``waiting_on_human`` derives from. ``resolved`` is computed by the
    hydrating repository so the derivation reads a plain boolean. ``closed`` is a second,
    later state: a fact consumed the decision — a transition, a migration, an escalation or a
    restart — so a decision can be resolved yet not closed while the runner has not moved on."""

    decision_id: str
    submitted_at: datetime
    resolved: bool = False
    closed: bool = False


@domain_model
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


@domain_model
@dataclass(frozen=True)
class HubNodePollFact:
    """A ``hub_node_poll`` row — one pending-poll attempt at a hub command node (#66).
    Append-only. ``epoch`` is the arrival epoch of the current visit to ``node_id``, not
    a fresh one per poll. Pending-ness (:meth:`ChunkFacts.hub_node_pending`) derives from these rows
    plus the newest transition, so a ``kill -9`` between polls resumes from the store."""

    node_id: str
    epoch: int
    polled_at: datetime


@domain_model
@dataclass(frozen=True)
class MigrationFact:
    """A ``chunk_migrations`` fact — a cross-graph migration re-pinned the chunk.
    Its own recorded fact, **never a transition** (``bzh:migration-not-transition``).
    ``landed_node_executor`` is resolved at read time against ``to_graph_id``; ``source``
    attributes the move, and is ``None`` on a row predating it."""

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
        """The node a migration lands on in ``target_graph`` — name-match-else-entry.

        ``bzh:migration-not-transition``'s landing rule. A pure function of the passed-in
        graph (``bzh:domain-takes-objects``)."""
        if from_node_name is not None:
            node = target_graph.node_by_name(from_node_name)
            if node is not None:
                return node.node_id
        return target_graph.entry_node_id


@domain_model
@dataclass(frozen=True)
class RestartFact:
    """A ``chunk.restarted`` fact — an operator forced the chunk onto a node, now (#370).

    A movement fact of its own, never a transition. Its ``epoch`` fences the attempt it
    preempts; ``to_node_executor`` is resolved at read time as a transition's target's is."""

    to_node_id: str
    from_node_id: str | None
    #: The graph ``to_node_id`` belongs to — the *target* graph for a cross-graph move (#371).
    graph_id: str
    epoch: int
    recorded_at: datetime
    #: ``from_node_id``'s own graph, set only when the move crossed one; else ``graph_id`` is both.
    from_graph_id: str | None = None
    to_node_executor: Executor = Executor.RUNNER
    restarted_by: str = ""
    # The gate decision this move closed, or ``None`` — the restart is that decision's
    # resolving fact, the way an escalation's own ``decision_id`` closes one.
    decision_id: str | None = None


class MovementKind(StrEnum):
    """Which fact family put a chunk on the node it currently stands on."""

    TRANSITION = "transition"
    MIGRATION = "migration"
    RESTART = "restart"

    @property
    def rank(self) -> int:
        """The kind's place in an exact ``(recorded_at, epoch)`` tie: each family is recorded *after*
        the movement it supersedes, so the later family outranks."""
        return _MOVEMENT_KIND_RANK[self]

    def order_key(self, recorded_at: datetime, epoch: int) -> tuple[datetime, int, int]:
        """The key movements are ordered by — ``(recorded_at, epoch)``, the kind's rank breaking a tie."""
        return (recorded_at, epoch, self.rank)


_MOVEMENT_KIND_RANK: dict[MovementKind, int] = {
    MovementKind.TRANSITION: 0,
    MovementKind.MIGRATION: 1,
    MovementKind.RESTART: 2,
}


@domain_model
@dataclass(frozen=True)
class Movement:
    """A chunk's newest movement fact, whichever family wrote it — the one owner of
    "which node is this chunk on" (``canon:one-owner``)."""

    kind: MovementKind
    node_id: str | None
    executor: Executor
    # The graph the movement stands on; ``None`` only for a transition recorded without one.
    graph_id: str | None = None


@domain_model
@dataclass(frozen=True)
class RequeueFact:
    """A ``requeue.recorded`` fact — closes an open escalation by supersession."""

    requeued_at: datetime


@domain_model
@dataclass(frozen=True)
class PauseFact:
    """A ``chunk.paused``/``chunk.resumed`` fact — newest-fact-wins."""

    paused: bool
    set_at: datetime
    set_by: str


@domain_model
@dataclass(frozen=True)
class UsageFact:
    """A ``usage.recorded`` fact — one harness invocation's usage/cost telemetry.
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
    #: The invocation's own recorded harness identity — ``None`` recorded
    #: and un-backfilled, never a fresh resolution or a guess from ``model``.
    harness_id: str | None = None
    harness_version: str | None = None
    #: A runner-side subscription estimate, kept apart from ``cost_usd``; ``None`` when none was reported.
    estimated_cost_usd: float | None = None

    def cost_partial(self) -> bool:
        """Whether this row lacks both ``cost_usd`` and ``estimated_cost_usd``: :class:`UsageTotal`'s per-row test."""
        return self.cost_usd is None and self.estimated_cost_usd is None


@domain_model
@dataclass(frozen=True)
class OperationalEvent:
    """One ``event_log`` row — a durable, typed operational fact.
    ``chunk_id``/``runner_id`` are ``None`` for a runner-scoped/hub-authored event,
    respectively; ``detail`` is the event-specific payload, already decoded from JSON. A
    negative ``id`` marks a row :class:`EventFeed` synthesized rather than read."""

    id: int
    recorded_at: datetime
    severity: EventLogSeverity
    kind: str
    runner_id: str | None
    chunk_id: str | None
    lease_id: str | None
    node_name: str | None
    message: str
    detail: dict | None


@domain_model
@dataclass(frozen=True)
class EscalationOpen:
    """One fleet-wide **open** escalation — the input :class:`EventFeed` folds into the
    unified event feed. Carries its own ``chunk_id``, since the read it
    comes from spans every chunk at once."""

    chunk_id: str
    recorded_at: datetime
    takeover_command: str
    cause: str | None = None
    detail: str | None = None

    def matches(
        self,
        *,
        severity: EventLogSeverity | None = None,
        runner_id: str | None = None,
        chunk_id: str | None = None,
        since: datetime | None = None,
    ) -> bool:
        """Whether this escalation survives the event feed's filters, read as the row it projects
        into: it carries the ``needs-human`` severity and names no runner, so a ``severity`` filter
        for any other severity, or any ``runner_id`` filter, excludes it. ``since`` must be aware."""
        if severity is not None and severity != EVENT_LOG_SEVERITY[_EVENT_NEEDS_HUMAN]:
            return False
        if runner_id is not None:
            return False
        if chunk_id is not None and chunk_id != self.chunk_id:
            return False
        return since is None or self.recorded_at >= since


#: Default cap on ``list_events`` — an unbounded read of an append-only table is an unbounded response.
DEFAULT_EVENT_LIST_LIMIT = 200

_EVENT_NEEDS_HUMAN: EventLogKind = "needs-human"

#: The kinds the feed shows only as a projection of other state — a runner's ``event.recorded``
#: fact of one is refused at intake rather than stored beside its own projection.
PROJECTED_EVENT_KINDS: frozenset[EventLogKind] = frozenset({_EVENT_NEEDS_HUMAN})


@domain_model
@dataclass(frozen=True)
class EventFeed:
    """``event_log`` rows unified with every currently-open escalation.

    Sorted newest ``recorded_at`` first, ``id`` descending as the tiebreak, whatever the severity."""

    rows: list[OperationalEvent]

    @classmethod
    def of(cls, events: list[OperationalEvent], escalations: list[EscalationOpen]) -> EventFeed:
        projected = [cls._projected(i, esc) for i, esc in enumerate(escalations)]
        merged = [*events, *projected]
        return cls(sorted(merged, key=lambda e: (e.recorded_at, e.id), reverse=True))

    @staticmethod
    def _projected(index: int, esc: EscalationOpen) -> OperationalEvent:
        """One open escalation as a synthetic row carrying a **negative** ``id`` — it is
        not an ``event_log`` row."""
        return OperationalEvent(
            id=-(index + 1),
            recorded_at=esc.recorded_at,
            severity=EVENT_LOG_SEVERITY[_EVENT_NEEDS_HUMAN],
            kind=_EVENT_NEEDS_HUMAN,
            runner_id=None,
            chunk_id=esc.chunk_id,
            lease_id=None,
            node_name=None,
            # `esc.takeover_command` is not always a resume command, so this promises a
            # way to proceed only when one exists (`blizzard-context:/domain/humans/escalation.md`).
            message=(
                f"chunk {esc.chunk_id} needs a human — see the chunk's escalation for how to proceed"
                if esc.takeover_command
                else f"chunk {esc.chunk_id} needs a human"
            ),
            detail={"cause": esc.cause, "detail": esc.detail} if esc.cause or esc.detail else None,
        )


@domain_model
@dataclass(frozen=True)
class ActivityEntry:
    """One row of the activity feed — a historical fact reshaped into the
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
    cause: ActivityChunkChangeCause | None = None
    graph_id: str | None = None
    # event-logged
    severity: EventLogSeverity | None = None
    kind: str | None = None
    # runner-changed
    by: str | None = None
    reason: str | None = None


@domain_model
@dataclass(frozen=True)
class ActivityFeed:
    """The activity feed's three already-bounded per-source reads, merged.

    Reshapes the event and runner pause-fact reads into the common row, sorts by
    ``(at desc, key desc)`` — ``key`` breaking an exact-instant tie — and caps to ``limit``."""

    rows: list[ActivityEntry]

    @classmethod
    def of(
        cls,
        chunk_changed: Sequence[ActivityEntry],
        events: Sequence[OperationalEvent],
        runner_changed: Sequence[RecordedPause],
        *,
        limit: int,
    ) -> ActivityFeed:
        merged = [
            *chunk_changed,
            *(cls._of_event(e) for e in events),
            *(cls._of_pause(p) for p in runner_changed),
        ]
        merged.sort(key=lambda row: (row.at, row.key), reverse=True)
        return cls(merged[:limit])

    @staticmethod
    def _of_event(row: OperationalEvent) -> ActivityEntry:
        """One ``event_log`` row reshaped into the feed's common row type — its
        ``event-logged`` half."""
        return ActivityEntry(
            type="event-logged",
            key=f"event_log:{row.id}",
            at=row.recorded_at,
            chunk_id=row.chunk_id,
            runner_id=row.runner_id,
            severity=row.severity,
            kind=row.kind,
        )

    @staticmethod
    def _of_pause(fact: RecordedPause) -> ActivityEntry:
        """One runner pause-family fact reshaped into the feed's ``runner-changed`` row: its
        ``kind`` is ``paused``/``resumed``, prefixed ``locally-`` for the runner's own brake."""
        return ActivityEntry(
            type="runner-changed",
            key=fact.key,
            at=fact.at,
            runner_id=fact.runner_id,
            kind=f"{'locally-' if fact.local else ''}{'paused' if fact.paused else 'resumed'}",
            by=fact.by,
            reason=fact.reason,
        )


@domain_model
@dataclass(frozen=True)
class DecisionChoice:
    """One selectable gate outcome."""

    name: str
    description: str


class GateState(StrEnum):
    """Where a gate decision stands: awaiting its choice, decided, or closed with none made."""

    OPEN = "open"
    RESOLVED = "resolved"
    CLOSED_UNDECIDED = "closed_undecided"


class GateVerdict(StrEnum):
    """How a resolution lands on a gate: written, falling through to name the first write's
    winner, or refused."""

    APPLY = "apply"
    REPLAY = "replay"
    REFUSE = "refuse"


#: How a resolution lands from each gate state; a resolved gate falls through so the race names its winner.
GATE_RESOLVE_LEGALITY: Mapping[GateState, GateVerdict] = MappingProxyType(
    {
        GateState.OPEN: GateVerdict.APPLY,
        GateState.RESOLVED: GateVerdict.REPLAY,
        GateState.CLOSED_UNDECIDED: GateVerdict.REFUSE,
    }
)


@domain_model
@dataclass(frozen=True)
class GateDecision:
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
    #: The runner whose configuration imposed this gate; ``None`` when the graph declared it.
    imposed_by_runner_id: str | None = None

    @property
    def resolved(self) -> bool:  # ast-grep-ignore: bzh:property-delegates
        return self.resolved_choice is not None

    @property
    def is_open(self) -> bool:
        """Whether the gate still awaits its resolution: neither resolved nor carried by a transition."""
        return self._awaits_resolution()

    @property
    def closed_undecided(self) -> bool:
        """Whether a fact closed the gate with no choice made — an operator restart moving the
        chunk off it. No choice is invented for such a gate."""
        return self._transitioned_undecided()

    def state(self) -> GateState:
        """The gate's standing: resolved once a choice is recorded, else closed undecided once a
        transition carried it, else open."""
        if self.resolved:
            return GateState.RESOLVED
        if self.transitioned:
            return GateState.CLOSED_UNDECIDED
        return GateState.OPEN

    def resolve_verdict(self) -> GateVerdict:
        """How a resolution lands from this gate's state, per :data:`GATE_RESOLVE_LEGALITY`."""
        return GATE_RESOLVE_LEGALITY[self.state()]

    def _awaits_resolution(self) -> bool:
        return not self.resolved and not self.transitioned

    def _transitioned_undecided(self) -> bool:
        return not self.resolved and self.transitioned

    def require_resolvable(self, *, choice: str, chunk_status: ChunkStatus) -> None:
        """Refuse a resolution this gate cannot take, in order: a ``choice`` outside the gate's
        own (:class:`NotADecisionChoice`); then, only while unresolved, a gate already closed —
        undecided by a restart, or by its chunk ending (:class:`DecisionClosed`). Once
        resolved every retry falls through, so the first-write-wins race names the winner."""
        if choice not in {c.name for c in self.choices}:
            valid = ", ".join(c.name for c in self.choices)
            raise NotADecisionChoice(f"`{choice}` is not a choice of this decision (one of: {valid})")
        verdict = self.resolve_verdict()
        if verdict is GateVerdict.REPLAY:
            return
        if verdict is GateVerdict.REFUSE:
            raise DecisionClosed(f"decision {self.decision_id} was closed undecided by a restart")
        if not verb_legal_from(ChunkVerb.RESOLVE_DECISION, chunk_status):
            raise DecisionClosed(f"decision {self.decision_id} closed: chunk {self.chunk_id} is {chunk_status.value}")

    def resolving_refusal(self, *, chunk_id: str, node_id: str, node_name: str, choice: str) -> str | None:
        """Why a resolving transition naming this gate cannot leave ``node_id``, or ``None``: the
        gate belongs to another chunk or node, is not yet resolved, or resolved to another choice."""
        if self.chunk_id != chunk_id or self.node_id != node_id:
            return f"decision {self.decision_id} does not match node `{node_name}`"
        if self.resolved_choice is None:
            return f"decision {self.decision_id} is not yet resolved"
        if choice != self.resolved_choice:
            return f"choice `{choice}` is not the resolved choice `{self.resolved_choice}`"
        return None


class NotADecisionChoice(ValueError):
    """A resolution named a choice the gate does not offer."""


class DecisionClosed(Exception):
    """A resolution reached a gate something already closed undecided: an operator restart moved
    the chunk off it, or the chunk ended."""


def holds_claim(status: ChunkStatus) -> bool:
    """Whether a chunk at this status still holds the route it may be carrying.
    Terminal outranks route liveness: a terminal transition from a runner node stamps no
    ``route.released``, so the raw route fact outlives it."""
    return status not in TERMINAL_STATUSES


class ChunkVerb(StrEnum):
    """A verb that reads a chunk's derived status to decide whether it is legal — the rows of
    :data:`CHUNK_VERB_LEGALITY`. A verb names the chunk's role when it acts on one end of a
    relation (``DECLARE_DEPENDENCY`` is the dependent's).
    An ephemeral (grouped-away or deleted) chunk has no status: every verb reads it as unknown."""

    #: Declare a dependency, as the dependent.
    DECLARE_DEPENDENCY = "declare-dependency"
    #: Let a new ingest mint over a work ref this chunk holds: only once the holder is finished.
    INGEST_HELD_WORK_REF = "ingest-held-work-ref"
    #: The operator's promote; on an already-promoted chunk, a replay that writes nothing whatever the status.
    PROMOTE = "promote"
    #: The operator's per-chunk pause brake.
    PAUSE = "pause"
    #: Lift the operator's per-chunk pause brake.
    RESUME = "resume"
    #: The operator's terminal abandonment.
    STOP = "stop"
    #: The operator's manual completion. At ``done`` it is a replay that writes nothing.
    COMPLETE = "complete"
    #: The operator's forced move onto a node at a fresh epoch, same graph or across graphs.
    RESTART = "restart"
    #: Delete the chunk outright.
    DELETE = "delete"
    #: Take part in a group, as the survivor or as a chunk folded into it.
    GROUP = "group"
    #: Edit the graph pin (also refused once the chunk has moved).
    EDIT_GRAPH_PIN = "edit-graph-pin"
    #: Edit the default model, effort, or harnesses.
    EDIT_DEFAULTS = "edit-defaults"
    #: Set, overwrite, or clear the intended migration.
    EDIT_INTENDED_MIGRATION = "edit-intended-migration"
    #: Be named in a reorder of the ``ready`` queue.
    REORDER_READY = "reorder-ready"
    #: Be named in a reorder of the ``not_ready`` backlog.
    REORDER_BACKLOG = "reorder-backlog"
    #: A runner's claim of a ready chunk; a chunk whose live route is held refuses it as a lost race first.
    CLAIM = "claim"
    #: Rotate the live route's capability token; a route left on an ended chunk confers no tenure.
    REKEY_ROUTE_TOKEN = "rekey-route-token"
    #: Read the current node's envelope; an ended chunk has no node-step to run.
    READ_ENVELOPE = "read-envelope"
    #: The hub requeue, superseding the open escalation.
    REQUEUE = "requeue"
    #: Resolve a still-open gate decision; the chunk ending closes it.
    RESOLVE_DECISION = "resolve-decision"
    #: Answer a node question; the chunk ending leaves no session to hear it.
    ANSWER_QUESTION = "answer-question"
    HUB_ADVANCE = "hub-advance"


_EVERY_STATUS: frozenset[ChunkStatus] = frozenset(ChunkStatus)
_NON_TERMINAL: frozenset[ChunkStatus] = _EVERY_STATUS - TERMINAL_STATUSES

#: The statuses each :class:`ChunkVerb` is legal from; any other refuses with the verb's own refusal.
CHUNK_VERB_LEGALITY: Mapping[ChunkVerb, frozenset[ChunkStatus]] = MappingProxyType(
    {
        ChunkVerb.DECLARE_DEPENDENCY: PRE_CLAIM_STATUSES,
        ChunkVerb.INGEST_HELD_WORK_REF: TERMINAL_STATUSES,
        ChunkVerb.PROMOTE: _NON_TERMINAL,
        ChunkVerb.PAUSE: _NON_TERMINAL - {ChunkStatus.DELIVERING},
        ChunkVerb.RESUME: _EVERY_STATUS,
        ChunkVerb.STOP: _NON_TERMINAL,
        ChunkVerb.COMPLETE: _EVERY_STATUS,
        ChunkVerb.RESTART: _NON_TERMINAL,
        ChunkVerb.DELETE: PRE_CLAIM_STATUSES,
        ChunkVerb.GROUP: PRE_CLAIM_STATUSES,
        ChunkVerb.EDIT_GRAPH_PIN: PRE_CLAIM_STATUSES,
        ChunkVerb.EDIT_DEFAULTS: PRE_CLAIM_STATUSES,
        ChunkVerb.EDIT_INTENDED_MIGRATION: _NON_TERMINAL,
        ChunkVerb.REORDER_READY: frozenset({ChunkStatus.READY}),
        ChunkVerb.REORDER_BACKLOG: frozenset({ChunkStatus.NOT_READY}),
        ChunkVerb.CLAIM: frozenset({ChunkStatus.READY}),
        ChunkVerb.REKEY_ROUTE_TOKEN: _NON_TERMINAL,
        ChunkVerb.READ_ENVELOPE: _NON_TERMINAL,
        ChunkVerb.REQUEUE: frozenset({ChunkStatus.NEEDS_HUMAN}),
        ChunkVerb.RESOLVE_DECISION: _NON_TERMINAL,
        ChunkVerb.ANSWER_QUESTION: _NON_TERMINAL,
        ChunkVerb.HUB_ADVANCE: _NON_TERMINAL - {ChunkStatus.NOT_READY},
    }
)


def verb_legal_from(verb: ChunkVerb, status: ChunkStatus) -> bool:
    """Whether ``verb`` is legal on a chunk at ``status``, per :data:`CHUNK_VERB_LEGALITY`."""
    return status in CHUNK_VERB_LEGALITY[verb]


def holds_work_refs(status: ChunkStatus) -> bool:
    """Whether a chunk at this status still holds its work refs against a new ingest — a live
    holder. The inverse of :attr:`ChunkVerb.INGEST_HELD_WORK_REF`'s window, read by every
    live-holder derivation."""
    return not verb_legal_from(ChunkVerb.INGEST_HELD_WORK_REF, status)


@domain_model
@dataclass(frozen=True)
class ChunkFacts:
    """Every fact a chunk's status derives from, already loaded. The derivation is a
    pure function of this aggregate — the unit tests build it directly, no store."""

    minted: bool
    promoted: bool = False
    stopped: bool = False
    # ``chunk.stopped``'s own instant; ``None`` exactly when not stopped.
    stopped_at: datetime | None = None
    # ``chunk.completed`` — an operator's manual completion, named for the
    # operator since :meth:`completed_at` already names the render-only derived instant.
    operator_completed: bool = False
    operator_completed_at: datetime | None = None
    # ``delivery.landed`` — the whole-chunk terminal fact, informational only
    # (``bzh:facts-not-status``): DONE derives from the terminal transition, not this.
    delivery_landed: bool = False
    # The chunk's per-repo ``delivery.repo_landed`` facts, independent of whether delivery
    # has reached a terminal transition; the derivation reads only non-emptiness.
    landed_repos: frozenset[str] = field(default_factory=frozenset)
    escalations: list[EscalationFact] = field(default_factory=list)
    leases: list[LeaseFact] = field(default_factory=list)
    # The chunk's epoch owners — a claim's reservation among them, so it raises the fence.
    epoch_owners: list[EpochOwnerFact] = field(default_factory=list)
    transitions: list[TransitionFact] = field(default_factory=list)
    routes_created: list[RouteCreatedFact] = field(default_factory=list)
    routes_released: list[RouteReleasedFact] = field(default_factory=list)
    route_tokens_minted: list[RouteTokenMintedFact] = field(default_factory=list)
    questions: list[QuestionFact] = field(default_factory=list)
    decisions: list[DecisionFact] = field(default_factory=list)
    requeues: list[RequeueFact] = field(default_factory=list)
    # The chunk's cross-graph migration facts — each re-pins the chunk and
    # re-queues it, superseding an earlier transition for the terminal/hub-node checks.
    migrations: list[MigrationFact] = field(default_factory=list)
    # The chunk's operator restart facts (#370) — a third movement family beside the two above.
    restarts: list[RestartFact] = field(default_factory=list)
    pauses: list[PauseFact] = field(default_factory=list)
    usage: list[UsageFact] = field(default_factory=list)
    # The chunk's recorded delivery kick-backs (#64) — feeds :meth:`ChunkFacts.bounce_count` /
    # :meth:`ChunkFacts.bounces_over_cap` and the chunk-detail bounce history. Never a status.
    bounces: list[BounceFact] = field(default_factory=list)
    # The chunk's recorded hub-node poll attempts (#66) — feeds :meth:`ChunkFacts.hub_node_pending`.
    # Never a status: pending is a facet of ``delivering``.
    hub_node_polls: list[HubNodePollFact] = field(default_factory=list)

    @staticmethod
    def or_default(facts: ChunkFacts | None) -> ChunkFacts:
        """``facts``, or the unminted default — the one fallback every reader of a
        possibly-absent pre-write ``ChunkFacts`` applies, instead of repeating it."""
        return facts if facts is not None else ChunkFacts(minted=True)

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
        """The chunk's newest cross-graph migration fact, or ``None``.

        Ordered by ``(recorded_at, epoch)`` — the same key ``newest_transition`` uses."""
        if not self.migrations:
            return None
        return max(self.migrations, key=lambda m: (m.recorded_at, m.epoch))

    def newest_restart(self) -> RestartFact | None:
        """The chunk's newest operator restart fact, or ``None`` (#370).

        Ordered by ``(recorded_at, epoch)`` — the same key ``newest_transition`` uses."""
        if not self.restarts:
            return None
        return max(self.restarts, key=lambda r: (r.recorded_at, r.epoch))

    def restart_history(self) -> list[RestartFact]:
        """The chunk's operator restarts oldest first, by the ``(recorded_at, epoch)`` key
        :meth:`newest_restart` selects the tail of."""
        return sorted(self.restarts, key=lambda r: (r.recorded_at, r.epoch))

    def transition_graph_off_pin(self, pin_graph_id: str) -> str | None:
        """The graph the newest transition was recorded on when it is not ``pin_graph_id`` — the graph a
        cross-graph move left, which that transition's own nodes resolve against — else ``None``."""
        transition = self.newest_transition()
        if transition is None or transition.graph_id is None or transition.graph_id == pin_graph_id:
            return None
        return transition.graph_id

    def admits(self, verb: ChunkVerb) -> bool:
        """Whether ``verb`` is legal from this chunk's derived status (:data:`CHUNK_VERB_LEGALITY`)."""
        return verb_legal_from(verb, self.status())

    def latest_epoch(self) -> int | None:
        """The chunk's latest fencing epoch — the newest across its leases, restarts, and
        epoch owners.

        A restart mints an epoch with no attempt behind it (#370), and a claim reserves one
        before its lease is reported: the fence has to rise the moment either lands."""
        epochs = (
            [lease.epoch for lease in self.leases]
            + [restart.epoch for restart in self.restarts]
            + [owner.epoch for owner in self.epoch_owners]
        )
        return max(epochs) if epochs else None

    def latest_movement(self, as_of: datetime | None = None) -> Movement | None:
        """The chunk's newest movement fact, or ``None`` while it has not moved at all.

        Ordered by :meth:`MovementKind.order_key` — ``(recorded_at, epoch)``, the kind's own rank
        breaking an exact tie: each family is recorded *after* the movement it supersedes. With
        ``as_of``, the newest movement recorded at or before that instant: each family is filtered
        before it is ranked."""

        def seen(recorded_at: datetime) -> bool:
            return as_of is None or recorded_at <= as_of

        ranked: list[tuple[tuple[datetime, int, int], Movement]] = []
        transitions = [t for t in self.transitions if seen(t.recorded_at)]
        if transitions:
            t = max(transitions, key=lambda t: (t.recorded_at, t.epoch))
            movement = Movement(MovementKind.TRANSITION, t.to_node_id, t.to_node_executor, t.graph_id)
            ranked.append((MovementKind.TRANSITION.order_key(t.recorded_at, t.epoch), movement))
        migrations = [m for m in self.migrations if seen(m.recorded_at)]
        if migrations:
            m = max(migrations, key=lambda m: (m.recorded_at, m.epoch))
            movement = Movement(MovementKind.MIGRATION, m.landed_node_id, m.landed_node_executor, m.to_graph_id)
            ranked.append((MovementKind.MIGRATION.order_key(m.recorded_at, m.epoch), movement))
        restarts = [r for r in self.restarts if seen(r.recorded_at)]
        if restarts:
            r = max(restarts, key=lambda r: (r.recorded_at, r.epoch))
            movement = Movement(MovementKind.RESTART, r.to_node_id, r.to_node_executor, r.graph_id)
            ranked.append((MovementKind.RESTART.order_key(r.recorded_at, r.epoch), movement))
        if not ranked:
            return None
        return max(ranked, key=lambda entry: entry[0])[1]

    def current_node_id(self) -> str | None:
        """The chunk's current node id — the newest movement fact's target, else ``None``.

        ``None`` means the chunk has not yet moved, and the caller resolves the pinned
        graph's entry node."""
        movement = self.latest_movement()
        return movement.node_id if movement is not None else None

    def current_node(self, graph: Graph) -> Node | None:
        """The chunk's current node on ``graph`` — the newest movement's target, else the graph's
        entry node. ``None`` when that id names no node there (the reserved terminal)."""
        return graph.node_by_id(self.current_node_id() or graph.entry_node_id)

    def hub_advance_node(self, graph: Graph) -> Node | None:
        """The generic hub command node a hub-advance may run for this chunk, or ``None``: the
        chunk must stand at one, by its newest movement, at a status :attr:`ChunkVerb.HUB_ADVANCE`
        is legal from — never one resting un-promoted, nor one that has ended."""
        node_id = self.current_node_id()
        node = graph.node_by_id(node_id) if node_id is not None else None
        if node is None or not node.is_hub_command_node:
            return None
        return node if verb_legal_from(ChunkVerb.HUB_ADVANCE, self.status()) else None

    def awaits_exit_from(self, node: Node, *, epoch: int) -> bool:
        """``node``'s visit at ``epoch`` has recorded no exit yet and no newer epoch has superseded
        it — the replay key ``(from_node, epoch)`` the exit transition and the migration are
        recorded under, so a replay after the node's exit matches nothing. Status stays with each
        caller."""
        left = any(t.from_node_id == node.node_id and t.epoch == epoch for t in self.transitions) or any(
            m.from_node_id == node.node_id and m.epoch == epoch for m in self.migrations
        )
        return not left and (self.latest_epoch() or 0) <= epoch

    def accepted_transition_target(self, *, from_node_id: str, epoch: int) -> str | None:
        """The ``to_node_id`` of the transition already recorded out of ``from_node_id`` at
        ``epoch`` — the replay key — or ``None``."""
        return next(
            (t.to_node_id for t in self.transitions if t.from_node_id == from_node_id and t.epoch == epoch), None
        )

    def epoch_floor(self) -> int:
        """The epoch a fresh node-step envelope carries — the latest fencing epoch, ``0`` before any."""
        return self.latest_epoch() or 0

    def escalated_at(self, epoch: int) -> bool:
        """Whether any escalation, open or superseded, was recorded at ``epoch``."""
        return any(e.epoch == epoch for e in self.escalations)

    def open_escalation_at(self, epoch: int) -> EscalationFact | None:
        """The open escalation, when the attempt at ``epoch`` raised it; else ``None``."""
        escalation = self.open_escalation()
        return escalation if escalation is not None and escalation.epoch == epoch else None

    def open_questions_at(self, epoch: int) -> list[QuestionFact]:
        """The open questions the attempt at ``epoch`` asked, oldest first."""
        return [q for q in self.open_questions() if q.epoch == epoch]

    def restarted_past(self, epoch: int) -> bool:
        """Whether an operator restart minted an epoch above ``epoch`` — the attempt at ``epoch``
        and its parked session were superseded."""
        return any(restart.epoch > epoch for restart in self.restarts)

    def entered_by_restart(self) -> bool:
        """The chunk's current node visit was forced by an operator restart (#370).

        True until the chunk moves again, so every re-entry into that visit — the first
        one and any crash-recovery repeat — runs on a freshly minted session."""
        movement = self.latest_movement()
        return movement is not None and movement.kind is MovementKind.RESTART

    def newest_transition_is_terminal(self) -> bool:
        """The newest accepted transition's target is the reserved terminal (``done``, #63).

        The **sole** DONE trigger — reaching the terminal, not any landed/closed fact. A later
        movement of any other family supersedes the transition entirely: a
        re-queued chunk is never DONE off a superseded terminal."""
        movement = self.latest_movement()
        if movement is None or movement.kind is not MovementKind.TRANSITION:
            return False
        return movement.node_id == RESERVED_TERMINAL

    def _latest_movement_enters_hub_node(self) -> bool:
        """The chunk's newest movement landed it on a hub-executed node.

        A migration's landing node can itself be hub-executed, and so can a
        restart's target — either derives ``delivering`` rather than ``ready``."""
        movement = self.latest_movement()
        return movement is not None and movement.executor is Executor.HUB

    def _operator_completion_outranks_stop(self) -> bool:
        """A ``chunk.completed`` fact outranks the stop it follows — the one way a
        stopped chunk still reaches ``done``. Ties go to the completion, the same convention
        :meth:`latest_movement` states for its own tie: recorded *after* the stop it
        supersedes, so ``>=`` against ``stopped_at``, not ``>``."""
        if not self.operator_completed:
            return False
        if not self.stopped:
            return True
        assert self.operator_completed_at is not None  # invariant: set iff operator_completed
        assert self.stopped_at is not None  # invariant: set iff stopped
        return self.operator_completed_at >= self.stopped_at

    def status(self) -> ChunkStatus:
        """Derive a chunk's single status from its facts, first match wins. ``done`` is the
        **only** terminal (#63): reached via the terminal transition, an operator's manual
        completion — not the landed
        fact, since an authored ``merged -> <node>`` edge can land every repo and keep the
        chunk running post-merge."""
        return self._status_with(paused=self._is_paused())

    def status_if_paused(self) -> ChunkStatus:
        """The status this chunk derives once an operator Pause on it settles — :meth:`status`'s
        own ladder with the pause fact holding. Its current status where
        :attr:`ChunkVerb.PAUSE` refuses the pause, and where a rung above ``paused`` (a human
        gate) outranks the pause fact."""
        if not self.admits(ChunkVerb.PAUSE):
            return self.status()
        return self._status_with(paused=True)

    def _status_with(self, *, paused: bool) -> ChunkStatus:
        """:meth:`status`'s precedence ladder, with whether the pause fact holds as an input."""
        if self.stopped and not self._operator_completion_outranks_stop():
            return ChunkStatus.STOPPED
        if self.operator_completed or self.newest_transition_is_terminal():
            return ChunkStatus.DONE
        if self._has_open_escalation():
            return ChunkStatus.NEEDS_HUMAN
        if self._is_waiting_on_human():
            # An open question or an open decision (gate); the
            # reap clock is stopped and the answer/resolution flips it back.
            return ChunkStatus.WAITING_ON_HUMAN
        if paused:
            # Below the human-gated states (a chunk both parked on a question and paused
            # is still, first, waiting on a human) and above delivering/running.
            return ChunkStatus.PAUSED
        if not self.promoted and not self._has_live_route():
            # An un-promoted chunk rests ``not_ready``. Above the hub-node test, so a restart that re-aims a
            # resting chunk onto a hub-executed node does not promote it: only an explicit promote moves it on.
            return ChunkStatus.NOT_READY
        if self._latest_movement_enters_hub_node():
            return ChunkStatus.DELIVERING
        if self._has_live_route():
            return ChunkStatus.RUNNING
        return ChunkStatus.READY

    def is_ready_but_for_pause(self) -> bool:
        """Whether the chunk would derive ``ready`` with its pause lifted — promoted, no live
        route, not landed on a hub node — so a pause is the only thing withholding it
        from the ready queue. Answered by :meth:`status` itself, never a recomposed branch."""
        return replace(self, pauses=[]).status() is ChunkStatus.READY

    def completed_at(self) -> datetime | None:
        """The instant a terminal chunk finished, or ``None`` — render-only,
        never a status. Mirrors ``status``'s branch order (the operator completion
        included) so the two never disagree."""
        if self.stopped and not self._operator_completion_outranks_stop():
            return self.stopped_at
        if self.operator_completed:
            return self.operator_completed_at
        terminal_transition = self.newest_transition() if self.newest_transition_is_terminal() else None
        return terminal_transition.recorded_at if terminal_transition is not None else None

    def finished_before(self, instant: datetime) -> bool:
        """Whether the chunk is ``done`` and finished strictly before ``instant`` — the board's
        done-window cut. A ``stopped`` chunk never reads as finished here."""
        if self.status() is not ChunkStatus.DONE:
            return False
        completed_at = self.completed_at()
        return completed_at is not None and completed_at < instant

    def open_escalation(self) -> EscalationFact | None:
        """The newest escalation nothing later superseded, or ``None``.

        Closed by supersession, never a resolution fact: a later lease mint, a
        ``requeue.recorded`` or an operator restart (#370) hands the work back to the fleet,
        and a later **completion** — stopped or done — is the chunk ending without one (#293)."""
        if not self.escalations:
            return None
        newest = max(self.escalations, key=lambda e: e.recorded_at)
        superseding = (
            *(lease.minted_at for lease in self.leases),
            *(rq.requeued_at for rq in self.requeues),
            *(restart.recorded_at for restart in self.restarts),
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

    def unclosed_decision(self) -> DecisionFact | None:
        """The newest gate decision no fact has consumed yet, resolved or not, or ``None``.

        What a move that consumes the gate closes: a person's resolution ends the wait
        (:meth:`open_decision`) but leaves the decision for the runner to act on until a
        transition, migration, escalation or restart closes it."""
        unclosed = [d for d in self.decisions if not d.closed]
        if not unclosed:
            return None
        return max(unclosed, key=lambda d: d.submitted_at)

    def has_open_decision(self) -> bool:
        """True iff a gate's decision is unresolved — no resolution flips it off."""
        return self.open_decision() is not None

    def open_pause(self) -> PauseFact | None:
        """The newest pause fact iff it currently reads paused, else ``None``.

        Reads the fact directly rather than the derived status: PAUSED sits below the
        human-gated states, so a status-keyed reader would miss a chunk that is paused
        *and* parked on a question."""
        return self.pauses[-1] if self.pauses and self.pauses[-1].paused else None

    def _has_open_escalation(self) -> bool:
        """An escalation nothing later superseded — supersession, not resolution."""
        return self.open_escalation() is not None

    def _is_waiting_on_human(self) -> bool:
        """An open question or an open decision parks the chunk."""
        return bool(self.open_questions()) or self.has_open_decision()

    def _is_paused(self) -> bool:
        """Paused derives from the newest pause fact, newest-fact-wins."""
        return self.open_pause() is not None

    @property
    def routes(self) -> RouteHistory:
        """The chunk's route facts as the object that derives their liveness."""
        return RouteHistory.of(self)

    def _has_live_route(self) -> bool:
        """A ``route.created`` with no later ``route.released``."""
        return self.routes.newest is not None

    def has_landed_repos(self, artifacts: Sequence[StoredArtifact] = ()) -> bool:
        """True iff any repo has landed for this chunk — informational, never a status (#63).

        ``artifacts`` carries the generic ``merged/<repo>`` marker convention (#67) — the
        current landing truth; the fact inputs are read alongside for back-compat, so a
        historical chunk still reads landed."""
        return self.landed_with(LandedRepos.of(artifacts).names)

    def landed_with(self, repo_names: Collection[str]) -> bool:
        """True iff any repo has landed for this chunk: a ``merged/<repo>`` marker names one
        (``repo_names``), or the back-compat delivery facts already record a landing."""
        return self.delivery_landed or bool(self.landed_repos) or bool(repo_names)

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


def is_landed_revision(content: str) -> bool:
    """Whether a landing marker's content names a revision: blank content records no landing."""
    return bool(content.strip())


@domain_model
@dataclass(frozen=True)
class LandedRepos:
    """Repos landed via a hub command node's ``merged/<repo>`` marker artifact (#67).

    No engine code names a "deliver" node, so a chunk's landed detail is read off its
    own node artifacts rather than a privileged fact family. ``artifacts`` arrive in durable
    write order, so the newest-written marker decides a repo's sha."""

    names: frozenset[str]
    shas: dict[str, str]

    @classmethod
    def of(cls, artifacts: Sequence[StoredArtifact]) -> LandedRepos:
        shas = {
            a.name.removeprefix(_MARKER_PREFIX): a.data.strip()
            for a in artifacts
            if a.name.startswith(_MARKER_PREFIX) and is_landed_revision(a.data)
        }
        return cls(frozenset(shas), shas)


@domain_model
@dataclass(frozen=True)
class RouteHistory:
    """A chunk's route facts and the liveness they derive."""

    created: list[RouteCreatedFact] = field(default_factory=list)
    released: list[RouteReleasedFact] = field(default_factory=list)
    tokens_minted: list[RouteTokenMintedFact] = field(default_factory=list)

    @classmethod
    def of(cls, facts: ChunkFacts) -> RouteHistory:
        return cls(facts.routes_created, facts.routes_released, facts.route_tokens_minted)

    @property
    def newest(self) -> RouteCreatedFact | None:  # ast-grep-ignore: bzh:property-delegates
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
    def newest_token(self) -> RouteTokenMintedFact | None:  # ast-grep-ignore: bzh:property-delegates
        """The chunk's live route capability token, or ``None`` if unclaimed/released.

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


@domain_model
@dataclass(frozen=True)
class ChunkChange:
    """A ``chunk-changed`` frame's derived content — the current status
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
            if facts.transition_graph_off_pin(graph.graph_id) is not None and from_graph is not None:
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


@domain_model
@dataclass(frozen=True)
class UsageTotal:
    """A usage/cost total summed at read time, never a stored column. **The one canonical owner of the
    lower-bound + PARTIAL cost contract** (``canon:one-owner``): ``cost_usd``/``estimated_cost_usd`` sum only
    billed/estimated rows (the latter ``None`` when none contributed); ``cost_partial`` is ``True`` iff some row
    carries neither a billed nor an estimated amount, ``billed_partial`` iff some row carries no billed one."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool
    estimated_cost_usd: float | None = None
    billed_partial: bool = False
    #: The billed sum, kept apart from ``cost_usd`` (``0.0`` when no row was billed) — ``None`` when no row carried one.
    billed_cost_usd: float | None = None

    @classmethod
    def of(cls, rows: list[UsageFact]) -> UsageTotal:
        """Sum ``rows`` into one total — one chunk's own facts, or an arbitrary set
        (the fleet spend-since window)."""
        estimated_rows = [u.estimated_cost_usd for u in rows if u.estimated_cost_usd is not None]
        billed_rows = [u.cost_usd for u in rows if u.cost_usd is not None]
        return cls(
            input_tokens=sum(u.input_tokens for u in rows),
            output_tokens=sum(u.output_tokens for u in rows),
            cache_read_tokens=sum(u.cache_read_tokens for u in rows),
            cache_create_tokens=sum(u.cache_create_tokens for u in rows),
            cost_usd=sum(billed_rows),
            billed_cost_usd=sum(billed_rows) if billed_rows else None,
            estimated_cost_usd=sum(estimated_rows) if estimated_rows else None,
            cost_partial=any(u.cost_partial() for u in rows),
            billed_partial=any(u.cost_usd is None for u in rows),
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
        estimated_cost_usd_sum: float,
        estimated_rows: int,
        both_null_rows: int,
        null_cost_rows: int,
        billed_rows: int,
    ) -> UsageTotal:
        """Build from sums a caller already grouped in SQL, applying this same contract
        rather than a second, independent one: ``cost_usd_sum``/``estimated_cost_usd_sum``
        are the caller's own skip-null sums; the ``*_rows`` counts carry an estimate, neither
        amount, and no billed amount respectively, and ``billed_rows`` counts those carrying one."""
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_create_tokens=cache_create_tokens,
            cost_usd=cost_usd_sum,
            billed_cost_usd=cost_usd_sum if billed_rows > 0 else None,
            estimated_cost_usd=estimated_cost_usd_sum if estimated_rows > 0 else None,
            cost_partial=both_null_rows > 0,
            billed_partial=null_cost_rows > 0,
        )


@domain_model
@dataclass(frozen=True)
class FleetSummary:
    """Fleet-pulse counts — every chunk's derived status folded to four
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


@domain_model
@dataclass(frozen=True)
class NodeQuestion:
    """A durable question row with its derived answer *and delivery* state. Every state
    here is **derived**: answered exactly while an answer row exists (the winning
    first-write-wins CAS row), delivered exactly while an ``answer_deliveries`` row
    exists — answered says a human decided, delivered says the agent heard."""

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
    harness_id: str | None = None

    def require_answerable(self, chunk_status: ChunkStatus) -> None:
        """Refuse an answer once the question's chunk has ended (:class:`QuestionClosed`): no
        session remains to hear it (:attr:`ChunkVerb.ANSWER_QUESTION`)."""
        if not verb_legal_from(ChunkVerb.ANSWER_QUESTION, chunk_status):
            raise QuestionClosed(f"question {self.question_id} closed: chunk {self.chunk_id} is {chunk_status.value}")

    def delivery(self, *, chunk_id: str, superseded_by_restart: bool) -> tuple[QuestionDelivery, str | None]:
        """How an ``answer.delivered`` report lands on this question, with a refusal's detail.
        Delivery is the answer's return trip to the asking session, so it refuses a report naming
        another chunk, a question not yet answered, and one a restart superseded (answered by the
        system, never delivered). A repeat of a delivered question is a replay that writes nothing."""
        if chunk_id != self.chunk_id:
            return QuestionDelivery.REFUSE, f"question {self.question_id} belongs to chunk {self.chunk_id}"
        state = self.state(superseded_by_restart=superseded_by_restart)
        verdict = QUESTION_DELIVERY_LEGALITY[state]
        if verdict is not QuestionDelivery.REFUSE:
            return verdict, None
        if state is QuestionState.SUPERSEDED:
            return verdict, f"question {self.question_id} was superseded by a restart"
        return verdict, f"question {self.question_id} is not answered"

    def state(self, *, superseded_by_restart: bool) -> QuestionState:
        """The question's standing: superseded once a restart answered it for the system, else
        delivered, answered, or open by its derived rows."""
        if superseded_by_restart:
            return QuestionState.SUPERSEDED
        if not self.answered:
            return QuestionState.OPEN
        if self.delivered:
            return QuestionState.DELIVERED
        return QuestionState.ANSWERED


class QuestionDelivery(StrEnum):
    """How an ``answer.delivered`` report lands: written, a replay that writes nothing, or refused."""

    RECORD = "record"
    REPLAY = "replay"
    REFUSE = "refuse"


class QuestionState(StrEnum):
    """Where a question stands on the answer's return trip to its asking session."""

    OPEN = "open"
    ANSWERED = "answered"
    DELIVERED = "delivered"
    SUPERSEDED = "superseded"


#: How an ``answer.delivered`` report lands from each question state: only an answered question records it.
QUESTION_DELIVERY_LEGALITY: Mapping[QuestionState, QuestionDelivery] = MappingProxyType(
    {
        QuestionState.OPEN: QuestionDelivery.REFUSE,
        QuestionState.ANSWERED: QuestionDelivery.RECORD,
        QuestionState.DELIVERED: QuestionDelivery.REPLAY,
        QuestionState.SUPERSEDED: QuestionDelivery.REFUSE,
    }
)


class QuestionClosed(Exception):
    """An answer reached a question whose chunk has ended."""


@domain_model
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


# --- Work item repository seam (bzh:repository-split) -----------


class IReadWorkItemRepository(Protocol):
    """Read-only hub-owned work item operations."""

    def get(self, source: str, ref: str) -> HubWorkItem | None:
        """The item at ``(source, ref)``, open or closed, or ``None`` when no such
        item was ever allocated."""
        ...

    def list(self, source: str, *, limit: int = 200) -> list[HubWorkItem]:
        """Up to ``limit`` items at ``source``, newest first (a total order —
        ``work_item_id`` breaks a same-instant ``created_at`` tie, ULIDs sorting lexically
        by creation), open and closed alike — bounded the same way every other operator
        feed in this hub is (the activity feed, ``/api/events``)."""
        ...

    def get_many(self, pointers: Sequence[WorkRef]) -> dict[WorkRef, HubWorkItem]:
        """``get``'s batched sibling (`bzh:bulk-reconstitution`) — every requested
        pointer's item, keyed by pointer. A pointer naming no item is absent, the same
        as ``get`` returning ``None`` for it."""
        ...


class IWriteWorkItemRepository(IReadWorkItemRepository, Protocol):
    """Read-write variant — ``allocate_ref``, ``create_with_chunk``, ``edit`` and
    ``close``. Every hub item's creation mints its resting
    chunk in the same transaction; there is no chunkless filing path."""

    def allocate_ref(self, source: str) -> str:
        """Allocate a fresh, monotonic, never-reused ``ref`` for ``source``, in its own
        transaction — split out from the insert so a caller can hold the
        ``ref`` before the row it feeds exists, and mint a chunk against that pointer.
        May skip one on a crash between this call and the insert it feeds — a
        gap-tolerant contract, the same one a DB sequence carries."""
        ...

    def create_with_chunk(
        self,
        *,
        pointer: WorkRef,
        title: str,
        body: str,
        author: WorkItemAuthor,
        stated_priority: str | None,
        at: datetime,
        chunk: Chunk,
    ) -> HubWorkItem:
        """Insert the item row keyed by ``pointer`` — the ref :meth:`allocate_ref`
        already minted for it, taken as its own explicit parameter — and ``chunk``'s own
        rows, atomically in one transaction: a store failure leaves
        neither durable."""
        ...

    def create_run_with_chunk(
        self,
        *,
        pointer: WorkRef,
        title: str,
        body: str,
        author: WorkItemAuthor,
        routine_name: str,
        scope_slug: str,
        run_mode: str,
        at: datetime,
        chunk: Chunk,
    ) -> HubWorkItem:
        """A routine run's own one-act mint: the item row with its run columns,
        ``chunk``'s own rows, and the run's identity row, atomically in one transaction —
        no window in which the item exists without its chunk, or the chunk without its
        run context. The chunk carries no promote fact and no queue position, so it rests
        ``not_ready`` until the ordinary promote."""
        ...

    def edit(
        self, source: str, ref: str, *, title: str, body: str, stated_priority: str | None, at: datetime
    ) -> HubWorkItem | None:
        """Replace an open item's title/body/stated priority in place and stamp
        ``edited_at``; ``created_at`` and ``ref`` are untouched. ``None`` when the item
        already carries a closure — the write matches zero rows, a closure race is not
        silently overwritten."""
        ...

    def close(self, source: str, ref: str, *, closure: WorkItemClosure, at: datetime) -> HubWorkItem:
        """Record ``closed_at``/``closure`` on an open item, once."""
        ...

    def delete_chunk_and_withdraw_hub_items_locked(
        self, handle: ILockedChunkRead, chunk: Chunk, *, by: str, at: datetime
    ) -> int:
        """Delete ``chunk`` — the ``chunk_deleted`` fact that makes it ephemeral — and
        withdraw every open ``hub:``-source item it holds, atomically on ``handle``'s
        already-locked connection (``bzh:store-exclusive-write``;
        :class:`~blizzard.hub.domain.operations.delete.DeleteService`). A ``forge:`` pointer on the
        same chunk is left untouched. Returns the freshly-written ``chunk_deleted.id``."""
        ...

    def accept_create(
        self,
        *,
        proposal_id: str,
        pointer: WorkRef,
        title: str,
        body: str,
        author: WorkItemAuthor,
        at: datetime,
        chunk: Chunk,
        reason: str | None,
        closed_by: str,
    ) -> HubWorkItem | None:
        """Mint the item and ``chunk``'s own rows, plus ``proposal_id``'s
        accepted-and-minted ``garden_proposal_closures`` row, atomically in one
        transaction — mirrors :meth:`create_with_chunk`, plus the closure row,
        written first as its idempotence guard. Returns ``None`` and writes nothing when
        ``proposal_id`` already carries a closure."""
        ...


#: A work ref's source-native token (``acme#42``), or ``None`` when no configured source renders it.
WorkRefLabel = Callable[[WorkRef], str | None]
