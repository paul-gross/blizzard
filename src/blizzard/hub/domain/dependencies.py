"""Chunk dependency edges — declare and release, under the shared claim lock (issue #456).

A chunk names the chunks it depends on (``blizzard.hub.domain.chunks.dependencies``);
declaring refuses a cycle and admits only in :data:`PRE_CLAIM_STATUSES`, release has no
window. Both share ``ClaimService``/``EditService``/``RestartService``'s ``threading.Lock``
(issue #120) — pinned by ``tests/test_dependency_race.py`` and ``tests/test_dependency_service_component.py``."""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from blizzard.foundation.chunk_status import PRE_CLAIM_STATUSES, ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.dependencies import IWriteChunkDependenciesRepository
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.lifecycle import IReadChunkLifecycleRepository
from blizzard.hub.domain.work import Chunk, DependencyEdge


class DependentNotEditable(Exception):
    """The dependent chunk is outside :data:`PRE_CLAIM_STATUSES` — mirrors
    :class:`~blizzard.hub.domain.edit.ChunkNotEditable`'s shape for the one status window
    this service consults."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, dependencies are not declarable at this status")
        self.chunk_id = chunk_id
        self.status = status


class DependencyWouldCloseCycle(Exception):
    """Declaring this edge would close a cycle in the standing dependency graph. A
    self-edge (``dependent_chunk_id == prerequisite_chunk_id``) is the trivial cycle and
    raises this too — there is no separate exception for it."""

    def __init__(self, dependent_chunk_id: str, prerequisite_chunk_id: str) -> None:
        super().__init__(
            f"chunk {dependent_chunk_id} depending on {prerequisite_chunk_id} would close a cycle "
            "in the standing dependency graph"
        )
        self.dependent_chunk_id = dependent_chunk_id
        self.prerequisite_chunk_id = prerequisite_chunk_id


class NoStandingDependencyToRelease(Exception):
    """A release named an ordered pair with no standing edge."""

    def __init__(self, dependent_chunk_id: str, prerequisite_chunk_id: str) -> None:
        super().__init__(f"no standing dependency of {dependent_chunk_id} on {prerequisite_chunk_id}")
        self.dependent_chunk_id = dependent_chunk_id
        self.prerequisite_chunk_id = prerequisite_chunk_id


class PrerequisiteIsEphemeral(Exception):
    """A **declaration** named an ephemeral (grouped-away or deleted) prerequisite,
    raised by :class:`DependencyService` from a fresh ``is_ephemeral`` read taken under
    the shared claim lock — closing the race against every writer that shares the lock,
    `GroupService` included as of issue #460. Release never raises it."""

    def __init__(self, chunk_id: str) -> None:
        super().__init__(f"chunk {chunk_id} is ephemeral and cannot be named as a prerequisite")
        self.chunk_id = chunk_id


class DependencyService:
    """Declare and release a dependency edge between two chunks — the operator's ``a
    depends on b`` and its release (issue #456)."""

    def __init__(
        self,
        *,
        facts: IReadChunkFactsRepository,
        lifecycle: IReadChunkLifecycleRepository,
        dependencies: IWriteChunkDependenciesRepository,
        clock: IClock,
        claim_lock: threading.Lock,
    ) -> None:
        self._facts = facts
        self._lifecycle = lifecycle
        self._dependencies = dependencies
        self._clock = clock
        # The same lock ClaimService/EditService/RestartService already share (issue #120).
        self._claim_lock = claim_lock

    def declare(self, dependent: Chunk, prerequisite: Chunk, *, by: str) -> DependencyEdge:
        """Declare that ``dependent`` depends on ``prerequisite``.

        Idempotent: an already-standing pair is reported back rather than refused.
        Otherwise refuses, writing nothing, when ``dependent`` is gone or not
        :data:`PRE_CLAIM_STATUSES`, ``prerequisite`` is ephemeral, or it would close a cycle."""
        with self._claim_lock:
            return self._declare_locked(dependent, prerequisite, by=by)

    def _declare_locked(self, dependent: Chunk, prerequisite: Chunk, *, by: str) -> DependencyEdge:
        # Imported locally: `queue.py` imports this module's `plan_fold`/
        # `would_close_a_cycle` at module scope (issue #460), so a module-level import
        # the other way would close a circular-import loop.
        from blizzard.hub.domain.queue import ChunkNotFound

        existing = self._dependencies.standing_edge(dependent.chunk_id, prerequisite.chunk_id)
        if existing is not None:
            return existing

        # A `None` load means gone under this lock (issue #456) — refuse rather than
        # substitute a synthetic status, mirroring `DeleteService.delete`.
        facts = self._facts.load_facts(dependent.chunk_id)
        if facts is None:
            raise ChunkNotFound(dependent.chunk_id)
        status = facts.status()
        if status not in PRE_CLAIM_STATUSES:
            raise DependentNotEditable(dependent.chunk_id, status)

        # Re-derived under the same lock: closes the race against every writer holding it
        # — delete and the fold both included (issue #460).
        if self._lifecycle.is_ephemeral(prerequisite.chunk_id):
            raise PrerequisiteIsEphemeral(prerequisite.chunk_id)

        standing = self._dependencies.list_standing_edges()
        if would_close_a_cycle(standing, [(dependent.chunk_id, prerequisite.chunk_id)]):
            raise DependencyWouldCloseCycle(dependent.chunk_id, prerequisite.chunk_id)

        return self._dependencies.declare(dependent.chunk_id, prerequisite.chunk_id, by=by, at=self._clock.now())

    def release(self, edge: DependencyEdge, *, by: str) -> DependencyEdge:
        """Release ``edge``'s ``(dependent_chunk_id, prerequisite_chunk_id)`` pair — the
        store resolves whichever edge currently stands for it, not necessarily ``edge``
        itself, so a pair released then freshly re-declared releases the new edge,
        silently, in ``edge``'s place. Admitted whenever some edge stands for the pair
        (no status window); refuses, writing nothing, when none does."""
        with self._claim_lock:
            released = self._dependencies.release(
                edge.dependent_chunk_id, edge.prerequisite_chunk_id, by=by, at=self._clock.now()
            )
            if released is None:
                raise NoStandingDependencyToRelease(edge.dependent_chunk_id, edge.prerequisite_chunk_id)
            return released


def derive_blocked_markings(
    standing_edges: Iterable[DependencyEdge], statuses: Mapping[str, ChunkStatus]
) -> dict[str, str]:
    """The blocked marking per dependent chunk id — never folded into :class:`ChunkFacts`
    or its :meth:`~blizzard.hub.domain.work.ChunkFacts.status` (``bzh:facts-not-status``,
    issue #457, D1). ``standing_edges`` must already carry
    :meth:`~blizzard.hub.domain.chunks.dependencies.IReadChunkDependenciesRepository.list_standing_edges`'s
    own ``(declared_at, dependency_id)`` order: the first unmet edge per dependent wins, one hop,
    with no chain walk (D4). A prerequisite absent from ``statuses`` still blocks (D3) — deletion
    now refuses a standing edge onto a live prerequisite (issue #460), so this only guards the
    accepted residual race between a status read and a concurrent write, not an ordinary
    reachable path.

    Only a dependent read at :data:`PRE_CLAIM_STATUSES` derives a marking (review round 1 F1):
    the marking answers why a chunk cannot yet be claimed, and that question stops applying once
    a dependent is claimed, running, delivering, human-gated, paused, or terminal — even though
    its edge, declared while it was still pre-claim, persists unreleased through all of that. A
    dependent absent from ``statuses`` is treated the way its default ``not_ready`` would read —
    eligible, not excluded."""
    markings: dict[str, str] = {}
    for edge in standing_edges:
        if edge.dependent_chunk_id in markings:
            continue
        dependent_status = statuses.get(edge.dependent_chunk_id)
        if dependent_status is not None and dependent_status not in PRE_CLAIM_STATUSES:
            continue
        if statuses.get(edge.prerequisite_chunk_id) is ChunkStatus.DONE:
            continue
        markings[edge.dependent_chunk_id] = edge.prerequisite_chunk_id
    return markings


def would_close_a_cycle(standing: list[DependencyEdge], added: list[tuple[str, str]]) -> bool:
    """Would folding ``added``'s ordered ``(dependent, prerequisite)`` pairs into
    ``standing``'s existing edges close a cycle in the resulting graph (issue #456,
    extended #460)? ``standing`` is assumed acyclic already — the domain's own
    maintained invariant — so a cycle can only pass through one of ``added``. Generalizes
    the single-edge check :class:`DependencyService` uses to declare an edge to the
    set-level question :class:`~blizzard.hub.domain.queue.GroupService` asks over a
    whole fold, so neither duplicates the graph-walk."""
    graph: dict[str, list[str]] = {}
    for edge in standing:
        graph.setdefault(edge.dependent_chunk_id, []).append(edge.prerequisite_chunk_id)
    for dependent, prerequisite in added:
        graph.setdefault(dependent, []).append(prerequisite)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}

    def visit(node: str) -> bool:
        color[node] = GRAY
        for neighbor in graph.get(node, []):
            state = color.get(neighbor, WHITE)
            if state == GRAY:
                return True
            if state == WHITE and visit(neighbor):
                return True
        color[node] = BLACK
        return False

    return any(visit(node) for node in list(graph) if color.get(node, WHITE) == WHITE)


@dataclass(frozen=True)
class FoldEdgePlan:
    """Per-target dependency-edge rewrite instructions for one fold (D3, D4, issue
    #460) — the release/mint pairing :class:`~blizzard.hub.domain.queue.GroupService`
    applies inside each target's own atomic write, plus the untouched remainder of
    ``standing`` the cycle check runs against."""

    release_by_target: dict[str, list[str]]
    mint_by_target: dict[str, list[tuple[str, str]]]
    remaining: list[DependencyEdge]


def plan_fold(standing: list[DependencyEdge], survivor_id: str, folded_ids: list[str]) -> FoldEdgePlan:
    """The dependency-edge side of folding ``folded_ids`` into ``survivor_id`` (D3):
    every standing edge naming a folded chunk in either role is released; its remapped
    pair — folded ids mapped to ``survivor_id``, everything else unchanged — mints
    nothing further when it collapses to a self-edge or duplicates a pair already
    resulting (either untouched in ``standing``, or minted earlier in this same pass,
    ``standing``'s own ``(declared_at, dependency_id)`` order breaking every tie), and
    is minted fresh otherwise. An edge naming two different folded chunks is attributed
    to whichever endpoint is folded when only one is, and to the dependent side's target
    when both are (either choice is correct; this one is deterministic). Raises nothing —
    the caller checks :func:`would_close_a_cycle` against the result before writing
    anything."""
    folded = set(folded_ids)

    def remap(chunk_id: str) -> str:
        return survivor_id if chunk_id in folded else chunk_id

    release_by_target: dict[str, list[str]] = {cid: [] for cid in folded_ids}
    mint_by_target: dict[str, list[tuple[str, str]]] = {cid: [] for cid in folded_ids}
    remaining = [e for e in standing if e.dependent_chunk_id not in folded and e.prerequisite_chunk_id not in folded]
    resulting_pairs = {(e.dependent_chunk_id, e.prerequisite_chunk_id) for e in remaining}

    for edge in standing:
        dep_folded = edge.dependent_chunk_id in folded
        prereq_folded = edge.prerequisite_chunk_id in folded
        if not dep_folded and not prereq_folded:
            continue
        owner = edge.dependent_chunk_id if dep_folded else edge.prerequisite_chunk_id
        release_by_target[owner].append(edge.dependency_id)
        pair = (remap(edge.dependent_chunk_id), remap(edge.prerequisite_chunk_id))
        if pair[0] == pair[1] or pair in resulting_pairs:
            continue
        resulting_pairs.add(pair)
        mint_by_target[owner].append(pair)

    return FoldEdgePlan(release_by_target=release_by_target, mint_by_target=mint_by_target, remaining=remaining)
