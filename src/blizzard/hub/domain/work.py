"""Work-lifecycle domain — the chunk, its facts, and its **derived** status.

The center of the model (D-024). Per ``bzh:facts-not-status`` a chunk's status is
never a stored column: it is computed here, by :func:`derive_chunk_status`, from
the recorded facts (D-004/D-067). This module owns the derivation queries the hub
runs — expressed as pure functions over already-loaded domain facts
(``bzh:domain-takes-objects``), so they unit-test with zero store and zero tokens.

The fact inputs (:class:`ChunkFacts`) are the domain-object form of the hub-store
rows in :mod:`blizzard.hub.store.schema`; a read repository hydrates them (the
:class:`IReadChunkRepository` seam), and the domain derives from the objects.

Precedence is **first match wins**, top to bottom, exactly as
``design/domain/events.md`` specifies — a chunk matching several rows has exactly
one status. ``waiting_on_human`` (open ask / open decision) sits between
``needs_human`` and ``delivering``: a chunk parks there on an open **question**
(``question.asked`` with no ``question.answered`` — ask/answer, MVP criterion 7) or
an open **decision** (a gate's ``decision.submitted`` no resolution flips off —
gates, MVP criterion 12). Both fact inputs live on :class:`ChunkFacts` and OR into
the one ``waiting_on_human`` branch (see :func:`derive_chunk_status`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.graph import Executor


class ChunkStatus(StrEnum):
    """The derived chunk statuses (D-067). Never stored — always a query result."""

    NOT_READY = "not_ready"
    READY = "ready"
    RUNNING = "running"
    DELIVERING = "delivering"
    WAITING_ON_HUMAN = "waiting_on_human"
    NEEDS_HUMAN = "needs_human"
    STOPPED = "stopped"
    DONE = "done"


# --- Domain objects ---------------------------------------------------------


@dataclass(frozen=True)
class PmPointer:
    """One wrapped PM item — ``{source, ref}`` (D-107), superseding ``{provider, url}``
    (D-075). ``source`` names a configured ``[[pm_source]]`` (D-108); ``ref`` is that
    source's own item token (a GitHub issue number). Contents never stored."""

    source: str
    ref: str


@dataclass(frozen=True)
class Chunk:
    """The unit of work that travels the workflow graph (D-024/D-047)."""

    chunk_id: str
    graph_id: str
    pm_pointers: list[PmPointer]
    minted_at: datetime


# --- Facts that feed the derivations ---------------------------------------
#
# Each is the domain-object form of a fact row. Only the fields the derivations
# read are carried; a hydrating repository fills them.


@dataclass(frozen=True)
class RouteCreatedFact:
    """A ``route.created`` fact — the claim (D-021/D-080)."""

    created_at: datetime


@dataclass(frozen=True)
class RouteReleasedFact:
    """A ``route.released`` fact — forcible detach (D-088)."""

    released_at: datetime


@dataclass(frozen=True)
class PrOpenedFact:
    """A ``pr.opened`` fact — the open-pr deliver mode's park record (D-059).

    One per repo whose branch the coordinator opened a PR for instead of merging. It
    carries no terminal weight: while a chunk has ``pr.opened`` facts and no ``pr.closed``,
    it derives ``delivering`` (awaiting an external merge) with environments held (D-066).
    ``number``/``url`` are the forge handle a later ``check_pr`` polls (D-065); ``repo`` is
    also the reconciliation skip-set that keeps a redelivery from opening a duplicate PR.
    """

    repo: str
    number: int
    url: str
    commit_hash: str
    opened_at: datetime


@dataclass(frozen=True)
class PrClosedFact:
    """A ``pr.closed`` fact — the terminal outcome of an open-pr delivery (D-065).

    Written when a poll or the on-demand check detects the PR reached a terminal state;
    ``merged`` distinguishes merged from closed-without-merge (both terminal), and
    ``landed_commit`` carries the merge commit where one exists. Its presence flips the
    chunk to ``done`` and the route is released.
    """

    repo: str
    number: int
    merged: bool
    landed_commit: str | None = None


@dataclass(frozen=True)
class LeaseFact:
    """A ``lease.minted`` fact reported up from a runner (D-044)."""

    epoch: int
    minted_at: datetime


@dataclass(frozen=True)
class TransitionFact:
    """A ``transition.recorded`` fact (D-027) with its target node's executor.

    ``to_node_executor`` is resolved by the hydrating repository (a join to the
    pinned graph's nodes) so the derivation stays a pure function — the domain
    never re-opens a store to learn whether the target is a hub node.

    ``from_node_id`` and ``choice_name`` describe the edge that was taken — the
    step's origin node and the judgement choice that routed it. The status
    derivations read only ``to_node_id``/``to_node_executor``/``epoch``/``recorded_at``;
    the edge fields feed the chunk's transition-history view (D-036) and default to
    ``None`` so a derivation unit test never has to supply them.
    """

    to_node_id: str
    to_node_executor: Executor
    epoch: int
    recorded_at: datetime
    from_node_id: str | None = None
    choice_name: str | None = None


@dataclass(frozen=True)
class EscalationFact:
    """An ``escalation.recorded`` fact — retries exhausted / dead worker (D-009).

    Carries the runner-composed **takeover command** (D-035/harness-adapters.md): the
    literal ``cd <workdir> && <harness resume>`` a human pastes to enter the parked
    session. It is surfaced on the chunk detail so ``needs_human`` is actionable; the
    status derivation itself keys only on ``(epoch, recorded_at)`` supersession (D-067).
    """

    epoch: int
    recorded_at: datetime
    takeover_command: str = ""


@dataclass(frozen=True)
class QuestionFact:
    """A ``question.asked`` row and whether it has been answered ([ask-answer.md]).

    Open/answered is **derived** (D-004): a question is open exactly while no
    ``question.answered`` row exists, and an open question is the fact the chunk's
    ``waiting_on_human`` status derives from. ``answered`` is resolved by the
    hydrating repository (the presence of the answer row) so the derivation stays a
    pure function; ``question_id`` and ``asked_at`` order the asks for a chunk carrying
    more than one.
    """

    question_id: str
    asked_at: datetime
    answered: bool = False


@dataclass(frozen=True)
class DecisionFact:
    """A gate's ``decision.submitted`` row and whether a transition references it (D-045).

    Kin to :class:`QuestionFact`: the chunk derives ``waiting_on_human`` from an
    **open** decision — one no transition resolves — exactly the D-037 pending pattern.
    ``resolved`` is computed by the hydrating repository (a transition carrying this
    ``decision_id``) so the derivation reads a plain boolean. This is the input shape
    the gates track writes decision facts against (``design/domain/work.md``); until it
    lands, :attr:`ChunkFacts.decisions` is empty and no chunk derives a gate park.
    """

    decision_id: str
    submitted_at: datetime
    resolved: bool = False


@dataclass(frozen=True)
class RequeueFact:
    """A ``requeue.recorded`` fact — closes an open escalation by supersession (D-067)."""

    requeued_at: datetime


@dataclass(frozen=True)
class DecisionChoice:
    """One selectable gate outcome — a button on the board/bot (D-042)."""

    name: str
    description: str


@dataclass(frozen=True)
class DecisionRow:
    """A gate decision in full — the surfacing/read model (D-045).

    Resolution and resolving-transition state are **derived**: ``resolved_choice`` is
    set once a resolution row exists, and ``transitioned`` is true once a transition
    references this decision (the runner has advanced the chunk, D-027). The holding
    runner acts on a decision that is resolved but not yet transitioned.
    """

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
    """Every fact a chunk's status derives from (D-067), already loaded.

    The derivation is a pure function of this aggregate — the unit tests build it
    directly, no store required.
    """

    minted: bool
    promoted: bool = False
    stopped: bool = False
    delivery_landed: bool = False
    pr_closed: bool = False
    escalations: list[EscalationFact] = field(default_factory=list)
    leases: list[LeaseFact] = field(default_factory=list)
    transitions: list[TransitionFact] = field(default_factory=list)
    routes_created: list[RouteCreatedFact] = field(default_factory=list)
    routes_released: list[RouteReleasedFact] = field(default_factory=list)
    questions: list[QuestionFact] = field(default_factory=list)
    decisions: list[DecisionFact] = field(default_factory=list)
    requeues: list[RequeueFact] = field(default_factory=list)
    pr_opened: list[PrOpenedFact] = field(default_factory=list)


# --- The derivation queries (D-067) -----------------------------------------


def derive_chunk_status(facts: ChunkFacts) -> ChunkStatus:
    """Derive a chunk's single status from its facts, first match wins (D-067)."""
    if facts.stopped:
        return ChunkStatus.STOPPED
    if facts.delivery_landed or facts.pr_closed:
        # ``pr.closed`` is the open-pr mode's terminal fact (merged or closed-without-merge,
        # both terminal — D-065), the counterpart to ``delivery.landed`` for merge-to-main.
        return ChunkStatus.DONE
    if _has_open_escalation(facts):
        return ChunkStatus.NEEDS_HUMAN
    if _is_waiting_on_human(facts):
        # An open question (ask-answer.md) or an open decision (gate, D-045); the
        # reap clock is stopped and the answer/resolution flips it back.
        return ChunkStatus.WAITING_ON_HUMAN
    if _newest_transition_enters_hub_node(facts):
        return ChunkStatus.DELIVERING
    if _has_live_route(facts):
        return ChunkStatus.RUNNING
    if not facts.promoted:
        # An un-promoted chunk rests ``not_ready`` — visible but never claimed (D-103).
        # Sits just above the ``ready`` fall-through and below every post-claim state, so a
        # promoted chunk that later runs/delivers/parks still derives from those facts; only
        # a fresh, un-promoted chunk with no live route lands here.
        return ChunkStatus.NOT_READY
    return ChunkStatus.READY


def _has_open_escalation(facts: ChunkFacts) -> bool:
    """An escalation with no later lease mint — supersession, not resolution (D-067)."""
    return open_escalation(facts) is not None


def open_escalation(facts: ChunkFacts) -> EscalationFact | None:
    """The newest escalation not yet closed by a later lease mint (D-067), or ``None``.

    Requeue/takeover close an escalation by **supersession** — a later lease mint or a
    later ``requeue.recorded`` fact, never a resolution fact (D-067) — so an escalation
    stays open exactly while nothing was recorded after it. When open, its
    ``takeover_command`` is the resume command a human pastes (harness-adapters.md); the
    board surfaces it on the ``needs_human`` chunk.
    """
    if not facts.escalations:
        return None
    newest = max(facts.escalations, key=lambda e: e.recorded_at)
    if any(lease.minted_at > newest.recorded_at for lease in facts.leases):
        return None
    if any(rq.requeued_at > newest.recorded_at for rq in facts.requeues):
        return None
    return newest


def _is_waiting_on_human(facts: ChunkFacts) -> bool:
    """An open question or an open decision parks the chunk (ask-answer.md/D-045)."""
    return bool(open_questions(facts)) or has_open_decision(facts)


def open_questions(facts: ChunkFacts) -> list[QuestionFact]:
    """The chunk's unanswered questions, oldest first ([ask-answer.md]).

    A question is open exactly while no ``question.answered`` row exists (D-004); an
    answer flips it out of ``waiting_on_human``. The list is what the chunk detail and
    ``blizzard hub status`` surface, and its non-emptiness is the derivation's input.
    """
    return sorted((q for q in facts.questions if not q.answered), key=lambda q: (q.asked_at, q.question_id))


def has_open_decision(facts: ChunkFacts) -> bool:
    """True iff a gate's decision is unresolved — no resolution flips it off (D-045)."""
    return open_decision(facts) is not None


def open_decision(facts: ChunkFacts) -> DecisionFact | None:
    """The newest gate decision no resolution has flipped off (D-045), or ``None``.

    A decision is open while it carries no resolution row — the person has not yet
    picked a choice. Once resolved, ``waiting_on_human`` drops away (the chunk's route
    is still live, so it derives ``running`` until the holding runner records the
    resolving transition, D-027). Pending-ness is thus derived, never stored.
    """
    unresolved = [d for d in facts.decisions if not d.resolved]
    if not unresolved:
        return None
    return max(unresolved, key=lambda d: d.submitted_at)


def awaiting_external_merge(facts: ChunkFacts) -> bool:
    """A ``delivering`` chunk parked on an open PR — ``pr.opened`` without ``pr.closed`` (D-065).

    Not a distinct status: the chunk derives ``delivering`` (its newest transition still
    enters the deliver hub node, environments held — D-066). This is the board **detail**
    that distinguishes an open-pr park from an in-flight merge (design/domain/events.md).
    """
    return bool(facts.pr_opened) and not facts.pr_closed


def open_pr_handles(facts: ChunkFacts) -> list[PrOpenedFact]:
    """The chunk's open PRs — the handles a poll or the on-demand check reads (D-065)."""
    return list(facts.pr_opened)


def _newest_transition_enters_hub_node(facts: ChunkFacts) -> bool:
    """The newest accepted transition's target is a hub-executed node (D-030)."""
    transition = newest_transition(facts)
    return transition is not None and transition.to_node_executor is Executor.HUB


def _has_live_route(facts: ChunkFacts) -> bool:
    """A ``route.created`` with no later ``route.released`` (D-088).

    Tie semantics (worth naming so the next reader doesn't have to rediscover it):
    on a same-instant ``created``/``released`` pair this uses strict ``>``, so a tie
    reads as still-live — a *reclaim* wins ties, because a fresh ``route.created``
    stamped at the exact instant of a prior release must still derive ``running``
    (see ``test_reclaimed_after_release_is_running_again``). This is the opposite
    tie-break from :meth:`ChunkStore.route_of`, which uses ``>=`` so a *release*
    wins ties (the gate a same-instant detach relies on). The two are not aligned
    to a single winner: doing so would silently break whichever direction lost, and
    a plain-timestamp fact model cannot tell "created after released" from "released
    after created" when the instants coincide — there is no sequence/epoch tiebreak
    for routes the way :func:`newest_transition` has one for transitions. Under
    :class:`~blizzard.foundation.clock.SystemClock` a same-instant tie is not
    reachable in practice, so this divergence is accepted rather than forced.
    """
    if not facts.routes_created:
        return False
    newest_created = max(facts.routes_created, key=lambda r: r.created_at)
    return not any(rel.released_at > newest_created.created_at for rel in facts.routes_released)


def newest_transition(facts: ChunkFacts) -> TransitionFact | None:
    """The chunk's newest accepted transition — its current node derives from this.

    Ordered by ``(recorded_at, epoch)``: the timestamp is the primary key, and the
    fencing epoch (monotonic per chunk) breaks a tie between two transitions stamped
    at the same instant — a coordinator that authors a follow-on transition under a
    higher epoch in the same tick, or any virtual-clock test.
    """
    if not facts.transitions:
        return None
    return max(facts.transitions, key=lambda t: (t.recorded_at, t.epoch))


def transition_history(facts: ChunkFacts) -> list[TransitionFact]:
    """The chunk's accepted transitions in the order they were recorded (oldest first).

    The chronological walk the board renders (D-036, MVP criterion 11): each entry is
    one node-step's edge — where it came from, the choice taken, where it went — so the
    review-fail loop back to ``build`` reads as a visible step in the timeline. Ordered
    by ``(recorded_at, epoch)``, the same key :func:`newest_transition` selects the tail
    of, so "the last entry is the current node" holds by construction.
    """
    return sorted(facts.transitions, key=lambda t: (t.recorded_at, t.epoch))


def current_node_id(facts: ChunkFacts) -> str | None:
    """The chunk's current node id — the newest transition's target, else ``None``.

    ``None`` means the chunk has not yet transitioned, so its current node is the
    pinned graph's entry node — a graph the caller already holds; resolving it is
    not this function's job (``bzh:domain-takes-objects``).
    """
    transition = newest_transition(facts)
    return transition.to_node_id if transition is not None else None


def latest_epoch(facts: ChunkFacts) -> int | None:
    """The chunk's latest fencing epoch — its newest lease's, else ``None`` (D-007)."""
    if not facts.leases:
        return None
    return max(lease.epoch for lease in facts.leases)


# --- Question rows (the ask/answer rendezvous — questions.md) ----------------


@dataclass(frozen=True)
class QuestionRow:
    """A durable question row with its derived answer state ([ask-answer.md]).

    The full surfacing shape behind ``blizzard hub status`` and the chunk detail's
    open-questions list. Open/answered is **derived** (D-004): a question is answered
    exactly while its answer row exists, and ``answered_by``/``answer``/``answered_at``
    are the winning first-write-wins CAS row (``None`` while open).
    """

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


@dataclass(frozen=True)
class AnswerOutcome:
    """The result of an answer write — first-write-wins CAS ([ask-answer.md]).

    ``won`` is True for the write that landed the row; a later writer gets ``won=False``
    with the **winning** row's ``answer``/``answered_by`` so the loser is told who
    already answered (the 409 body).
    """

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
        """One question row with its derived answer state, or None ([ask-answer.md])."""
        ...

    def list_open_questions(self) -> list[QuestionRow]:
        """Every unanswered question across the fleet — the ``hub status`` surface."""
        ...

    def load_questions(self, chunk_id: str) -> list[QuestionRow]:
        """A chunk's questions, open and answered — the chunk-detail surface (D-004)."""
        ...

    def load_artifacts(self, chunk_id: str) -> list[ArtifactRow]:
        """Every artifact row of a chunk; the caller resolves latest-by-epoch (D-089)."""
        ...

    def route_of(self, chunk_id: str) -> Route | None:
        """The chunk's live route (runner/workspace/envs), or None if unclaimed/released."""
        ...

    def list_ready(self) -> list[Chunk]: ...
    def list_all(self) -> list[Chunk]: ...
    def queue_positions(self) -> dict[str, float]:
        """The newest explicit ready-queue position per chunk — the order peek honours (D-048)."""
        ...

    def find_live_holder(self, pointer: PmPointer) -> str | None:
        """The chunk_id of a live (non-terminal) chunk holding ``pointer`` (D-093), or None."""
        ...

    def accepted_transition_target(self, chunk_id: str, *, from_node_id: str, epoch: int) -> str | None:
        """The ``to_node_id`` of an already-accepted transition out of ``from_node_id`` at
        ``epoch`` — the idempotency probe for a re-applied completion (D-090), or None."""
        ...

    def landed_repos(self, chunk_id: str) -> set[str]:
        """The repos already landed for a chunk — the delivery reconciliation skip-set (D-091)."""
        ...

    def open_prs(self, chunk_id: str) -> list[PrOpenedFact]:
        """The chunk's ``pr.opened`` facts — the open-pr reconcile skip-set and check handles (D-059/D-065)."""
        ...

    def runner_high_water(self, runner_id: str) -> int:
        """The greatest per-runner seq the hub has already applied, or 0 (D-069)."""
        ...

    def get_decision(self, decision_id: str) -> DecisionRow | None:
        """One gate decision in full, with derived resolution/transition state (D-045)."""
        ...

    def find_decision(self, chunk_id: str, *, node_id: str, epoch: int) -> DecisionRow | None:
        """The decision already open for a (chunk, node, epoch) — the idempotency probe
        for a re-submitted runner-config gate decision (a lost-ack replay, D-045)."""
        ...

    def decision_for_chunk(self, chunk_id: str) -> DecisionRow | None:
        """The chunk's newest not-yet-transitioned decision — what the board/runner act on."""
        ...

    def list_open_decisions(self) -> list[DecisionRow]:
        """Every unresolved decision across the fleet — the ``blizzard hub decisions`` view."""
        ...


class IWriteChunkRepository(IReadChunkRepository, Protocol):
    """Read-write chunk access. Only the domain layer depends on this variant."""

    def mint(self, chunk: Chunk) -> None: ...
    def record_promote(self, chunk_id: str, *, at: datetime) -> None:
        """Record a ``chunk.promoted`` fact — flips ``not_ready`` to ``ready`` (D-103).

        Idempotent: a chunk already promoted keeps its first row, so a re-promote (a
        double board click, a CLI retry) writes nothing and the status is unchanged."""
        ...

    def record_lease(self, chunk_id: str, *, epoch: int, runner_id: str, at: datetime) -> None: ...
    def set_runner_high_water(self, runner_id: str, *, seq: int, at: datetime) -> None:
        """Advance a runner's applied-seq high-water mark (upsert, D-069)."""
        ...

    def record_route(self, route: Route, *, at: datetime) -> None: ...
    def record_route_released(self, chunk_id: str, *, at: datetime) -> None: ...
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
        """One node-step's transition and its artifacts, written atomically (D-036).

        ``decision_id`` is set only on a gate-resolving transition — the Decision this
        transition resolves (D-045); ordinary transitions leave it ``None``."""
        ...

    def record_delivery_repo_landed(self, chunk_id: str, *, repo: str, commit_hash: str, at: datetime) -> None: ...
    def record_delivery_landed(self, chunk_id: str, *, at: datetime) -> None: ...
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
        no-op if already landed (D-030/crash recovery). Returns True iff it wrote."""
        ...

    def record_pr_opened(
        self, chunk_id: str, *, repo: str, number: int, url: str, commit_hash: str, at: datetime
    ) -> None:
        """Record a ``pr.opened`` park fact (open-pr mode, D-059) — no terminal, envs held (D-066)."""
        ...

    def finalize_pr_delivery(
        self,
        chunk_id: str,
        *,
        closed: list[PrClosedFact],
        from_node_id: str,
        to_node_id: str,
        choice_name: str,
        epoch: int,
        runner_id: str,
        transition_id: str,
        at: datetime,
    ) -> bool:
        """Terminate an open-pr delivery atomically and idempotently (D-065).

        Called once every open PR has reached a terminal state: writes the per-repo
        ``pr.closed`` facts, the hub lease, the terminal transition, and the route release
        in one transaction — the open-pr counterpart to :meth:`finalize_delivery`. Guarded
        by the ``pr.closed`` existence check so a re-checked/replayed finalize is a no-op.
        Returns True iff it wrote."""
        ...

    def record_escalation(self, chunk_id: str, *, epoch: int, takeover_command: str, at: datetime) -> None:
        """Record an ``escalation.recorded`` fact reported up by a runner (D-009/D-067).

        Retries exhausted (or a dead worker past the cap): the chunk derives
        ``needs_human`` until a later lease mint supersedes it. The runner-composed
        takeover command rides along so the parked session is resumable (D-035)."""
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
        """Land a ``question.asked`` row — the chunk derives ``waiting_on_human`` ([ask-answer.md]).

        Runner-authored, forwarded up the outbound buffer; the row is the durable
        rendezvous the answer keys off. Idempotent by ``question_id`` (a store-and-forward
        replay re-lands the same id harmlessly)."""
        ...

    def answer_question(self, question_id: str, *, answer: str, answered_by: str, at: datetime) -> AnswerOutcome:
        """First-write-wins CAS on the answer row ([ask-answer.md]).

        Exactly one answer row ever exists: the first write wins (``won=True``); a
        racing second write loses (``won=False``) and is handed the winning row. This
        row alone flips the chunk out of ``waiting_on_human`` (D-004)."""
        ...

    def record_answer_delivered(self, *, question_id: str, chunk_id: str, at: datetime) -> None:
        """Record an ``answer.delivered`` fact — the resume-with-answer ran ([ask-answer.md]).

        Board detail (the session was reconstituted around the answer); the status
        already flipped at ``question.answered``, so no status derives from this."""
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
        """Open a gate decision, committing any step artifacts atomically (D-045/D-036).

        A graph gate passes no artifacts (they landed with the arriving transition); a
        runner-config gate carries the gated step's artifacts here, exactly where the
        step's transition would have written them."""
        ...

    def record_decision_resolution(self, decision_id: str, *, choice: str, resolved_by: str, at: datetime) -> bool:
        """First-write-wins CAS: record the person's choice, or return ``False`` if
        the decision was already resolved (the loser is told who won, D-045)."""
        ...

    def record_requeue(self, chunk_id: str, *, at: datetime) -> None:
        """Record a ``requeue.recorded`` fact — supersedes an open escalation (D-067)."""
        ...

    def record_queue_position(self, chunk_id: str, *, position: float, at: datetime) -> None:
        """Append a ready chunk's new queue position; order derives (D-048/D-004)."""
        ...

    def add_pm_pointers(self, chunk_id: str, pointers: list[PmPointer], *, at: datetime) -> None:
        """Fold PM pointers into a group survivor, de-duped by (source, ref) (D-076/D-107)."""
        ...

    def record_grouped(self, chunk_id: str, *, grouped_into: str, at: datetime) -> None:
        """Record ``chunk.grouped`` — the merged-away chunk becomes ephemeral (D-048/D-047)."""
        ...
