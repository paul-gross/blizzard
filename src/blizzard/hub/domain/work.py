"""Work-lifecycle domain — the chunk, its facts, and its **derived** status.

The center of the model. Per ``bzh:facts-not-status`` a chunk's status is
never a stored column: it is computed here, by :func:`derive_chunk_status`, from
the recorded facts. This module owns the derivation queries the hub
runs — expressed as pure functions over already-loaded domain facts
(``bzh:domain-takes-objects``), so they unit-test with zero store and zero tokens.

The fact inputs (:class:`ChunkFacts`) are the domain-object form of the hub-store
rows in :mod:`blizzard.hub.store.schema`; a read repository hydrates them (the
:class:`IReadChunkRepository` seam), and the domain derives from the objects.

Precedence is **first match wins**, top to bottom — a chunk matching several rows has
exactly one status. ``waiting_on_human`` (open ask / open decision) sits between
``needs_human`` and ``delivering``: a chunk parks there on an open **question**
(``question.asked`` with no ``question.answered`` — ask/answer, MVP criterion 7) or
an open **decision** (a gate's ``decision.submitted`` no resolution flips off —
gates, MVP criterion 12). Both fact inputs live on :class:`ChunkFacts` and OR into
the one ``waiting_on_human`` branch (see :func:`derive_chunk_status`).
"""

from __future__ import annotations

from collections.abc import Sequence
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


# --- Domain objects ---------------------------------------------------------


@dataclass(frozen=True)
class PmPointer:
    """One wrapped PM item — ``{source, ref}``, superseding ``{provider, url}``.
    ``source`` names a configured ``[[pm_source]]``; ``ref`` is that
    source's own item token (a GitHub issue number). Contents never stored."""

    source: str
    ref: str


# The model a chunk runs on absent an edit (issue #27). Defined independently of the
# runner's own ``DEFAULT_WORKER_MODEL`` (``blizzard.runner.harness.internal.
# claude_code_adapter``) — the hub domain must not depend on the runner
# (``bzh:domain-core``, separate daemons) — but carries the same value so a freshly
# minted chunk's selection matches what the fleet actually ran before this field existed.
DEFAULT_MODEL = "claude-opus-4-8"


@dataclass(frozen=True)
class Chunk:
    """The unit of work that travels the workflow graph."""

    chunk_id: str
    graph_id: str
    pm_pointers: list[PmPointer]
    minted_at: datetime
    # The chunk's model selection — editable while ``not_ready`` (issue #27,
    # ``domain/edit.py``). Defaulted so the many fakes/tests that build a ``Chunk``
    # without opinion on it keep compiling.
    model: str = DEFAULT_MODEL


# --- Facts that feed the derivations ---------------------------------------
#
# Each is the domain-object form of a fact row. Only the fields the derivations
# read are carried; a hydrating repository fills them.


@dataclass(frozen=True)
class RouteCreatedFact:
    """A ``route.created`` fact — the claim.

    ``seq`` is the monotonic route-event tiebreak (see :func:`_has_live_route`): a
    per-chunk counter shared with :class:`RouteReleasedFact`, assigned in real write
    order. Defaults to ``0`` for callers (mostly tests) that don't construct a tie.
    """

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

    Minted alongside a claim's :class:`RouteCreatedFact` and appended, never rewritten
    (``bzh:facts-not-status`` — a re-key appends a fresh fact rather than mutating this
    one). ``token_hash`` is the sha256 hex digest only; the plaintext is returned once
    in the claim response and never stored. ``seq`` shares the same per-chunk counter
    :class:`RouteCreatedFact`/:class:`RouteReleasedFact` do (see
    :func:`newest_live_route_token`), so it totally orders against a create/release
    even on a timestamp tie.
    """

    token_hash: str
    minted_at: datetime
    seq: int = 0


@dataclass(frozen=True)
class PrOpenedFact:
    """A ``pr.opened`` fact — the open-pr deliver mode's park record.

    One per repo whose branch the coordinator opened a PR for instead of merging. It
    carries no terminal weight: while a chunk has ``pr.opened`` facts and no ``pr.closed``,
    it derives ``delivering`` (awaiting an external merge) with environments held.
    ``number``/``url`` are the forge handle a later ``check_pr`` polls; ``repo`` is
    also the reconciliation skip-set that keeps a redelivery from opening a duplicate PR.
    """

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
    """A ``transition.recorded`` fact with its target node's executor.

    ``to_node_executor`` is resolved by the hydrating repository (a join to the
    pinned graph's nodes) so the derivation stays a pure function — the domain
    never re-opens a store to learn whether the target is a hub node.

    ``from_node_id`` and ``choice_name`` describe the edge that was taken — the
    step's origin node and the judgement choice that routed it. The status
    derivations read only ``to_node_id``/``to_node_executor``/``epoch``/``recorded_at``;
    the edge fields feed the chunk's transition-history view and default to
    ``None`` so a derivation unit test never has to supply them.

    ``graph_id`` is the graph the transition happened in (issue #90 — graph-provenance).
    The hydrating repository resolves ``to_node_executor`` and the history view resolves
    the node names against *this* graph rather than the chunk's current pin, so a chunk
    that later migrates to another graph still reads its old-graph steps correctly.
    Defaults to ``None`` so a derivation unit test that supplies its own
    ``to_node_executor`` need not name a graph.
    """

    to_node_id: str
    to_node_executor: Executor
    epoch: int
    recorded_at: datetime
    from_node_id: str | None = None
    choice_name: str | None = None
    graph_id: str | None = None


@dataclass(frozen=True)
class EscalationFact:
    """An ``escalation.recorded`` fact — retries exhausted / dead worker.

    Carries the runner-composed **takeover command**: the
    literal ``cd <workdir> && <harness resume>`` a human pastes to enter the parked
    session. It is surfaced on the chunk detail so ``needs_human`` is actionable; the
    status derivation itself keys only on ``(epoch, recorded_at)`` supersession.
    """

    epoch: int
    recorded_at: datetime
    takeover_command: str = ""


@dataclass(frozen=True)
class QuestionFact:
    """A ``question.asked`` row and whether it has been answered.

    Open/answered is **derived**: a question is open exactly while no
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
    """A gate's ``decision.submitted`` row and whether a transition references it.

    Kin to :class:`QuestionFact`: the chunk derives ``waiting_on_human`` from an
    **open** decision — one no transition resolves — the same open/unresolved pending
    pattern an open question follows. ``resolved`` is computed by the hydrating
    repository (a transition carrying this ``decision_id``) so the derivation reads a
    plain boolean. This is the input shape the gates track writes decision facts
    against; until it lands, :attr:`ChunkFacts.decisions` is empty and no chunk derives
    a gate park.
    """

    decision_id: str
    submitted_at: datetime
    resolved: bool = False


@dataclass(frozen=True)
class BounceFact:
    """A ``chunk_bounces`` row — one delivery kick-back (#64).

    Contention, not failure: a bounce consumes no node retry and is not itself an
    escalation — it is counted separately, and only crossing the node's ``bounce_cap``
    escalates. ``epoch`` is the coordinator's own ``hub_epoch`` at bounce time — the
    natural key (``chunk_id``, ``epoch``) a hydrating repository's write guards against a
    redelivery replay double-counting. ``cause`` names the kick-back reason (``conflict``
    in the MVP forge seam — the fuller checks/master-moved vocabulary is a later phase's
    forge-seam extension); ``envelope`` is the opaque JSON kick-back payload (cause detail,
    the repo, the base branch) the routed edge's artifact carries into the fix node so it
    never rediscovers what bounced it.
    """

    epoch: int
    cause: str
    envelope: str
    recorded_at: datetime


@dataclass(frozen=True)
class HubNodePollFact:
    """A ``hub_node_poll`` row — one pending-poll attempt at a hub command node (#66).

    Append-only, stamped from the injected clock. ``epoch`` is the arrival epoch of
    the current visit to ``node_id`` — the same value the node's own marker/log
    artifacts are recorded under (:class:`~blizzard.hub.delivery.hub_node.HubNodeExecutor`),
    not a fresh one minted per poll. Pending-ness (:func:`hub_node_pending`) derives
    from these rows plus the newest transition — nothing in-memory, so a ``kill -9``
    between polls resumes polling straight from the store.
    """

    node_id: str
    epoch: int
    polled_at: datetime


@dataclass(frozen=True)
class MigrationFact:
    """A ``chunk_migrations`` fact — a cross-graph migration re-pinned the chunk (issue #90).

    Its own recorded fact, **never a transition** (``bzh:migration-not-transition``): a
    judgement choice targeting another graph ends the current attempt, re-pins the chunk
    to ``to_graph_id``, releases the route, and re-queues the chunk at ``landed_node_id``
    (name-match-else-entry against the target graph, resolved concretely at write time —
    ``None`` only as a schema allowance, read as the target's entry). Recorded at the
    submitting ``epoch`` (the fence the next claim mints above); ``model`` is the
    re-pinned model, or ``None`` when the migration kept the chunk's current model.
    ``from_node_id``/``from_graph_id``/``choice_name`` describe the edge for the history
    view. The status derivations key on ``(recorded_at, epoch)`` supersession of the
    newest transition and read ``landed_node_id``.
    """

    from_node_id: str | None
    from_graph_id: str
    to_graph_id: str
    landed_node_id: str | None
    choice_name: str | None
    model: str | None
    epoch: int
    recorded_at: datetime


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

    Unlike :class:`LeaseFact`/:class:`EscalationFact`, usage is deliberately **not**
    epoch-fenced: a row whose epoch trails the chunk's latest is real spend incurred by a
    fenced-out zombie attempt, and must be attributed and summed exactly like every other
    row — never dropped (the completion path's stale-epoch rejection, ``apply.py``, is the
    contrast). ``node_id``/``epoch``/``kind``/``model`` identify the invocation for the
    per-node-step history view; the chunk-level total (:func:`derive_chunk_usage`) sums
    every row's tokens/cost regardless of epoch.
    """

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
class DecisionChoice:
    """One selectable gate outcome — a button on the board/bot."""

    name: str
    description: str


@dataclass(frozen=True)
class DecisionRow:
    """A gate decision in full — the surfacing/read model.

    Resolution and resolving-transition state are **derived**: ``resolved_choice`` is
    set once a resolution row exists, and ``transitioned`` is true once a transition
    references this decision (the runner has advanced the chunk). The holding
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
    """Every fact a chunk's status derives from, already loaded.

    The derivation is a pure function of this aggregate — the unit tests build it
    directly, no store required.
    """

    minted: bool
    promoted: bool = False
    stopped: bool = False
    # ``delivery.landed`` — the whole-chunk terminal fact ``finalize_delivery`` writes
    # atomically with the terminal transition (merge-to-main). Informational only
    # (``bzh:facts-not-status``): it no longer drives DONE (:func:`newest_transition_is_terminal`
    # does) — it feeds :func:`has_landed_repos` alongside ``landed_repos`` below, so a
    # merged-but-not-yet-terminal chunk (an authored ``merged -> <node>`` edge) still
    # reads "landed" honestly in chunk detail even though this whole-chunk fact never fires
    # for it (only the per-repo facts do).
    delivery_landed: bool = False
    # The chunk's per-repo ``delivery.repo_landed`` facts — recorded as each repo lands,
    # independent of whether the chunk's delivery has reached a terminal transition yet.
    # Feeds :func:`has_landed_repos`; the derivation reads only non-emptiness.
    landed_repos: frozenset[str] = field(default_factory=frozenset)
    pr_closed: bool = False
    escalations: list[EscalationFact] = field(default_factory=list)
    leases: list[LeaseFact] = field(default_factory=list)
    transitions: list[TransitionFact] = field(default_factory=list)
    routes_created: list[RouteCreatedFact] = field(default_factory=list)
    routes_released: list[RouteReleasedFact] = field(default_factory=list)
    route_tokens_minted: list[RouteTokenMintedFact] = field(default_factory=list)
    questions: list[QuestionFact] = field(default_factory=list)
    decisions: list[DecisionFact] = field(default_factory=list)
    requeues: list[RequeueFact] = field(default_factory=list)
    # The chunk's cross-graph migration facts (issue #90) — each re-pins the chunk to
    # another graph and re-queues it. Feeds :func:`newest_migration` /
    # :func:`current_node_id` and gates the terminal/hub-node checks, so a re-queued chunk
    # derives ``ready`` at its new landing node rather than ``done``/``delivering`` off its
    # superseded pre-migration transition.
    migrations: list[MigrationFact] = field(default_factory=list)
    pr_opened: list[PrOpenedFact] = field(default_factory=list)
    pauses: list[PauseFact] = field(default_factory=list)
    usage: list[UsageFact] = field(default_factory=list)
    # The chunk's recorded delivery kick-backs (#64) — feeds :func:`bounce_count` /
    # :func:`bounces_over_cap` and the chunk-detail bounce history. Never a status.
    bounces: list[BounceFact] = field(default_factory=list)
    # The chunk's recorded hub-node poll attempts (#66) — feeds :func:`hub_node_pending`
    # and the executor's own interval/timeout gating. Never a status: pending is a
    # facet of ``delivering`` (the newest transition still enters the hub node).
    hub_node_polls: list[HubNodePollFact] = field(default_factory=list)


# --- The derivation queries -----------------------------------------


def derive_chunk_status(facts: ChunkFacts) -> ChunkStatus:
    """Derive a chunk's single status from its facts, first match wins.

    ``done`` is the **only** terminal (#63): it derives from *reaching* the terminal
    transition (:func:`newest_transition_is_terminal`), not from the landed fact —
    an authored ``merged -> <node>`` edge can land every repo and keep the chunk
    running or escalated in a post-merge node, never un-merged, never wrongly DONE.
    """
    if facts.stopped:
        return ChunkStatus.STOPPED
    if newest_transition_is_terminal(facts) or facts.pr_closed:
        # ``pr.closed`` is the open-pr mode's terminal fact (merged or closed-without-merge,
        # both terminal); its finalize always lands the terminal transition too, but the
        # explicit check keeps this branch legible about the open-pr counterpart.
        return ChunkStatus.DONE
    if _has_open_escalation(facts):
        return ChunkStatus.NEEDS_HUMAN
    if _is_waiting_on_human(facts):
        # An open question or an open decision (gate); the
        # reap clock is stopped and the answer/resolution flips it back.
        return ChunkStatus.WAITING_ON_HUMAN
    if _is_paused(facts):
        # Below the human-gated states (a chunk both parked on a question and paused
        # is still, first, waiting on a human) and above delivering/running (issue #46).
        return ChunkStatus.PAUSED
    if _newest_transition_enters_hub_node(facts):
        return ChunkStatus.DELIVERING
    if _has_live_route(facts):
        return ChunkStatus.RUNNING
    if not facts.promoted:
        # An un-promoted chunk rests ``not_ready`` — visible but never claimed.
        # Sits just above the ``ready`` fall-through and below every post-claim state, so a
        # promoted chunk that later runs/delivers/parks still derives from those facts; only
        # a fresh, un-promoted chunk with no live route lands here.
        return ChunkStatus.NOT_READY
    return ChunkStatus.READY


def _has_open_escalation(facts: ChunkFacts) -> bool:
    """An escalation with no later lease mint — supersession, not resolution."""
    return open_escalation(facts) is not None


def open_escalation(facts: ChunkFacts) -> EscalationFact | None:
    """The newest escalation not yet closed by a later lease mint, or ``None``.

    Requeue/takeover close an escalation by **supersession** — a later lease mint or a
    later ``requeue.recorded`` fact, never a resolution fact — so an escalation
    stays open exactly while nothing was recorded after it. When open, its
    ``takeover_command`` is the resume command a human pastes; the
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
    """An open question or an open decision parks the chunk."""
    return bool(open_questions(facts)) or has_open_decision(facts)


def _is_paused(facts: ChunkFacts) -> bool:
    """Paused derives from the newest pause fact, newest-fact-wins (issue #46)."""
    return open_pause(facts) is not None


def open_pause(facts: ChunkFacts) -> PauseFact | None:
    """The newest pause fact iff it currently reads paused, else ``None`` (issue #46).

    The wire (``PauseView``) and the runner both need the **fact** — who paused it and
    when — not merely the derived boolean ``_is_paused`` gives the status query. PAUSED
    sits below the human-gated states, so a status-keyed reader would miss a chunk that
    is paused *and* parked on a question (``status == waiting_on_human``); this reads
    the fact directly, independent of where pause sits in the derivation order.
    """
    return facts.pauses[-1] if facts.pauses and facts.pauses[-1].paused else None


def open_questions(facts: ChunkFacts) -> list[QuestionFact]:
    """The chunk's unanswered questions, oldest first.

    A question is open exactly while no ``question.answered`` row exists; an
    answer flips it out of ``waiting_on_human``. The list is what the chunk detail and
    ``blizzard hub status`` surface, and its non-emptiness is the derivation's input.
    """
    return sorted((q for q in facts.questions if not q.answered), key=lambda q: (q.asked_at, q.question_id))


def has_open_decision(facts: ChunkFacts) -> bool:
    """True iff a gate's decision is unresolved — no resolution flips it off."""
    return open_decision(facts) is not None


def open_decision(facts: ChunkFacts) -> DecisionFact | None:
    """The newest gate decision no resolution has flipped off, or ``None``.

    A decision is open while it carries no resolution row — the person has not yet
    picked a choice. Once resolved, ``waiting_on_human`` drops away (the chunk's route
    is still live, so it derives ``running`` until the holding runner records the
    resolving transition). Pending-ness is thus derived, never stored.
    """
    unresolved = [d for d in facts.decisions if not d.resolved]
    if not unresolved:
        return None
    return max(unresolved, key=lambda d: d.submitted_at)


def awaiting_external_merge(facts: ChunkFacts) -> bool:
    """A ``delivering`` chunk parked on an open PR — ``pr.opened`` without ``pr.closed``.

    Not a distinct status: the chunk derives ``delivering`` (its newest transition still
    enters the deliver hub node, environments held). This is the board **detail**
    that distinguishes an open-pr park from an in-flight merge.
    """
    return bool(facts.pr_opened) and not facts.pr_closed


_MARKER_PREFIX = "merged/"


def landed_repos_from_markers(artifacts: Sequence[ArtifactRow]) -> frozenset[str]:
    """Repos landed via a generic hub command node's ``merged/<repo>`` marker
    artifact (#67) — the convention the authored default (and PR+CI example) delivery
    graphs record per pushed repo, via the mid-run marker callback
    (:meth:`~blizzard.hub.delivery.hub_node.HubNodeExecutor.record_marker`). This is
    the landing truth going forward: no engine code names a "deliver" node, so
    nothing writes the old per-repo ``delivery.repo_landed`` fact any more — a chunk's
    landed detail is read off its own node artifacts, not a privileged fact family.
    """
    return frozenset(
        artifact.name.removeprefix(_MARKER_PREFIX) for artifact in artifacts if artifact.name.startswith(_MARKER_PREFIX)
    )


def has_landed_repos(facts: ChunkFacts, artifacts: Sequence[ArtifactRow] = ()) -> bool:
    """True iff any repo has landed for this chunk — informational, never a status (#63).

    Feeds chunk-detail truthfully whether landing is fully finalized (the whole-chunk
    ``delivery_landed`` fact) or only some/all repos have landed while the chunk sits
    in a post-merge node, running or escalated — "merged but stuck" honestly, never
    un-merged. ``artifacts`` is the chunk's node artifacts, read for the generic
    ``merged/<repo>`` marker convention (#67) — the CURRENT landing truth; the fact
    inputs (``delivery_landed``/``landed_repos``) are read alongside for BACK-COMPAT
    with chunks a pre-#67 hub delivered (the ``delivery_*`` fact tables are kept, not
    migrated), so a historical chunk still reads landed even though nothing writes
    those facts any more.
    """
    return facts.delivery_landed or bool(facts.landed_repos) or bool(landed_repos_from_markers(artifacts))


def bounce_count(facts: ChunkFacts) -> int:
    """The chunk's total recorded delivery kick-backs (#64) — informational.

    Feeds the cap check (:func:`bounces_over_cap`) and the chunk-detail bounce
    history; never itself a status — a bounce is contention, not failure."""
    return len(facts.bounces)


def bounces_over_cap(facts: ChunkFacts, cap: int) -> bool:
    """True once the chunk's bounce count has **crossed** ``cap`` (#64).

    Crossed, not reached: a node whose ``bounce_cap`` is 5 tolerates 5 kick-backs
    before this flips True on the 6th — the cap counts bounces a chunk survives before
    escalating, not a zero-indexed budget."""
    return bounce_count(facts) > cap


def newest_migration(facts: ChunkFacts) -> MigrationFact | None:
    """The chunk's newest cross-graph migration fact, or ``None`` (issue #90).

    Ordered by ``(recorded_at, epoch)`` — the same key :func:`newest_transition` uses —
    so "the latest movement" is comparable across the two fact kinds.
    """
    if not facts.migrations:
        return None
    return max(facts.migrations, key=lambda m: (m.recorded_at, m.epoch))


def _latest_movement_is_migration(facts: ChunkFacts) -> bool:
    """The chunk's newest movement fact is a migration, not a transition (issue #90).

    A migration re-queues the chunk, so once it is the latest movement the chunk's
    current node is the migration's landing node and the pre-migration transition's
    terminal/hub identity is superseded (a re-queued chunk is ``ready``, never ``done``
    off its old-graph terminal). Ties go to the migration — it is recorded *after* the
    transition that brought the chunk to the node it migrates out of.
    """
    migration = newest_migration(facts)
    if migration is None:
        return False
    transition = newest_transition(facts)
    if transition is None:
        return True
    return (migration.recorded_at, migration.epoch) >= (transition.recorded_at, transition.epoch)


def newest_transition_is_terminal(facts: ChunkFacts) -> bool:
    """The newest accepted transition's target is the reserved terminal (``done``, #63).

    The **sole** DONE trigger — reaching the terminal, not any landed/closed fact. A
    chunk whose repos all landed but whose newest transition entered a post-merge node
    (an authored ``merged -> <node>`` edge) does not derive DONE here; it derives
    ``running``/``needs_human`` from the branches below, with :func:`has_landed_repos`
    surfacing the landed detail truthfully alongside. A **later migration** supersedes the
    transition entirely (issue #90): a re-queued chunk is never DONE off a pre-migration
    terminal.
    """
    if _latest_movement_is_migration(facts):
        return False
    transition = newest_transition(facts)
    return transition is not None and transition.to_node_id == RESERVED_TERMINAL


def _newest_transition_enters_hub_node(facts: ChunkFacts) -> bool:
    """The newest accepted transition's target is a hub-executed node.

    A later migration supersedes it (issue #90) — a re-queued chunk derives ``ready``, not
    ``delivering`` off a pre-migration hub-node transition.
    """
    if _latest_movement_is_migration(facts):
        return False
    transition = newest_transition(facts)
    return transition is not None and transition.to_node_executor is Executor.HUB


def landing_node(target_graph: Graph, from_node_name: str | None) -> str:
    """The node a migration lands on in ``target_graph`` — name-match-else-entry (issue #90).

    ``bzh:migration-not-transition``'s landing rule: land on the node of the target graph
    whose name matches the node the chunk migrated *from*, or the target's entry node when
    no name matches (the triage case — the target has no same-named node). A pure function
    of the passed-in graph (``bzh:domain-takes-objects``); the caller resolves the target
    graph and this picks the concrete landing node id to record.
    """
    if from_node_name is not None:
        node = target_graph.node_by_name(from_node_name)
        if node is not None:
            return node.node_id
    return target_graph.entry_node_id


def hub_node_poll_history(facts: ChunkFacts, *, node_id: str, epoch: int) -> list[HubNodePollFact]:
    """A hub node's poll attempts for one (node, epoch) visit, oldest first (#66).

    The executor's own gating input: the earliest entry bounds ``poll_timeout``, the
    newest gates ``poll_interval`` — both read off this history rather than any
    in-memory state, so a ``kill -9`` between polls resumes exactly here.
    """
    return sorted(
        (p for p in facts.hub_node_polls if p.node_id == node_id and p.epoch == epoch), key=lambda p: p.polled_at
    )


def hub_node_pending(facts: ChunkFacts) -> HubNodePollFact | None:
    """The chunk's in-progress hub-node poll, or ``None`` — chunk-detail honesty (#66).

    Not a distinct status: the chunk still derives ``delivering`` (its newest
    transition still enters the hub node — :func:`_newest_transition_enters_hub_node`).
    This is the DETAIL that distinguishes a hub node about to run its first attempt
    from one already parked mid-poll, mirroring :func:`awaiting_external_merge`. A
    poll fact recorded for the newest transition's ``(to_node_id, epoch)`` with no
    later transition means the node is still waiting on external state; the caller
    resolves the node's ``poll_interval`` to compute the next-poll time.
    """
    transition = newest_transition(facts)
    if transition is None or transition.to_node_executor is not Executor.HUB:
        return None
    history = hub_node_poll_history(facts, node_id=transition.to_node_id, epoch=transition.epoch)
    return history[-1] if history else None


def newest_live_route(
    routes_created: list[RouteCreatedFact], routes_released: list[RouteReleasedFact]
) -> RouteCreatedFact | None:
    """The newest ``route.created`` fact still live, or ``None`` if released.

    The single tie-break both :func:`_has_live_route` and :meth:`ChunkStore.route_of`
    resolve against, so route liveness has exactly one answer at a same-instant tie
    (issue #41 — the two used to disagree, one via ``>`` and the other via ``>=`` on
    ``created_at``/``released_at`` alone). Ordered by ``(timestamp, seq)``, the same
    shape :func:`newest_transition` uses for ``(recorded_at, epoch)``: ``seq`` is a
    per-chunk counter shared across both fact kinds, assigned in real write order, so
    it breaks a timestamp tie the way the timestamp itself cannot.

    This resolves both directions correctly rather than picking a fixed winner:

    - A same-instant **detach** records its ``route.released`` *after* the route's
      ``route.created`` in real write order, so its ``seq`` is higher — the release
      outranks the create and the route reads not-live (the gate a same-instant
      detach's own 409 check relies on).
    - A same-instant **re-claim** records a fresh ``route.created`` *after* the prior
      release in real write order, so its ``seq`` is higher than that release's — the
      new create outranks the old release and the route reads live (see
      ``test_reclaimed_after_release_is_running_again``, which this must not break).

    Under :class:`~blizzard.foundation.clock.SystemClock` a same-instant tie is not
    reachable in practice (microsecond resolution); ``seq`` only matters under a
    :class:`~blizzard.foundation.clock.FixedClock`, which is where the tie was found.
    """
    if not routes_created:
        return None
    newest_created = max(routes_created, key=lambda r: (r.created_at, r.seq))
    key = (newest_created.created_at, newest_created.seq)
    if any((rel.released_at, rel.seq) > key for rel in routes_released):
        return None
    return newest_created


def _has_live_route(facts: ChunkFacts) -> bool:
    """A ``route.created`` with no later ``route.released``. See :func:`newest_live_route`."""
    return newest_live_route(facts.routes_created, facts.routes_released) is not None


def newest_live_route_token(
    routes_created: list[RouteCreatedFact],
    routes_released: list[RouteReleasedFact],
    route_tokens_minted: list[RouteTokenMintedFact],
) -> RouteTokenMintedFact | None:
    """The chunk's live route capability token, or ``None`` if unclaimed/released (issue #84a).

    The live token is the newest :class:`RouteTokenMintedFact` minted for the
    currently-live acquisition — i.e. at or after :func:`newest_live_route`'s own
    ``seq``. Restricting to ``seq >= live.seq`` is sufficient to scope the search to
    the live acquisition's window with no separate upper bound against
    ``route_released``: a token minted *before* the live route's own ``created_at``
    fact belonged to an earlier (already-released) acquisition and is excluded by the
    lower bound alone, and there is no later release to bound against or
    :func:`newest_live_route` would already have returned ``None``.

    Newest-fact-wins (ordered the same way :func:`newest_live_route` orders its own
    candidates — ``(timestamp, seq)``) is what makes a re-key (Phase 6: a fresh token
    fact appended for the same live route, same ``seq`` floor) supersede the prior
    token immediately, with no separate revocation step.
    """
    live = newest_live_route(routes_created, routes_released)
    if live is None:
        return None
    candidates = [t for t in route_tokens_minted if t.seq >= live.seq]
    if not candidates:
        return None
    return max(candidates, key=lambda t: (t.minted_at, t.seq))


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

    The chronological walk the board renders (MVP criterion 11): each entry is
    one node-step's edge — where it came from, the choice taken, where it went — so the
    review-fail loop back to ``build`` reads as a visible step in the timeline. Ordered
    by ``(recorded_at, epoch)``, the same key :func:`newest_transition` selects the tail
    of, so "the last entry is the current node" holds by construction.
    """
    return sorted(facts.transitions, key=lambda t: (t.recorded_at, t.epoch))


def current_node_id(facts: ChunkFacts) -> str | None:
    """The chunk's current node id — the newest movement fact's target, else ``None``.

    Normally the newest transition's ``to_node_id``. When a **migration** is the latest
    movement (issue #90), it is the migration's ``landed_node_id`` in the re-pinned graph
    instead — so a re-queued chunk's current node is where it landed under the new graph,
    not the old-graph node it migrated out of. Centralizing this here fixes every
    ``current_node_id(...) or graph.entry_node_id`` call site at once.

    ``None`` means the chunk has not yet moved, so its current node is the pinned graph's
    entry node — a graph the caller already holds; resolving it is not this function's job
    (``bzh:domain-takes-objects``). ``landed_node_id`` may itself be ``None`` (the schema
    allowance for "the target's entry") and falls through the same ``or entry_node_id``.
    """
    if _latest_movement_is_migration(facts):
        migration = newest_migration(facts)
        assert migration is not None  # _latest_movement_is_migration guarantees it
        return migration.landed_node_id
    transition = newest_transition(facts)
    return transition.to_node_id if transition is not None else None


def latest_epoch(facts: ChunkFacts) -> int | None:
    """The chunk's latest fencing epoch — its newest lease's, else ``None``."""
    if not facts.leases:
        return None
    return max(lease.epoch for lease in facts.leases)


@dataclass(frozen=True)
class UsageTotal:
    """A usage/cost total summed over a set of usage facts at read time — never a
    stored column (``bzh:facts-not-status``, the same precedent :func:`derive_chunk_status`
    sets). Neutrally named (not ``ChunkUsage``) because it is shared by
    :func:`derive_chunk_usage` (one chunk's own facts) and :func:`derive_fleet_usage`
    (an arbitrary, fleet-wide set of rows) alike — describing a fleet-wide total in
    chunk terms would be a truthful-name smell (``canon:truthful-names``).

    **This is the one canonical owner of the lower-bound + PARTIAL cost contract**
    (``canon:one-owner``) — every other usage total on the wire
    (:class:`~blizzard.wire.chunk.ChunkUsageTotalView`,
    :class:`~blizzard.wire.fleet.FleetSpendView`) and in the runner store
    (:class:`~blizzard.runner.store.repository.UsageTotals`) points back to this
    docstring rather than restating it: token counts are always exact; ``cost_usd``
    sums only the rows that carry one — a cost-absent row (the envelope-less
    transcript-sum fallback) contributes ``$0``, never a fabricated figure — so
    ``cost_partial`` (True iff any summed row's ``cost_usd`` was absent) tells the
    caller ``cost_usd`` is then a **lower bound**, never the true spend, and must be
    surfaced as PARTIAL rather than presented as exact."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool


def _sum_usage(rows: list[UsageFact]) -> UsageTotal:
    """Sum ``rows`` into one total — see :class:`UsageTotal` for the lower-bound +
    PARTIAL contract this implements. Shared by :func:`derive_chunk_usage` (one
    chunk's facts) and :func:`derive_fleet_usage` (an arbitrary set of rows, e.g.
    fleet spend-since)."""
    return UsageTotal(
        input_tokens=sum(u.input_tokens for u in rows),
        output_tokens=sum(u.output_tokens for u in rows),
        cache_read_tokens=sum(u.cache_read_tokens for u in rows),
        cache_create_tokens=sum(u.cache_create_tokens for u in rows),
        cost_usd=sum(u.cost_usd for u in rows if u.cost_usd is not None),
        cost_partial=any(u.cost_usd is None for u in rows),
    )


def derive_chunk_usage(facts: ChunkFacts) -> UsageTotal:
    """Sum a chunk's usage facts into its derived total — tokens by class + cost.

    Deliberately unfenced by epoch (unlike the status derivations above): every recorded
    usage row is real spend, so every row is summed regardless of which epoch minted it.
    A pure function of already-loaded facts, unit-testable with zero store.
    """
    return _sum_usage(facts.usage)


def derive_fleet_usage(rows: list[UsageFact]) -> UsageTotal:
    """Sum usage facts across the whole fleet into one total (issue #60) — the fleet
    spend-since read's derivation. Same summation as :func:`derive_chunk_usage`, over an
    arbitrary set of rows (here: every usage fact at or after a cutoff instant,
    :meth:`IReadChunkRepository.usage_since`) rather than one chunk's own facts."""
    return _sum_usage(rows)


# --- Question rows (the ask/answer rendezvous) -------------------------------


@dataclass(frozen=True)
class QuestionRow:
    """A durable question row with its derived answer state.

    The full surfacing shape behind ``blizzard hub status`` and the chunk detail's
    open-questions list. Open/answered is **derived**: a question is answered
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
    """The result of an answer write — first-write-wins CAS.

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

    def find_live_holder(self, pointer: PmPointer) -> str | None:
        """The chunk_id of a live (non-terminal) chunk holding ``pointer``, or None."""
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

    def usage_since(self, since: datetime) -> list[UsageFact]:
        """Every usage fact recorded at or after ``since``, across every chunk — the
        fleet spend-since read's input (issue #60); the caller derives the total via
        :func:`derive_fleet_usage`."""
        ...


class IWriteChunkRepository(IReadChunkRepository, Protocol):
    """Read-write chunk access. Only the domain layer depends on this variant."""

    def mint(self, chunk: Chunk) -> None: ...
    def record_promote(self, chunk_id: str, *, at: datetime) -> None:
        """Record a ``chunk.promoted`` fact — flips ``not_ready`` to ``ready``.

        Idempotent: a chunk already promoted keeps its first row, so a re-promote (a
        double board click, a CLI retry) writes nothing and the status is unchanged."""
        ...

    def record_lease(self, chunk_id: str, *, epoch: int, runner_id: str, at: datetime) -> None: ...
    def set_runner_high_water(self, runner_id: str, *, seq: int, at: datetime) -> None:
        """Advance a runner's applied-seq high-water mark (upsert)."""
        ...

    def record_route(self, route: Route, *, token_hash: str, at: datetime) -> None:
        """Record the route **and** mint its capability token's fact, atomically (issue #84a).

        ``token_hash`` is the sha256 hex digest of the claim's plaintext route token —
        already minted and hashed by the caller (``bzh:domain-takes-objects``); this
        appends the :class:`RouteTokenMintedFact` in the same store write as
        ``route_created`` (one transaction), never a column on the route fact itself
        (``bzh:facts-not-status``)."""
        ...

    def record_route_released(self, chunk_id: str, *, at: datetime) -> None: ...

    def record_route_token(self, chunk_id: str, *, token_hash: str, at: datetime) -> None:
        """Append a fresh :class:`RouteTokenMintedFact` for the chunk's route — the
        re-key path (issue #84b). Never mutates the prior token fact
        (``bzh:facts-not-status``): the newest-fact-wins derivation
        (:func:`newest_live_route_token`) supersedes it immediately, with no separate
        revocation step. ``token_hash`` is already minted and hashed by the caller
        (``bzh:domain-takes-objects``)."""
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

        Append-only, and the sole input :func:`bounce_count` derives from. Guarded by
        the natural key: a redelivery replay after a ``kill -9`` between this write and
        the routing/escalation write that follows it (:meth:`record_hub_step_transition` /
        :meth:`record_bounce_escalation`) re-enters harmlessly and never double-counts.
        Returns True iff it wrote, False when already recorded at this epoch."""
        ...

    def record_bounce_escalation(
        self, chunk_id: str, *, epoch: int, runner_id: str, takeover_command: str, at: datetime
    ) -> bool:
        """Escalate a chunk whose bounce count crossed its node's cap (#64), atomically
        and idempotently.

        The hub lease and the escalation fact land in one transaction, guarded by the
        escalation's existence at this epoch — a redelivery replay re-enters harmlessly
        and never double-escalates. No transition is recorded: the chunk's held route and
        stuck node are untouched, exactly like a normal retries-exhausted escalation — it
        derives ``needs_human`` with the bounce history still readable in chunk detail.
        Returns True iff it wrote, False when already recorded."""
        ...

    def record_escalation(self, chunk_id: str, *, epoch: int, takeover_command: str, at: datetime) -> None:
        """Record an ``escalation.recorded`` fact reported up by a runner.

        Retries exhausted (or a dead worker past the cap): the chunk derives
        ``needs_human`` until a later lease mint supersedes it. The runner-composed
        takeover command rides along so the parked session is resumable."""
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
        Idempotency rides the caller's per-runner outbound-buffer seq high-water mark
        (:class:`FactIngestService`) — a seq already applied never reaches this method a
        second time, so no second dedup key is needed here."""
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
        """Open a gate decision, committing any step artifacts atomically.

        A graph gate passes no artifacts (they landed with the arriving transition); a
        runner-config gate carries the gated step's artifacts here, exactly where the
        step's transition would have written them."""
        ...

    def record_decision_resolution(self, decision_id: str, *, choice: str, resolved_by: str, at: datetime) -> bool:
        """First-write-wins CAS: record the person's choice, or return ``False`` if
        the decision was already resolved (the loser is told who won)."""
        ...

    def record_requeue(self, chunk_id: str, *, at: datetime) -> None:
        """Record a ``requeue.recorded`` fact — supersedes an open escalation."""
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
        model: str | None,
        epoch: int,
        at: datetime,
        artifacts: list[ArtifactRow],
    ) -> bool:
        """Record a cross-graph migration atomically and idempotently (issue #90).

        In one transaction: the ``chunk_migrations`` fact, the ``chunks.graph_id`` re-pin
        (+ ``chunks.model`` when ``model`` is given), the ``route_released``, and the
        submitting node-step's ``artifacts`` (so the triage node's reasoning asset carries
        to the landing claim — the migration branch bypasses :meth:`record_transition`,
        where a step's artifacts normally commit). Idempotent by ``(chunk_id,
        from_node_id, epoch)`` — a redelivery replay writes nothing. No lease is minted:
        the fact is recorded at the submitting epoch, and the next claim mints a fresh
        higher one. Returns True iff it wrote, False on a replay."""
        ...

    def record_queue_position(self, chunk_id: str, *, position: float, at: datetime) -> None:
        """Append a ready chunk's new queue position; order derives."""
        ...

    def add_pm_pointers(self, chunk_id: str, pointers: list[PmPointer], *, at: datetime) -> None:
        """Fold PM pointers into a group survivor, de-duped by (source, ref)."""
        ...

    def record_grouped(self, chunk_id: str, *, grouped_into: str, at: datetime) -> None:
        """Record ``chunk.grouped`` — the merged-away chunk becomes ephemeral."""
        ...

    def record_pause(self, chunk_id: str, *, paused: bool, by: str, at: datetime) -> None:
        """Append a ``chunk.paused``/``chunk.resumed`` fact — newest-fact-wins (issue #46)."""
        ...

    def set_graph(self, chunk_id: str, *, graph_id: str) -> None:
        """Repin a not-ready chunk to a different workflow graph (issue #27).

        A plain column overwrite, not an append-only fact: ``graph_id`` was already a
        mint-time column with no fact log behind it, and this keeps the same shape.
        The caller (:class:`~blizzard.hub.domain.edit.EditService`) has already checked
        the chunk is still ``not_ready``."""
        ...

    def set_model(self, chunk_id: str, *, model: str) -> None:
        """Repin a not-ready chunk's model selection (issue #27) — see :meth:`set_graph`."""
        ...

    # --- The generic hub command node (#65) ---------------------------------

    def acquire_hub_exec_slot(self, chunk_id: str, *, node_id: str, at: datetime, stale_after: timedelta) -> str | None:
        """Acquire the fleet-wide hub-execution serialization slot, or ``None`` if busy.

        A FACT-based lease (``bzh:facts-not-status``), not an in-process lock: insert-if-
        none-live in one transaction. Reentrant for the chunk that already holds it (a
        later step of the same run re-acquires the same slot id, a no-op); a live slot
        held by a **different** chunk defers this caller (returns ``None``) unless it is
        older than ``stale_after`` against the injected clock, in which case it is
        reclaimed as abandoned (a kill -9 mid-run) before this caller's own slot is
        minted. Returns the acquired/held slot id, or ``None`` while genuinely busy."""
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
        recorded this artifact writes nothing a second time. Ordinary artifact rows —
        durable, shown in chunk detail, fed into subsequent node envelopes exactly like a
        worker-produced artifact. Returns True iff it wrote, False when already recorded.
        """
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
        """Record a generic hub command node's exit transition, atomically and
        idempotently (#65) — the ``HubNodeExecutor`` counterpart to
        :meth:`finalize_delivery`, generalized to any authored target (including the
        reserved terminal). The hub lease and the transition land in one transaction;
        ``release_route`` is True only when
        ``to_node_id`` is the reserved terminal, writing the route release alongside.
        Guarded by the transition's existence at ``(chunk_id, from_node_id, epoch)``: a
        redelivery replay after a ``kill -9`` re-enters harmlessly. Returns True iff it
        wrote, False when already recorded."""
        ...

    def record_hub_node_poll(self, chunk_id: str, *, node_id: str, epoch: int, at: datetime) -> None:
        """Append one pending-poll-attempt fact (#66) — never a transition.

        Append-only: an at-least-once poll attempt is harmless to record twice (it only
        ever widens the interval/timeout gating's read, never double-counts anything
        load-bearing), so this carries no idempotency guard. Stamped from the injected
        clock; :func:`hub_node_pending` and :func:`hub_node_poll_history` are the sole
        readers."""
        ...
