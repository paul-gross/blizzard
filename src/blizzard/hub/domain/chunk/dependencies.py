"""Chunk dependency edges — declare and release.

A chunk names the chunks it depends on (``blizzard.hub.domain.chunk.ports.dependencies``);
declaring refuses a cycle and admits only in :data:`PRE_CLAIM_STATUSES`, release has no
window. Declaring runs under the shared row lock (``bzh:store-exclusive-write``)
``ClaimService``/``EditService``/``RestartService``/``DeleteService`` all take, plus a
residual fleet-wide ``threading.Lock`` the cycle check alone still needs — pinned by
``tests/test_dependency_race.py`` and ``tests/test_dependency_service_component.py``."""

from __future__ import annotations

# The residual cycle-check lock — recorded debt, blizzard-context:/architecture/system-shape/exclusive-writes.md
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from blizzard.foundation.chunk_status import PRE_CLAIM_STATUSES, ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model, dto
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, DependencyEdge
from blizzard.hub.domain.chunk.ports.dependencies import FoldMint, IWriteChunkDependenciesRepository
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites, ILockedChunkRead


class DependentNotEditable(Exception):
    """The dependent chunk is outside :data:`PRE_CLAIM_STATUSES` — mirrors
    :class:`~blizzard.hub.domain.operations.edit.ChunkNotEditable`'s shape for the one status window
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
    the row lock — closing the race against every writer sharing it, `GroupService`
    included. Release never raises it."""

    def __init__(self, chunk_id: str) -> None:
        super().__init__(f"chunk {chunk_id} is ephemeral and cannot be named as a prerequisite")
        self.chunk_id = chunk_id


class DependencyService:
    """Declare and release a dependency edge between two chunks — the operator's ``a
    depends on b`` and its release."""

    def __init__(
        self,
        *,
        dependencies: IWriteChunkDependenciesRepository,
        exclusive: IChunkExclusiveWrites,
        clock: IClock,
        cycle_lock: threading.Lock,
    ) -> None:
        self._dependencies = dependencies
        # The locked-transaction seam (``bzh:store-exclusive-write``) ClaimService's own
        # CAS shares — the row lock over the two named chunks.
        self._exclusive = exclusive
        self._clock = clock
        # The residual fleet-wide lock GroupService also shares: row locks alone cannot
        # close a race between two declares closing a cycle through chunks neither one
        # names, so the cycle check itself still needs one lock over the whole graph.
        self._cycle_lock = cycle_lock

    def declare(self, dependent: Chunk, prerequisite: Chunk, *, by: str) -> DependencyEdge:
        """Declare that ``dependent`` depends on ``prerequisite``.

        Idempotent: an already-standing pair is reported back rather than refused.
        Otherwise refuses, writing nothing, when ``dependent`` is gone or not
        :data:`PRE_CLAIM_STATUSES`, ``prerequisite`` is ephemeral, or it would close a cycle."""
        with self._cycle_lock, self._exclusive.locked([dependent.chunk_id, prerequisite.chunk_id]) as handle:
            return self._declare_locked(handle, dependent, prerequisite, by=by)

    def _declare_locked(
        self, handle: ILockedChunkRead, dependent: Chunk, prerequisite: Chunk, *, by: str
    ) -> DependencyEdge:
        standing = handle.standing_edges()
        existing = next(
            (
                e
                for e in standing
                if e.dependent_chunk_id == dependent.chunk_id and e.prerequisite_chunk_id == prerequisite.chunk_id
            ),
            None,
        )
        if existing is not None:
            return existing

        # A `None` load means gone under this lock — refuse rather than
        # substitute a synthetic status, mirroring `DeleteService.delete`.
        facts = handle.facts(dependent.chunk_id)
        if facts is None:
            raise ChunkNotFound(dependent.chunk_id)
        status = facts.status()
        if status not in PRE_CLAIM_STATUSES:
            raise DependentNotEditable(dependent.chunk_id, status)

        # Re-derived under the same lock: closes the race against every writer holding it
        # — delete and the fold both included.
        if handle.is_ephemeral(prerequisite.chunk_id):
            raise PrerequisiteIsEphemeral(prerequisite.chunk_id)

        if would_close_a_cycle(standing, [(dependent.chunk_id, prerequisite.chunk_id)]):
            raise DependencyWouldCloseCycle(dependent.chunk_id, prerequisite.chunk_id)

        return self._dependencies.declare_locked(
            handle, dependent.chunk_id, prerequisite.chunk_id, by=by, at=self._clock.now()
        )

    def release(self, edge: DependencyEdge, *, by: str) -> DependencyEdge:
        """Release ``edge``'s ``(dependent_chunk_id, prerequisite_chunk_id)`` pair — the
        store resolves whichever edge currently stands for it, not necessarily ``edge``
        itself, so a pair released then freshly re-declared releases the new edge,
        silently, in ``edge``'s place. Admitted whenever some edge stands for the pair
        (no status window); refuses, writing nothing, when none does."""
        with self._cycle_lock:
            released = self._dependencies.release(
                edge.dependent_chunk_id, edge.prerequisite_chunk_id, by=by, at=self._clock.now()
            )
            if released is None:
                raise NoStandingDependencyToRelease(edge.dependent_chunk_id, edge.prerequisite_chunk_id)
            return released


def derive_blocked_prerequisites(
    standing_edges: Iterable[DependencyEdge], statuses: Mapping[str, ChunkStatus]
) -> dict[str, list[str]]:
    """Every unmet prerequisite per dependent chunk id, in declared order — never folded into
    :class:`ChunkFacts` (``bzh:facts-not-status``). Only a dependent read at :data:`PRE_CLAIM_STATUSES` derives
    any; a dependent absent from ``statuses`` reads as its default ``not_ready``. ``standing_edges`` must already
    carry the order of
    :meth:`~blizzard.hub.domain.chunk.ports.dependencies.IReadChunkDependenciesRepository.list_standing_edges`.
    A dependent with none is absent from the result rather than carrying an empty list.
    Full rule: `blizzard-context:/domain/work/statuses.md` §The blocked marking."""
    unmet: dict[str, list[str]] = {}
    for edge in standing_edges:
        dependent_status = statuses.get(edge.dependent_chunk_id)
        if dependent_status is not None and dependent_status not in PRE_CLAIM_STATUSES:
            continue
        if statuses.get(edge.prerequisite_chunk_id) is ChunkStatus.DONE:
            continue
        unmet.setdefault(edge.dependent_chunk_id, []).append(edge.prerequisite_chunk_id)
    return unmet


def derive_blocked_markings(
    standing_edges: Iterable[DependencyEdge], statuses: Mapping[str, ChunkStatus]
) -> dict[str, str]:
    """The blocked marking per dependent chunk id — :func:`derive_blocked_prerequisites` narrowed to the one
    prerequisite the marking names, the earliest-declared of the unmet set."""
    return {
        dependent: prerequisites[0]
        for dependent, prerequisites in derive_blocked_prerequisites(standing_edges, statuses).items()
    }


@dto
@dataclass(frozen=True)
class ChunkNeighbor:
    """One neighbor at one hop of :func:`derive_chunk_neighborhood` —
    the neighbor's own id, its status where it resolved, and the edge's own satisfaction.
    ``status`` is ``None`` only for the residual race a neighbor's facts fail to resolve;
    the edge is still drawn, unsatisfied, rather than dropped."""

    chunk_id: str
    status: ChunkStatus | None
    satisfied: bool


@dto
@dataclass(frozen=True)
class ChunkNeighborhood:
    """A chunk's standing edges one hop each way."""

    prerequisites: list[ChunkNeighbor]
    dependents: list[ChunkNeighbor]


def derive_chunk_neighborhood(
    chunk_id: str, edges: Iterable[DependencyEdge], statuses: Mapping[str, ChunkStatus]
) -> ChunkNeighborhood:
    """A sibling of :func:`derive_blocked_markings`, answering a different question: every edge naming
    ``chunk_id``, for a chunk at any status. ``edges`` must already be in the order of
    :meth:`~blizzard.hub.domain.chunk.ports.dependencies.IReadChunkDependenciesRepository.standing_edges_for`,
    and ``statuses`` must carry ``chunk_id``'s own status. Full rule: `blizzard-context:/domain/work/statuses.md` §The
    neighborhood."""
    prerequisites: list[ChunkNeighbor] = []
    dependents: list[ChunkNeighbor] = []
    subject_done = statuses.get(chunk_id) is ChunkStatus.DONE
    for edge in edges:
        if edge.dependent_chunk_id == chunk_id:
            neighbor_status = statuses.get(edge.prerequisite_chunk_id)
            prerequisites.append(
                ChunkNeighbor(
                    chunk_id=edge.prerequisite_chunk_id,
                    status=neighbor_status,
                    satisfied=neighbor_status is ChunkStatus.DONE,
                )
            )
        if edge.prerequisite_chunk_id == chunk_id:
            dependents.append(
                ChunkNeighbor(
                    chunk_id=edge.dependent_chunk_id,
                    status=statuses.get(edge.dependent_chunk_id),
                    satisfied=subject_done,
                )
            )
    return ChunkNeighborhood(prerequisites=prerequisites, dependents=dependents)


def would_close_a_cycle(standing: list[DependencyEdge], added: list[tuple[str, str]]) -> bool:
    """Would folding ``added``'s ordered ``(dependent, prerequisite)`` pairs into ``standing``'s edges close a cycle
    in the resulting graph? ``standing`` is assumed acyclic already, so a cycle can only pass through one of
    ``added`` — one pair or a whole fold's edge set alike."""
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


@domain_model
@dataclass(frozen=True)
class FoldEdgePlan:
    """Per-target dependency-edge rewrite instructions for one fold: the release/mint
    pairing to apply inside each target's own atomic write,
    plus the untouched remainder of ``standing`` the cycle check runs against."""

    release_by_target: dict[str, list[str]]
    mint_by_target: dict[str, list[FoldMint]]
    remaining: list[DependencyEdge]


def plan_fold(standing: list[DependencyEdge], survivor_id: str, folded_ids: list[str]) -> FoldEdgePlan:
    """The dependency-edge side of folding ``folded_ids`` into ``survivor_id``: every standing edge naming a
    folded chunk in either role is released, and its remapped pair is minted — at the instant the edge was first
    declared — unless it collapses to a self-edge or duplicates a pair already resulting. Where pairs collapse the
    earliest ``declared_at`` wins: a later-declared unfolded standing edge on the same pair is released too and the
    pair re-minted at the earlier instant. ``standing`` is in declared order. Raises nothing — the caller checks
    :func:`would_close_a_cycle` first. Why the instant carries:
    `blizzard-context:/domain/work/statuses.md` §The blocked marking."""
    folded = set(folded_ids)

    def remap(chunk_id: str) -> str:
        return survivor_id if chunk_id in folded else chunk_id

    release_by_target: dict[str, list[str]] = {cid: [] for cid in folded_ids}
    mint_by_target: dict[str, list[FoldMint]] = {cid: [] for cid in folded_ids}
    remaining = [e for e in standing if e.dependent_chunk_id not in folded and e.prerequisite_chunk_id not in folded]
    unfolded_by_pair = {(e.dependent_chunk_id, e.prerequisite_chunk_id): e for e in remaining}
    minted_pairs: set[tuple[str, str]] = set()
    superseded: set[str] = set()

    for edge in standing:
        dep_folded = edge.dependent_chunk_id in folded
        prereq_folded = edge.prerequisite_chunk_id in folded
        if not dep_folded and not prereq_folded:
            continue
        owner = edge.dependent_chunk_id if dep_folded else edge.prerequisite_chunk_id
        release_by_target[owner].append(edge.dependency_id)
        pair = (remap(edge.dependent_chunk_id), remap(edge.prerequisite_chunk_id))
        if pair[0] == pair[1] or pair in minted_pairs:
            continue
        existing = unfolded_by_pair.get(pair)
        if existing is not None:
            if existing.declared_at <= edge.declared_at:
                continue
            release_by_target[owner].append(existing.dependency_id)
            superseded.add(existing.dependency_id)
            del unfolded_by_pair[pair]
        minted_pairs.add(pair)
        mint_by_target[owner].append(FoldMint(pair[0], pair[1], edge.declared_at))

    remaining = [e for e in remaining if e.dependency_id not in superseded]
    return FoldEdgePlan(release_by_target=release_by_target, mint_by_target=mint_by_target, remaining=remaining)
