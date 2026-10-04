"""Lifecycle's decisions — what happens to a held chunk and its leases, decided over loaded values.

Every function here takes plain values (the current instant included, never a clock) and returns a
decision: which move to make, which record to write, or whether a gate admits. The steps and services
under ``lifecycle/`` and ``loop/`` read the store and the hub, call these, and carry the decision out.
"""

from __future__ import annotations

from collections.abc import Container, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.chunk_status import TERMINAL_STATUSES, ChunkStatus
from blizzard.foundation.leases import LeaseState
from blizzard.foundation.roles import domain_model
from blizzard.runner.leases import Lease
from blizzard.runner.leases.closure import ESCALATION_MINT
from blizzard.runner.lifecycle.shutdown_drain import SHUTDOWN_DRAIN_DEADLINE
from blizzard.runner.throttle.pause import PausePark, needs_pause_park
from blizzard.wire.chunk import ChunkDecisionStatusView, ChunkStatusView, ChunkUsageTotalView
from blizzard.wire.envelope import ApplyOutcome, NodeConfig, NodeEnvelope
from blizzard.wire.queue import QueuePeekEntry

if TYPE_CHECKING:
    from blizzard.runner.harness.identity import SessionReference
    from blizzard.runner.hub.client import RouteClaimOutcome
    from blizzard.runner.leases.elicitation import PendingElicitation
    from blizzard.runner.leases.escalations import ParkedEscalation
    from blizzard.runner.lifecycle.takeover import OpenTakeover
    from blizzard.wire.question import QuestionView

_PARTIAL_NOTE = " (PARTIAL — true spend may be higher)"


# --------------------------------------------------------------------------------------------- #
# Lease moves — which move is legal from which lease state, and which wait out the local brake.
# --------------------------------------------------------------------------------------------- #


class LeaseMove(StrEnum):
    """Every move an attempt makes on its lease."""

    #: Close a failed attempt and mint a retry at the same node.
    RETRY = "retry"
    #: Close and escalate — retries exhausted, an unresolvable owner, or the spend cap.
    ESCALATE = "escalate"
    #: Close an unspawned lease as reaped (crash residue with no live work).
    REAP_UNSPAWNED = "reap-unspawned"
    #: Kill a stalled live worker and fail its attempt.
    REAP_STALLED = "reap-stalled"
    #: Close as released — the hub routes the chunk elsewhere, or no longer knows it.
    ABANDON = "abandon"
    #: Interrupt the worker and park on an operator pause; the claim is kept.
    PARK_PAUSED = "park-paused"
    #: Park an exited, usage-limited generation in place.
    PARK_USAGE_LIMITED = "park-usage-limited"
    #: Kill and close a lease an operator restart fenced out; the claim is kept.
    PREEMPT = "preempt"
    #: Close after the hub applied the attempt's completion or decision.
    CLOSE_APPLIED = "close-applied"
    #: Start a process for the lease — a wake, a resume, a judge launch, or a fresh spawn.
    START_PROCESS = "start-process"


#: Which lease moves are legal from each lease state; an unspawned lease belongs to Reap's orphan arm.
LEASE_MOVES: Mapping[LeaseState, frozenset[LeaseMove]] = MappingProxyType(
    {
        # A reap fails the attempt, so a retry or an escalation follows it from these three.
        "spawning": frozenset(
            {LeaseMove.REAP_UNSPAWNED, LeaseMove.RETRY, LeaseMove.ESCALATE, LeaseMove.ABANDON, LeaseMove.PREEMPT}
        ),
        "running": frozenset(
            {
                LeaseMove.REAP_STALLED,
                LeaseMove.RETRY,
                LeaseMove.ESCALATE,
                LeaseMove.ABANDON,
                LeaseMove.PARK_PAUSED,
                LeaseMove.PREEMPT,
            }
        ),
        "stale": frozenset(
            {
                LeaseMove.REAP_STALLED,
                LeaseMove.RETRY,
                LeaseMove.ESCALATE,
                LeaseMove.ABANDON,
                LeaseMove.PARK_PAUSED,
                LeaseMove.PREEMPT,
            }
        ),
        "exited": frozenset(
            {
                LeaseMove.RETRY,
                LeaseMove.ESCALATE,
                LeaseMove.ABANDON,
                LeaseMove.PARK_PAUSED,
                LeaseMove.PARK_USAGE_LIMITED,
                LeaseMove.PREEMPT,
                LeaseMove.CLOSE_APPLIED,
                LeaseMove.START_PROCESS,
            }
        ),
        "parked": frozenset({LeaseMove.ABANDON, LeaseMove.PREEMPT, LeaseMove.START_PROCESS}),
        "backing-off": frozenset(
            {LeaseMove.ABANDON, LeaseMove.PARK_PAUSED, LeaseMove.PREEMPT, LeaseMove.START_PROCESS}
        ),
        "closed": frozenset(),
    }
)

#: The moves the runner's own brake holds back: starting a process, killing an irreplaceable worker, or escalating.
BRAKE_DEFERRED_MOVES: frozenset[LeaseMove] = frozenset(
    {LeaseMove.RETRY, LeaseMove.ESCALATE, LeaseMove.REAP_STALLED, LeaseMove.START_PROCESS}
)


def lease_move_legal(state: LeaseState, move: LeaseMove) -> bool:
    """Whether ``move`` is legal from a lease in ``state``."""
    return move in LEASE_MOVES[state]


def brake_defers(move: LeaseMove) -> bool:
    """Whether the runner's own brake holds ``move`` back until it lifts."""
    return move in BRAKE_DEFERRED_MOVES


def spawned(lease: Lease) -> bool:
    """A lease is spawned once its worker's pid and session are both recorded."""
    return lease.pid is not None and lease.session_id is not None


def routed_away(view: ChunkStatusView, runner_id: str) -> bool:
    """The hub no longer routes the chunk to this runner. A 404 reads as routed away at the call;
    an unreachable hub never does."""
    return view.route_runner_id != runner_id


# --------------------------------------------------------------------------------------------- #
# Takeover holds and the fence.
# --------------------------------------------------------------------------------------------- #


@domain_model
@dataclass(frozen=True)
class TakeoverHolds:
    """Every open takeover, as the one skip the loop steps apply: a person holding a session keeps
    every loop step off the leases it covers — the reference lease and anything at or below the
    takeover's fence. A lease minted after a re-claim sits above that and is the loop's again."""

    takeovers: tuple[OpenTakeover, ...] = ()

    @classmethod
    def of(cls, takeovers: Iterable[OpenTakeover]) -> TakeoverHolds:
        return cls(tuple(takeovers))

    def holds(self, chunk_id: str, epoch: int | None) -> bool:
        return any(takeover.holds(chunk_id, epoch) for takeover in self.takeovers)

    def holds_lease(self, lease: _FenceRef) -> bool:
        return self.holds(lease.chunk_id, lease.epoch)

    def covers(self, chunk_id: str) -> bool:
        """Some open takeover names the chunk, at whatever epoch."""
        return any(takeover.chunk_id == chunk_id for takeover in self.takeovers)


class _FenceRef(Protocol):
    """The two facts :class:`Fenced` needs — from a live lease or a closed one behind an open
    escalation, nothing else."""

    @property
    def chunk_id(self) -> str: ...

    @property
    def epoch(self) -> int: ...


@domain_model
@dataclass(frozen=True)
class Fenced:
    """Whether the hub has moved a chunk out from under a reference epoch still held here — an
    active lease or a closed one behind an open escalation.

    The signal is the fence itself: an epoch above the reference's, or a restart AT it. A takeover
    holding the reference is the one place a higher one is somebody else's business."""

    takeovers: TakeoverHolds

    def out(self, view: ChunkStatusView, ref: _FenceRef) -> bool:
        if self.takeovers.holds_lease(ref):
            return False
        if view.latest_epoch is not None and view.latest_epoch > ref.epoch:
            return True
        # A restart mints one above the newest epoch THE HUB knows, which excludes a reference whose
        # own mint is still buffered here — so it can land LEVEL with what it displaces.
        return any(epoch >= ref.epoch for epoch in view.restart_epochs)


# --------------------------------------------------------------------------------------------- #
# Claiming off the ready queue, and recovering an interrupted claim.
# --------------------------------------------------------------------------------------------- #


def pick_claim_entry(entries: Sequence[QueuePeekEntry], *, strict: bool) -> QueuePeekEntry | None:
    """The entry to claim next: strict holds at a blocked head; pass-over takes the first unblocked."""
    if not entries:
        return None
    if strict:
        head = entries[0]
        return None if head.blocked is not None else head
    return next((entry for entry in entries if entry.blocked is None), None)


class ClaimVerdict(StrEnum):
    """What a route claim came to."""

    WON = "won"
    #: The runner is paused at the hub — refused outright.
    PAUSED = "paused"
    #: The chunk is no longer claimable (ended, not ready, paused, or held for a person).
    NOT_CLAIMABLE = "not-claimable"
    #: An unmet prerequisite.
    DEPENDENCY = "dependency"
    #: The runner cannot serve the chunk.
    INCOMPATIBLE = "incompatible"
    #: Another runner won the race.
    LOST = "lost"


@domain_model
@dataclass(frozen=True)
class ClaimDisposition:
    """What FILL does after one claim: whether the peeked entry leaves the snapshot, whether the
    binding is released, and whether filling continues this tick."""

    verdict: ClaimVerdict
    drop_entry: bool
    release: bool
    keep_filling: bool


def claim_verdict(outcome: RouteClaimOutcome) -> ClaimVerdict:
    if outcome.denied_paused is not None:
        return ClaimVerdict.PAUSED
    if outcome.denied_terminal is not None:
        return ClaimVerdict.NOT_CLAIMABLE
    if outcome.denied_dependency is not None:
        return ClaimVerdict.DEPENDENCY
    if outcome.denied_incompatible is not None:
        return ClaimVerdict.INCOMPATIBLE
    if outcome.conflict is not None or outcome.claimed is None:
        return ClaimVerdict.LOST
    return ClaimVerdict.WON


def claim_disposition(verdict: ClaimVerdict, *, strict: bool) -> ClaimDisposition:
    """A paused denial stops filling — every later claim this tick would be refused the same way.
    A dependency block under strict keeps its entry so the next attempt holds at this head; every
    other loss releases the binding and moves on."""
    strict_hold = strict and verdict is ClaimVerdict.DEPENDENCY
    return ClaimDisposition(
        verdict=verdict,
        drop_entry=not strict_hold,
        release=verdict is not ClaimVerdict.WON,
        keep_filling=verdict is not ClaimVerdict.PAUSED and not strict_hold,
    )


def reclaim_verdict(outcome: RouteClaimOutcome) -> ClaimVerdict:
    """A reclaim reuses a binding left by an interrupted claim. Only a paused denial reads apart;
    a chunk no longer claimable, a dependency block, and an incompatible runner all mean the claim
    is not this runner's to make, the same as losing the race."""
    verdict = claim_verdict(outcome)
    if verdict in (ClaimVerdict.WON, ClaimVerdict.PAUSED):
        return verdict
    return ClaimVerdict.LOST


def owns_node_entry(
    view: ChunkStatusView,
    *,
    local_epoch: int,
    open_escalation_epoch: int | None,
    lease_in_binding_tenure: bool,
) -> bool:
    """Whether FILL, not ADVANCE, spawns a running chunk's lease-less current node.

    ADVANCE enters a strictly newer hub epoch through the node's declared session. FILL keeps the
    runner's own epoch (unless an escalation it raised there is still unflushed), the current restart
    entry, and a first claim with no lease in this binding tenure."""
    hub_epoch = view.latest_epoch
    if hub_epoch == local_epoch:
        return open_escalation_epoch != local_epoch
    if hub_epoch is not None and hub_epoch > local_epoch and hub_epoch in view.restart_epochs:
        return True
    return not lease_in_binding_tenure


class InterruptedClaimMove(StrEnum):
    """What becomes of a binding with no active lease behind it."""

    #: Spawn the current node for a requeued chunk.
    RESUME_REQUEUED = "resume-requeued"
    #: Spawn the current node for a claim whose spawn never minted a lease, when :func:`owns_node_entry` holds.
    ADOPT = "adopt"
    #: Claim again, reusing the binding — the claim never landed.
    RECLAIM = "reclaim"
    #: Release — requeued here, but routed elsewhere now.
    RELEASE_REQUEUED_ELSEWHERE = "release-requeued-elsewhere"
    #: Release — another runner won the chunk.
    RELEASE_OTHER_RUNNER = "release-other-runner"
    #: Release — no live route, and neither claimable nor ours.
    RELEASE_NO_ROUTE = "release-no-route"
    #: Keep the binding and look again next tick.
    HOLD = "hold"


def interrupted_claim_move(
    view: ChunkStatusView, *, runner_id: str, requeued: bool, braked: bool
) -> InterruptedClaimMove:
    """A pending requeue outranks everything; it resumes only once the chunk is neither paused nor
    ended, the mark left pending meanwhile. A resolved gate keeps its route live and is left to
    ADVANCE. A running chunk routed here is adopted — when FILL owns its node entry, a check
    against local epochs made only for this arm; a ready one is claimed again unless a brake
    holds new claims."""
    ours = view.route_runner_id == runner_id
    if requeued:
        if not ours:
            return InterruptedClaimMove.RELEASE_REQUEUED_ELSEWHERE
        if view.pause is not None or view.status in TERMINAL_STATUSES or view.status == ChunkStatus.PAUSED:
            return InterruptedClaimMove.HOLD
        return InterruptedClaimMove.RESUME_REQUEUED
    if view.decision is not None:
        return InterruptedClaimMove.HOLD
    if view.status == ChunkStatus.RUNNING and ours:
        return InterruptedClaimMove.ADOPT
    if view.status == ChunkStatus.READY:
        return InterruptedClaimMove.HOLD if braked else InterruptedClaimMove.RECLAIM
    if view.route_runner_id is not None and not ours:
        return InterruptedClaimMove.RELEASE_OTHER_RUNNER
    if view.route_runner_id is None:
        return InterruptedClaimMove.RELEASE_NO_ROUTE
    return InterruptedClaimMove.HOLD


def recovery_owner(latest: Lease | None) -> str | None:
    """The owner a recovery spawn mints under: the chunk's latest lease's own owner, else none —
    the genuinely fresh case the caller's default decides."""
    return latest.harness_id if latest is not None else None


# --------------------------------------------------------------------------------------------- #
# Minting a lease.
# --------------------------------------------------------------------------------------------- #


class MintOwnerSource(StrEnum):
    """Where a mint's owner comes from, in precedence order."""

    RESUME = "resume"
    EXPLICIT = "explicit"
    SELECT = "select"
    DEFAULT = "default"


def mint_owner_source(
    resume_from: SessionReference | None, harness_id: str | None, node: NodeConfig
) -> MintOwnerSource:
    """A resumed session's owner, then an explicit carried owner (a retry or a recovery), then
    selection among the node's acceptable set, then the runner's default."""
    if resume_from is not None:
        return MintOwnerSource.RESUME
    if harness_id is not None:
        return MintOwnerSource.EXPLICIT
    if node.session_harnesses:
        return MintOwnerSource.SELECT
    return MintOwnerSource.DEFAULT


def next_lease_epoch(local_latest: int, hub_floor: int) -> int:
    """One above both floors: the local fence alone is 0 for a chunk this runner never drove, so a
    migrated chunk would otherwise mint below what the hub already knows."""
    return max(local_latest, hub_floor) + 1


def resolved_retries_max(override: int | None, node_retries_max: int | None, default: int) -> int:
    """An explicit override (the zero-budget escalation mints), else the node's own, else the default."""
    if override is not None:
        return override
    return node_retries_max if node_retries_max is not None else default


def escalation_mint_admitted(open_escalation: ParkedEscalation | None) -> bool:
    """An escalation mint is refused while the chunk already has an open escalation."""
    return open_escalation is None


def retry_owner_admitted(harness_id: str | None, acceptable: Sequence[str]) -> bool:
    """A retry keeps its owner only while that owner is still in the node's acceptable set; an
    empty set admits any owner."""
    return not acceptable or harness_id in acceptable


# --------------------------------------------------------------------------------------------- #
# Failing, escalating, and preempting an attempt.
# --------------------------------------------------------------------------------------------- #


class FailureMove(StrEnum):
    ABANDON = "abandon"
    RETRY = "retry"
    DEFER = "defer"
    ESCALATE_OWNER_UNRESOLVABLE = "escalate-owner-unresolvable"
    ESCALATE_EXHAUSTED = "escalate-exhausted"


def failure_move(*, retried: int, retries_max: int, owner_blocked: bool, detached: bool, braked: bool) -> FailureMove:
    """A failed attempt on a chunk routed elsewhere (or gone) is abandoned before anything else —
    a retry would mint into a chunk this runner no longer holds. Then a retry while budget is left
    and the owner resolves; then a deferral under the local brake; then an escalation, for the
    unresolvable owner first."""
    if detached:
        return FailureMove.ABANDON
    if retried < retries_max and not owner_blocked:
        return FailureMove.RETRY
    if braked and brake_defers(LeaseMove.ESCALATE):
        return FailureMove.DEFER
    if owner_blocked:
        return FailureMove.ESCALATE_OWNER_UNRESOLVABLE
    return FailureMove.ESCALATE_EXHAUSTED


class OwnerUnresolvableMove(StrEnum):
    ABANDON = "abandon"
    DEFER = "defer"
    ESCALATE = "escalate"


def owner_unresolvable_move(*, detached: bool, braked: bool) -> OwnerUnresolvableMove:
    """The same detached-then-braked precedence :func:`failure_move` takes ahead of its escalation."""
    if detached:
        return OwnerUnresolvableMove.ABANDON
    if braked and brake_defers(LeaseMove.ESCALATE):
        return OwnerUnresolvableMove.DEFER
    return OwnerUnresolvableMove.ESCALATE


@domain_model
@dataclass(frozen=True)
class OwnerUnresolvableClosure:
    """How an owner-unresolvable escalation closes its lease."""

    #: ``unavailable`` for a known but unavailable owner, else ``unknown``.
    owner_status: str
    #: The store-only mint reason for a never-spawned escalation mint, else ``None`` (plain ``escalated``).
    closure_reason: str | None


def owner_unresolvable_closure(lease: Lease, *, unavailable: bool) -> OwnerUnresolvableClosure:
    """A lease that never spawned is the escalation mint itself and closes under its own reason."""
    return OwnerUnresolvableClosure(
        owner_status="unavailable" if unavailable else "unknown",
        closure_reason=ESCALATION_MINT if lease.session is None else None,
    )


# --------------------------------------------------------------------------------------------- #
# Dormant sessions: restart-resume, unpause, answers.
# --------------------------------------------------------------------------------------------- #


class RestartDisposition(StrEnum):
    PARK = "park"
    PREEMPT = "preempt"
    RESTART = "restart"
    ABANDON = "abandon"


def restart_disposition(view: ChunkStatusView, runner_id: str, *, fenced: bool) -> RestartDisposition:
    """A restart-marked lease on a chunk still routed here parks under a pause (the pause outranks a
    move), is preempted when a restart fenced it out while the runner was down, and otherwise
    resumes in place while the chunk runs. Anything else — routed away, ended — is abandoned."""
    ours = not routed_away(view, runner_id)
    if ours and view.pause is not None:
        return RestartDisposition.PARK
    if ours and fenced:
        return RestartDisposition.PREEMPT
    if ours and view.status == ChunkStatus.RUNNING:
        return RestartDisposition.RESTART
    return RestartDisposition.ABANDON


def pause_park_drain_expired(park: PausePark, *, now: datetime) -> bool:
    """The interrupt's budget has run out: a survivor of the park's interrupt is SIGKILLed."""
    return now >= park.parked_at + timedelta(seconds=SHUTDOWN_DRAIN_DEADLINE)


def park_names_elicitation(park: PausePark, elicitation: PendingElicitation | None) -> bool:
    """The park's interrupt signalled this standing elicitation — settling must wait on it too. An
    unnamed standing record is a usage-limit judge park's, left for its relaunch."""
    return (
        park.interrupted_elicitation_id is not None
        and elicitation is not None
        and elicitation.id == park.interrupted_elicitation_id
    )


class UnpauseMove(StrEnum):
    #: Still paused, or routed away — wait (Pull's lease reconcile owns a detach).
    WAIT = "wait"
    #: The lease is also ask-parked: clear the pause park; an answer restarts it.
    CLEAR_AWAIT_ANSWER = "clear-await-answer"
    #: A usage-limit judge park stands: relaunch the judge.
    RELAUNCH_JUDGE = "relaunch-judge"
    #: No warm environment or session to resume into.
    CANNOT_RESUME = "cannot-resume"
    #: Wake the dormant session.
    WAKE = "wake"


def unpause_move(
    view: ChunkStatusView, runner_id: str, *, ask_parked: bool, judge_parked: bool, has_env_and_session: bool
) -> UnpauseMove:
    """Once a pause park has settled and the brake admits a start: a lifted pause on a chunk still
    routed here wakes the session — unless a question underneath it or a standing judge park
    claims the resume instead."""
    if view.pause is not None or routed_away(view, runner_id):
        return UnpauseMove.WAIT
    if ask_parked:
        return UnpauseMove.CLEAR_AWAIT_ANSWER
    if judge_parked:
        return UnpauseMove.RELAUNCH_JUDGE
    if not has_env_and_session:
        return UnpauseMove.CANNOT_RESUME
    return UnpauseMove.WAKE


def answer_ready(question: QuestionView) -> bool:
    """A parked session wakes only on an answered question that carries its answer."""
    return question.answered and question.answer is not None


# --------------------------------------------------------------------------------------------- #
# Held chunks: applying the hub's answer, and driving a chunk with no active lease.
# --------------------------------------------------------------------------------------------- #


class ApplyMove(StrEnum):
    ENTER_NEXT = "enter-next"
    #: The next node waits out a per-chunk pause — the binding is held, nothing spawns.
    HOLD_PAUSED = "hold-paused"
    HOLD_FOR_HUB_NODE = "hold-for-hub-node"
    RELEASE_MIGRATED = "release-migrated"
    RELEASE_DONE = "release-done"
    HOLD_AT_GATE = "hold-at-gate"
    NONE = "none"


def apply_move(outcome: ApplyOutcome, next_envelope: NodeEnvelope | None, *, chunk_paused: bool) -> ApplyMove:
    """What the hub's answer to an applied step means here. A paused chunk starts no worker: the
    binding is held, and the node is entered once the pause lifts."""
    if outcome == ApplyOutcome.NEXT and next_envelope is not None:
        return ApplyMove.HOLD_PAUSED if chunk_paused else ApplyMove.ENTER_NEXT
    if outcome == ApplyOutcome.HUB_NODE_TAKEN:
        return ApplyMove.HOLD_FOR_HUB_NODE
    if outcome == ApplyOutcome.MIGRATED:
        return ApplyMove.RELEASE_MIGRATED
    if outcome == ApplyOutcome.DONE:
        return ApplyMove.RELEASE_DONE
    if outcome == ApplyOutcome.PARKED_AT_GATE:
        return ApplyMove.HOLD_AT_GATE
    return ApplyMove.NONE


def chunk_paused(view: ChunkStatusView) -> bool:
    """A per-chunk pause stands — the fact, or the status it derives."""
    return view.pause is not None or view.status == ChunkStatus.PAUSED


class HeldChunkMove(StrEnum):
    RELEASE_DONE = "release-done"
    RELEASE_STOPPED = "release-stopped"
    #: A gate park whose route left this runner — nothing left here to resolve.
    RELEASE_DETACHED_GATE = "release-detached-gate"
    RESOLVE_GATE = "resolve-gate"
    SPAWN_ADVANCED = "spawn-advanced"
    POLL_HUB_NODE = "poll-hub-node"
    HOLD = "hold"


#: The held-chunk moves an open takeover suppresses: those that would start a session or move the chunk on.
TAKEOVER_SUPPRESSED_HELD_MOVES: frozenset[HeldChunkMove] = frozenset(
    {HeldChunkMove.RESOLVE_GATE, HeldChunkMove.SPAWN_ADVANCED}
)


def held_chunk_reads_local_epoch(view: ChunkStatusView) -> bool:
    """The one held-chunk arm that compares against this runner's own latest epoch: a running
    chunk the hub reports an epoch for."""
    return view.status == ChunkStatus.RUNNING and view.latest_epoch is not None


def held_chunk_move(
    view: ChunkStatusView, *, runner_id: str, local_latest_epoch: int, taken_over: bool
) -> HeldChunkMove:
    """A held chunk with no active lease: an ended chunk releases; a decided gate whose route left
    this runner releases, a decided gate still here resolves; a running chunk at a strictly newer
    hub epoch enters its node; a chunk at a hub node is stepped. The strictly-higher epoch is
    load-bearing: a just-escalated chunk still derives ``running`` at the SAME epoch until its fact
    flushes, and would re-spawn forever."""
    move = _held_chunk_move(view, runner_id=runner_id, local_latest_epoch=local_latest_epoch)
    if taken_over and move in TAKEOVER_SUPPRESSED_HELD_MOVES:
        return HeldChunkMove.HOLD
    return move


def _held_chunk_move(view: ChunkStatusView, *, runner_id: str, local_latest_epoch: int) -> HeldChunkMove:
    if view.status == ChunkStatus.DONE:
        return HeldChunkMove.RELEASE_DONE
    if view.status == ChunkStatus.STOPPED:
        return HeldChunkMove.RELEASE_STOPPED
    decision = view.decision
    if decision is not None and routed_away(view, runner_id):
        return HeldChunkMove.RELEASE_DETACHED_GATE
    if decision is not None and decision.resolved_choice is not None and not decision.transitioned:
        return HeldChunkMove.RESOLVE_GATE
    hub_epoch = view.latest_epoch
    if view.status == ChunkStatus.RUNNING and hub_epoch is not None and hub_epoch > local_latest_epoch:
        return HeldChunkMove.SPAWN_ADVANCED
    if view.status == ChunkStatus.DELIVERING:
        return HeldChunkMove.POLL_HUB_NODE
    return HeldChunkMove.HOLD


def gate_resolution_lease_id(parked: Lease | None, decision: ChunkDecisionStatusView) -> str | None:
    """The resolving submission names the parked lease only when it sits at the decision's epoch."""
    return parked.lease_id if parked is not None and parked.epoch == decision.epoch else None


# --------------------------------------------------------------------------------------------- #
# The outbound drain: what a flushed completion or decision does to its lease.
# --------------------------------------------------------------------------------------------- #


class CompletionMove(StrEnum):
    FAIL = "fail"
    #: Close escalated under the spend cap — the next attempt is not spawned, no retry is spent.
    ESCALATE_SPEND_CAP = "escalate-spend-cap"
    CLOSE_AND_APPLY = "close-and-apply"


def completion_move(outcome: ApplyOutcome, *, capped: bool) -> CompletionMove:
    """A rejected completion fails the attempt. A completion that advanced the chunk while its
    spend reached the cap closes the lease escalated — so the escalation reads open here, like any
    other — instead of entering the next node. Anything else closes and applies."""
    if outcome == ApplyOutcome.FAILURE:
        return CompletionMove.FAIL
    if outcome == ApplyOutcome.NEXT and capped:
        return CompletionMove.ESCALATE_SPEND_CAP
    return CompletionMove.CLOSE_AND_APPLY


class DecisionMove(StrEnum):
    FAIL = "fail"
    CLOSE_PARKED = "close-parked"


def decision_move(outcome: ApplyOutcome) -> DecisionMove:
    """A rejected gate decision fails the attempt; an applied one parks the chunk on the decision."""
    return DecisionMove.FAIL if outcome == ApplyOutcome.FAILURE else DecisionMove.CLOSE_PARKED


def spend_cap_reached(cost: ChunkUsageTotalView, cap: float | None) -> bool:
    """The chunk's hub-derived spend — a lower bound — has reached the per-chunk cap."""
    return cap is not None and cost.cost_usd >= cap


def spend_cap_partial_note(cost: ChunkUsageTotalView) -> str:
    """Flags a spend total that is only a lower bound."""
    return _PARTIAL_NOTE if cost.billed_partial else ""


def spend_cap_detail(cost: ChunkUsageTotalView, cap: float) -> str:
    """The spend-cap escalation's detail line."""
    return f"spend cap ${cap:.2f} reached (spend ${cost.cost_usd:.2f}{spend_cap_partial_note(cost)})"


# --------------------------------------------------------------------------------------------- #
# Loop steps: REAP, restart-resume marking, PULL's lease reconcile, ADVANCE, FILL.
# --------------------------------------------------------------------------------------------- #


class ReapMove(StrEnum):
    #: A takeover holds it, or it is parked — the reap clock is stopped.
    SKIP = "skip"
    REAP_UNSPAWNED = "reap-unspawned"
    #: Exited — ADVANCE's (exit is the done declaration).
    LEAVE_EXITED = "leave-exited"
    #: Stalled, but the local brake holds the kill.
    DEFER = "defer"
    REAP_STALLED = "reap-stalled"
    RUN_ON = "run-on"


def reap_move(lease: Lease, *, taken_over: bool, parked: bool, alive: bool, stale: bool, braked: bool) -> ReapMove:
    """An unspawned lease is reaped even under the brake — crash residue with no live work. A
    stalled live worker is reaped unless the brake holds the kill; a pause is not a drain."""
    if taken_over or parked:
        return ReapMove.SKIP
    if not spawned(lease):
        return ReapMove.DEFER if braked and brake_defers(LeaseMove.REAP_UNSPAWNED) else ReapMove.REAP_UNSPAWNED
    if not alive:
        return ReapMove.LEAVE_EXITED
    if not stale:
        return ReapMove.RUN_ON
    return ReapMove.DEFER if braked and brake_defers(LeaseMove.REAP_STALLED) else ReapMove.REAP_STALLED


def resumable(
    lease: Lease,
    *,
    parked: Container[str],
    pending_submission: Container[str],
    eliciting: Container[str],
    backing_off: Container[str],
    taken_over: bool,
) -> bool:
    """Whether a restart marks the lease for same-lease resume: spawned, and neither held by a
    takeover, parked, mid-submission, mid-elicitation, nor backing off — each would wake a second
    process on the session, or skip a wait already durable."""
    if not spawned(lease) or taken_over:
        return False
    lease_id = lease.lease_id
    return not (
        lease_id in parked or lease_id in pending_submission or lease_id in eliciting or lease_id in backing_off
    )


def crash_orphaned(lease: Lease, *, session_ended: bool, alive: bool, stale: bool) -> bool:
    """Of the resumable leases, a crash orphaned those whose session neither declared itself done,
    nor survived the crash, nor had already gone stale before it — the last is ADVANCE's to judge."""
    return not session_ended and not alive and not stale


class LeaseReconcileMove(StrEnum):
    ABANDON = "abandon"
    PARK = "park"
    PREEMPT = "preempt"
    NONE = "none"


def lease_reconcile_move(
    view: ChunkStatusView, lease: Lease, *, runner_id: str, pause_parked: Container[str], fenced: bool
) -> LeaseReconcileMove:
    """A stopped or routed-away chunk abandons its lease — under a forced takeover too: a detach
    gives the environments back, person or not. A pause parks a spawned lease once (it outranks a
    move); an unspawned one has no worker to park and is left to Reap's orphan arm. A fence-out
    preempts."""
    if view.status == ChunkStatus.STOPPED or routed_away(view, runner_id):
        return LeaseReconcileMove.ABANDON
    if view.pause is not None:
        state: LeaseState = "running" if spawned(lease) else "spawning"
        if lease_move_legal(state, LeaseMove.PARK_PAUSED) and needs_pause_park(lease.lease_id, pause_parked):
            return LeaseReconcileMove.PARK
        return LeaseReconcileMove.NONE
    if fenced:
        return LeaseReconcileMove.PREEMPT
    return LeaseReconcileMove.NONE


class AdvanceMove(StrEnum):
    SKIP_TAKEN_OVER = "skip-taken-over"
    SKIP_UNSPAWNED = "skip-unspawned"
    SKIP_RESUME_MARKED = "skip-resume-marked"
    SKIP_PENDING_SUBMISSION = "skip-pending-submission"
    ON_UNPAUSE = "on-unpause"
    ON_ANSWER = "on-answer"
    ON_OVERLOAD_BACKOFF = "on-overload-backoff"
    RUNNING = "running"
    EXITED = "exited"


def advance_move(
    lease: Lease,
    *,
    taken_over: bool,
    resume_marked: bool,
    pending_submission: bool,
    pause_parked: bool,
    ask_parked: bool,
    backing_off: bool,
    alive: bool,
) -> AdvanceMove:
    """ADVANCE's per-lease dispatch, in precedence order. A pause park is finished before an ask
    park is polled, and both before an overload backoff — an operator's resume is an explicit go
    that skips the rest of the wait. A backing-off lease is checked before liveness, since its
    worker has already exited."""
    if taken_over:
        return AdvanceMove.SKIP_TAKEN_OVER
    if not spawned(lease):
        return AdvanceMove.SKIP_UNSPAWNED
    if resume_marked:
        return AdvanceMove.SKIP_RESUME_MARKED
    if pending_submission:
        return AdvanceMove.SKIP_PENDING_SUBMISSION
    if pause_parked:
        return AdvanceMove.ON_UNPAUSE
    if ask_parked:
        return AdvanceMove.ON_ANSWER
    if backing_off:
        return AdvanceMove.ON_OVERLOAD_BACKOFF
    return AdvanceMove.RUNNING if alive else AdvanceMove.EXITED


def open_slots(max_agents: int, active: int) -> int:
    """How many new claims FILL may make this tick."""
    return max(max_agents - active, 0)
