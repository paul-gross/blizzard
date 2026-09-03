"""Chunk dependency edges — declare and release, under the shared claim lock (issue #456).

A chunk names the chunks it depends on (``blizzard.hub.domain.chunks.dependencies``);
declaring refuses a cycle and admits only in :data:`PRE_CLAIM_STATUSES`, release has no
window. Both share ``ClaimService``/``EditService``/``RestartService``'s ``threading.Lock``
(issue #120) — pinned by ``tests/test_dependency_race.py`` and ``tests/test_dependency_service_component.py``."""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping

from blizzard.foundation.chunk_status import PRE_CLAIM_STATUSES, ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.dependencies import IWriteChunkDependenciesRepository
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.lifecycle import IReadChunkLifecycleRepository
from blizzard.hub.domain.queue import ChunkNotFound
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
    but only narrowing it against ``GroupService``, which takes none. Release never raises it."""

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
        # (delete included); `GroupService` holds none, so this only narrows that race.
        if self._lifecycle.is_ephemeral(prerequisite.chunk_id):
            raise PrerequisiteIsEphemeral(prerequisite.chunk_id)

        standing = self._dependencies.list_standing_edges()
        if _closes_cycle(standing, dependent.chunk_id, prerequisite.chunk_id):
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
    does not yet refuse a standing edge onto a vanished prerequisite."""
    markings: dict[str, str] = {}
    for edge in standing_edges:
        if edge.dependent_chunk_id in markings:
            continue
        if statuses.get(edge.prerequisite_chunk_id) is ChunkStatus.DONE:
            continue
        markings[edge.dependent_chunk_id] = edge.prerequisite_chunk_id
    return markings


def _closes_cycle(standing: list[DependencyEdge], dependent_chunk_id: str, prerequisite_chunk_id: str) -> bool:
    """Would adding the edge ``dependent_chunk_id`` depends-on ``prerequisite_chunk_id``
    close a cycle over ``standing``? True exactly when ``dependent_chunk_id`` is already
    reachable from ``prerequisite_chunk_id`` by following existing standing edges —
    including the zero-length case ``prerequisite_chunk_id == dependent_chunk_id``, the
    trivial self-edge cycle."""
    graph: dict[str, list[str]] = {}
    for edge in standing:
        graph.setdefault(edge.dependent_chunk_id, []).append(edge.prerequisite_chunk_id)

    frontier = [prerequisite_chunk_id]
    seen = {prerequisite_chunk_id}
    while frontier:
        node = frontier.pop()
        if node == dependent_chunk_id:
            return True
        for neighbor in graph.get(node, []):
            if neighbor not in seen:
                seen.add(neighbor)
                frontier.append(neighbor)
    return False
