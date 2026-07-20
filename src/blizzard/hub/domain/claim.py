"""Route claim — how a runner takes work.

The ``POST /routes`` domain rule: acquisition is the birth of a **complete** route
fact. The hub accepts **exactly one** claim per chunk — a second claim on a chunk
that already has a live route loses with a :class:`ClaimConflict` (surfaced 409),
and the winning claim's result carries the chunk's first node envelope so the runner
starts working without a second round-trip.

A claim from a runner the registry marks paused is refused before the race is even
run, with the distinct :class:`ClaimDeniedPaused` (surfaced 403) — not a race loss,
not an epoch fence, but the hub's own arbiter enforcing its pause brake rather than
trusting the runner to have already read it back on pull (issue #44). The check reads
:class:`~blizzard.hub.domain.registry.RunnerRegistration.hub_paused`, itself derived
from the same ``runner_pause_facts`` the registry appends — no second source of
truth — and only that brake: a *locally*-paused-only runner (``locally_paused``) is
the runner's own restraint, not something the hub denies (issue #43 is that
mechanism). The denial stops only new claims; a route already held, and every
transition/completion/decision against it, is untouched (``bzh:facts-not-status`` —
pause is read at claim time, never persisted onto the chunk or the route).

The single-claim guarantee is the hub's single-writer property: the daemon
is the fleet's one arbiter, so the load-facts → check-live-route →
record-route sequence must run as an atomic compare-and-set. FastAPI serves sync
routes from a threadpool, so two runners' claims can arrive concurrently; a lock
serializes the CAS (the hub is one process — an in-process lock is the whole
arbitration surface, cross-machine or not). The same lock is injected into
:class:`~blizzard.hub.domain.edit.EditService` (issue #120, one instance per hub built
at the composition root — ``bzh:dependency-injection``): widening the edit window onto
``ready`` chunks (#120) means an edit's own check-then-act can now land in the same
window a claim does, and sharing this lock is what makes the two resolve to exactly one
winner rather than a torn read of a chunk's live-route state. Sharing the lock alone is
not enough, though: the edge resolves the chunk (and its pinned graph) *before* the
claim reaches the lock (``bzh:domain-takes-objects``), so ``_claim_locked`` re-reads
the chunk fresh once inside it and re-resolves the graph if an edit already moved
``graph_id`` in that window — otherwise the envelope built for the winning claim could
still describe the graph the edit just superseded, even though the persisted column
already shows the new one. The claim does **not** mint the
executing lease
: the runner mints it and reports ``lease.minted`` up through its outbound
buffer to ``POST /events``, and the completion fence checks against that. The
claim envelope carries the chunk's current epoch (``latest`` reported so far, or 0
before the runner's first lease report) so the worker starts without a round-trip;
the runner's own lease epoch — not this value — is what the fence consumes.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.hub.domain.enrollment import hash_token
from blizzard.hub.domain.envelope import build_node_envelope
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.graph import Graph, IReadGraphRepository
from blizzard.hub.domain.registry import IReadRunnerRegistry
from blizzard.hub.domain.work import (
    Chunk,
    ChunkStatus,
    IWriteChunkRepository,
    current_node_id,
    derive_chunk_status,
    latest_epoch,
)
from blizzard.wire.envelope import NodeEnvelope

#: `secrets.token_urlsafe` byte count for the route capability token — same length as
#: the runner bearer token (`hub/domain/enrollment.py`), for the same reason: a
#: 43-character URL-safe secret comfortably beyond brute-force range.
_ROUTE_TOKEN_BYTES = 32

# Same set `apply.py`/`decisions.py` guard on — duplicated here rather than shared,
# following this codebase's existing per-module convention for this constant.
_TERMINAL_STATUSES = frozenset({ChunkStatus.STOPPED, ChunkStatus.DONE})

# Crash point (``bzh:crash-point-registry``, issue #84b) — the claim-family boundary: the
# route and its capability-token fact (``record_route``) are durable, but the plaintext
# token has not yet reached the runner in the HTTP response. A kill here is recovered
# generically by the runner's own ``_reconcile_interrupted_claims``/``_adopt_interrupted_claim``
# (``runner/loop/steps.py``): the hub already shows the route live and held by this
# runner, so the runner adopts rather than re-claiming, and re-keys since it has no
# ``route_tokens`` row for the chunk. Reached by the generic build->deliver sweep
# scenario — every claim in that scenario passes through here — so no dedicated crash
# scenario is needed, only the module's addition to `_INSTRUMENTED_MODULES` and to the
# bounded CI subset (`tests/crash/test_kill9_sweep.py`).
_CP_CLAIM_AFTER_PERSIST_BEFORE_RESPONSE = crashpoint(
    "claim.after-persist.before-response",
    "the route + its route_token_minted fact are durable; the plaintext has not yet reached the runner",
)


class ClaimConflict(Exception):
    """The chunk already has a live route — this claim lost the race."""

    def __init__(self, *, held_by_runner_id: str) -> None:
        super().__init__(f"chunk already claimed by runner {held_by_runner_id}")
        self.held_by_runner_id = held_by_runner_id


class ClaimDeniedPaused(Exception):
    """The claiming runner is paused at the hub registry — refused before any race (issue #44).

    Distinct from :class:`ClaimConflict`: this runner did not lose to another claimant,
    it was never eligible to claim in the first place. Closes the gap between a hub
    pause landing and the runner's next pull mirroring it — the hub is the arbiter and
    stops the claim itself rather than trusting the runner to have already adhered."""

    def __init__(self, *, runner_id: str) -> None:
        super().__init__(f"runner {runner_id} is paused at the hub")
        self.runner_id = runner_id


class ClaimDeniedTerminal(Exception):
    """The chunk is already terminal ({done, stopped}) — refused before the race,
    mirroring :class:`ClaimDeniedPaused`'s shape: this is not a race loss, the chunk
    can never be claimed again. Closes the peek-then-claim window ``hub stop``
    (issue #118) leaves open — the ready queue's own peek-time filter cannot see a
    stop that lands between a runner's peek and its claim POST, so this check
    re-derives status fresh, under the claim lock, rather than trusting the peek."""

    def __init__(self, *, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not claimable")
        self.chunk_id = chunk_id
        self.status = status


@dataclass(frozen=True)
class ClaimResult:
    """A won claim — the route fact, its first node envelope, and the route's
    plaintext capability token (issue #84a).

    ``route_token`` is returned exactly once, here — the hub persists only its sha256
    hash (:meth:`~blizzard.hub.domain.work.IWriteChunkRepository.record_route`'s
    ``token_hash``); :class:`~blizzard.hub.domain.fleet.Route` itself stays
    dependency-free and carries no secret."""

    route: Route
    envelope: NodeEnvelope
    route_token: str


class ClaimService:
    """Claim a chunk for a runner, exactly-one-wins, and paused-runners-need-not-apply."""

    def __init__(
        self,
        *,
        chunks: IWriteChunkRepository,
        graphs: IReadGraphRepository,
        registry: IReadRunnerRegistry,
        clock: IClock,
        claim_lock: threading.Lock,
    ) -> None:
        self._chunks = chunks
        # Re-resolves the chunk's graph fresh under the lock (see `_claim_locked`)
        # when an edit repinned it after the edge resolved the caller's `graph` but
        # before this claim reached the lock — never builds the envelope from a
        # graph the edit already superseded.
        self._graphs = graphs
        self._registry = registry
        self._clock = clock
        # Serializes the check-live-route → record-route CAS across concurrent claims
        # on one hub daemon, and — since issue #120 — EditService's own
        # check-status/write over the same chunk. Injected from the composition root
        # rather than constructed here (``bzh:dependency-injection``): one lock per
        # hub, shared by both services, so a claim and an edit racing the same chunk
        # can't interleave; contention is a claim-rate concern, not a correctness one.
        self._claim_lock = claim_lock

    def claim(
        self,
        chunk: Chunk,
        graph: Graph,
        *,
        runner_id: str,
        workspace_id: str,
        environment_ids: list[str],
    ) -> ClaimResult:
        # Checked before the lock: a paused runner is refused regardless of whether it
        # would have won the race, so there is nothing here for the CAS to serialize.
        # An unregistered runner (`get_runner` returns None) cannot have been paused —
        # `FleetService.set_paused` requires a known runner — so it is not denied.
        registration = self._registry.get_runner(runner_id)
        if registration is not None and registration.hub_paused:
            raise ClaimDeniedPaused(runner_id=runner_id)
        with self._claim_lock:
            return self._claim_locked(
                chunk, graph, runner_id=runner_id, workspace_id=workspace_id, environment_ids=environment_ids
            )

    def _claim_locked(
        self,
        chunk: Chunk,
        graph: Graph,
        *,
        runner_id: str,
        workspace_id: str,
        environment_ids: list[str],
    ) -> ClaimResult:
        existing = self._chunks.route_of(chunk.chunk_id)
        if existing is not None:
            raise ClaimConflict(held_by_runner_id=existing.runner_id)

        # The edge resolved `chunk`/`graph` before this claim reached the lock
        # (``bzh:domain-takes-objects``) — but issue #120 lets an edit's own
        # check-then-act repin the chunk's graph (or model) in that same window,
        # sharing this lock precisely so one of the two lands first. Re-read the
        # chunk now, under the lock, and always build from that fresh copy: an
        # edit that landed first may have moved `graph_id` and/or `model`, and the
        # handed-in objects must not be what the envelope is built from — a torn
        # read of exactly the kind this lock exists to close (see module docstring).
        current = self._chunks.get(chunk.chunk_id)
        if current is None:  # pragma: no cover - the chunk cannot vanish mid-claim
            raise ClaimConflict(held_by_runner_id=runner_id)
        if current.graph_id != chunk.graph_id:
            fresh_graph = self._graphs.get(current.graph_id)
            if fresh_graph is None:  # pragma: no cover - a pinned graph always resolves
                raise ClaimConflict(held_by_runner_id=runner_id)
            graph = fresh_graph
        chunk = current

        facts = self._chunks.load_facts(chunk.chunk_id)
        # A stop (or, degenerately, a done) landing between this runner's peek and its
        # claim POST is invisible to the queue's peek-time filter (issue #118) — that
        # filter only excludes a chunk that already derived non-``ready`` when it was
        # peeked. Re-derive fresh, here, under the claim lock, rather than trusting the
        # peek to still hold.
        status = derive_chunk_status(facts) if facts is not None else ChunkStatus.NOT_READY
        if status in _TERMINAL_STATUSES:
            raise ClaimDeniedTerminal(chunk_id=chunk.chunk_id, status=status)

        # The runner mints the lease and reports its epoch via POST /events;
        # the claim only carries the current epoch (0 before the first report) into
        # the envelope, and does not itself write a lease fact.
        epoch = latest_epoch(facts) or 0 if facts is not None else 0
        now = self._clock.now()

        route = Route(
            chunk_id=chunk.chunk_id,
            runner_id=runner_id,
            workspace_id=workspace_id,
            environment_ids=list(environment_ids),
            created_at=now,
        )
        # Minted fresh per acquisition (issue #84a): the plaintext is returned once on
        # the result below and never stored — only its sha256 hash lands, appended as
        # its own route_token_minted fact in the same store write as record_route.
        route_token = secrets.token_urlsafe(_ROUTE_TOKEN_BYTES)
        self._chunks.record_route(route, token_hash=hash_token(route_token), at=now)
        _CP_CLAIM_AFTER_PERSIST_BEFORE_RESPONSE.reached()

        node_id = (current_node_id(facts) if facts is not None else None) or graph.entry_node_id
        node = graph.node_by_id(node_id)
        if node is None:  # pragma: no cover - a pinned graph always resolves its own node
            raise ClaimConflict(held_by_runner_id=runner_id)
        envelope = build_node_envelope(
            chunk=chunk,
            node=node,
            artifacts=self._chunks.load_artifacts(chunk.chunk_id),
            epoch=epoch,
        )
        return ClaimResult(route=route, envelope=envelope, route_token=route_token)

    def rekey(self, route: Route) -> str:
        """Rotate a live route's capability token (issue #84b) — the lost-plaintext
        recovery: a runner that crashed between the mint and reading the claim
        response back has no other way to learn its token.

        Mints a fresh ``secrets.token_urlsafe`` and appends it as a new
        ``route_token_minted`` fact (``bzh:facts-not-status`` — never a mutation of
        the prior fact); the newest-fact-wins derivation
        (:func:`~blizzard.hub.domain.work.newest_live_route_token`) supersedes the old
        token immediately, with no separate revocation step. Idempotent by
        construction: a re-run appends another fact and the runner simply stores
        whichever plaintext it last received. Takes the already-resolved live
        :class:`~blizzard.hub.domain.fleet.Route` (``bzh:domain-takes-objects`` — the
        caller confirms liveness and the requesting runner's ownership via
        ``route_of``/``assert_owns`` before calling this)."""
        route_token = secrets.token_urlsafe(_ROUTE_TOKEN_BYTES)
        self._chunks.record_route_token(route.chunk_id, token_hash=hash_token(route_token), at=self._clock.now())
        return route_token
