"""SQLAlchemy adapter for the chunk facts seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every read below folds already-recorded rows; nothing derives
a status column. Read-only (``blizzard-context:/architecture/repository-access.md``):
``load_facts``/``load_all_facts``/``load_facts_for``/``load_live_statuses`` each project
the union of every other seam's own writes, so this adapter has no write half.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Connection, Table, and_, func

from blizzard.foundation.chunk_status import TERMINAL_STATUSES, ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.model import (
    ChunkFacts,
)
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.kernel.transactions import Transaction
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_fact_loading import (
    MOVEMENT_FAMILIES,
    STATUS_FAMILIES,
    TICK_STATUS_FAMILIES,
    load_chunk_facts,
)
from blizzard.hub.store.internal.chunk_rows import ephemeral_ids_select
from blizzard.hub.store.internal.chunk_terminal_predicates import (
    done_completed_at,
    maybe_live,
    settled_done,
    settled_stopped,
)
from blizzard.hub.store.internal.fact_families import Projection, tables
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import connection_in


def status_family_tables() -> tuple[Table, ...]:
    """Every table the status families read, plus ``chunks`` (the roster the facts load keys on)."""
    by_name = {t.name: t for t in tables(Projection.STATUS)}
    by_name["chunks"] = s.chunks
    return tuple(by_name[name] for name in sorted(by_name))


class ChunkFactsStore:
    """Read-only chunk-facts adapter — the fleet's fact-derivation projection."""

    def __init__(self, store: ScopedConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def load_facts(self, chunk_id: str, *, transaction: Transaction | None = None) -> ChunkFacts | None:
        connection = connection_in(self._store, transaction, "load_facts")
        with connection as conn:
            result = load_chunk_facts(conn, [chunk_id])
        return result.get(chunk_id)

    def load_facts_conn(self, conn: Connection, chunk_id: str) -> ChunkFacts | None:
        """`load_facts`'s already-open-connection sibling — the locked-transaction seam's
        own read (``bzh:store-exclusive-write``), resolved on the caller's connection
        rather than a fresh one."""
        return load_chunk_facts(conn, [chunk_id]).get(chunk_id)

    def load_facts_for_conn(self, conn: Connection, chunk_ids: Sequence[str]) -> dict[str, ChunkFacts]:
        """`load_facts_for`'s already-open-connection sibling — see :meth:`load_facts_conn`."""
        if not chunk_ids:
            return {}
        return load_chunk_facts(conn, chunk_ids)

    def load_all_facts(self) -> dict[str, ChunkFacts]:
        """`src/blizzard/hub/domain/chunk/ports/facts.py`'s `IReadChunkFactsRepository.load_all_facts`."""
        with self._store.read("load_all_facts") as conn:
            return load_chunk_facts(conn, None)

    def load_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, ChunkFacts]:
        """`load_facts`'s batched sibling — every id's
        :class:`ChunkFacts` (`bzh:bulk-reconstitution`)."""
        if not chunk_ids:
            return {}
        with self._store.read("load_facts_for") as conn:
            return load_chunk_facts(conn, chunk_ids)

    def movement_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, ChunkFacts]:
        """Only the transition, migration and restart rows used to derive the current node."""
        if not chunk_ids:
            return {}
        with self._store.read("movement_facts_for") as conn:
            return load_chunk_facts(conn, chunk_ids, families=MOVEMENT_FAMILIES)

    def status_facts_for(
        self, chunk_ids: Sequence[str], *, transaction: Transaction | None = None
    ) -> dict[str, ChunkFacts]:
        """`load_facts_for`'s status-only sibling — every id's :class:`ChunkFacts`, keyed
        by chunk id, reading only the families ``TICK_STATUS_FAMILIES`` names rather than
        every family :meth:`load_facts_for` loads. An id that doesn't exist or is ephemeral is silently
        dropped, the same as :meth:`load_facts_for`."""
        if not chunk_ids:
            return {}
        with connection_in(self._store, transaction, "status_facts_for") as conn:
            return load_chunk_facts(conn, chunk_ids, families=TICK_STATUS_FAMILIES)

    def status_facts_for_conn(self, conn: Connection, chunk_ids: Sequence[str]) -> dict[str, ChunkFacts]:
        """`status_facts_for`'s already-open-connection sibling — see :meth:`load_facts_conn`."""
        if not chunk_ids:
            return {}
        return load_chunk_facts(conn, chunk_ids, families=TICK_STATUS_FAMILIES)

    def load_live_statuses(self, *, transaction: Transaction | None = None) -> dict[str, ChunkStatus]:
        """Every non-ephemeral, non-terminal chunk's derived :class:`ChunkStatus`, keyed by
        chunk id. The chunks a terminal fact already settles are excluded in the store
        query (:func:`maybe_live`), so the facts read and the per-chunk derivation track the
        live fleet rather than every chunk ever minted. The prefilter is sound, not exact —
        a terminal chunk it keeps is dropped by the derivation below. Status derivation
        itself stays in ``src/blizzard/hub/domain/chunk/model.py``; this only narrows which rows get read."""
        with connection_in(self._store, transaction, "load_live_statuses") as conn:
            statuses = self._maybe_live_statuses(conn)
        return {chunk_id: status for chunk_id, status in statuses.items() if status not in TERMINAL_STATUSES}

    def status_counts(self, *, project: ProjectId | None = None) -> dict[ChunkStatus, int]:
        """See :meth:`~blizzard.hub.domain.chunk.ports.facts.IReadChunkFactsRepository.status_counts`."""
        counted = s.chunks.c.chunk_id.not_in(ephemeral_ids_select(self._store))
        if project is not None:
            counted = and_(s.chunks.c.project_id == project, counted)
        with self._store.read("status_counts") as conn:
            stopped, done = (
                conn.execute(self._store.select(s.chunks, func.count()).where(counted, settled)).scalar_one()
                for settled in (settled_stopped(self._store), settled_done(self._store))
            )
            counts = Counter(self._maybe_live_statuses(conn, project).values())
        counts[ChunkStatus.STOPPED] += stopped
        counts[ChunkStatus.DONE] += done
        return {status: counts[status] for status in ChunkStatus}

    def done_since_count(self, since: datetime, *, project: ProjectId | None = None) -> int:
        """See :meth:`~blizzard.hub.domain.chunk.ports.facts.IReadChunkFactsRepository.done_since_count` —
        the settled-done chunks finishing at or after ``since`` are counted in SQL
        (:func:`settled_done`, :func:`done_completed_at`); the :func:`maybe_live` residue that derives ``done``
        resolves its instant through :meth:`ChunkFacts.completed_at`."""
        counted = s.chunks.c.chunk_id.not_in(ephemeral_ids_select(self._store))
        if project is not None:
            counted = and_(s.chunks.c.project_id == project, counted)
        with self._store.read("done_since_count") as conn:
            settled = conn.execute(
                self._store.select(s.chunks, func.count()).where(
                    counted, settled_done(self._store), done_completed_at(self._store) >= since
                )
            ).scalar_one()
            candidates = self._store.select(s.chunks, s.chunks.c.chunk_id).where(
                maybe_live(self._store), s.chunks.c.chunk_id.not_in(ephemeral_ids_select(self._store))
            )
            if project is not None:
                candidates = candidates.where(s.chunks.c.project_id == project)
            facts_by_id = load_chunk_facts(
                conn, [r.chunk_id for r in conn.execute(candidates)], families=STATUS_FAMILIES
            )
        residue = 0
        for facts in facts_by_id.values():
            finished = facts.completed_at() if facts.status() is ChunkStatus.DONE else None
            if finished is not None and finished >= since:
                residue += 1
        return settled + residue

    def _maybe_live_statuses(self, conn: Connection, project: ProjectId | None = None) -> dict[str, ChunkStatus]:
        """Every :func:`maybe_live` candidate's derived status, terminal ones included — the
        prefilter's tie residue derives here, not in SQL. Ephemeral chunks never write a
        terminal fact, so they are excluded in the same query."""
        candidates = self._store.select(s.chunks, s.chunks.c.chunk_id).where(
            maybe_live(self._store), s.chunks.c.chunk_id.not_in(ephemeral_ids_select(self._store))
        )
        if project is not None:
            candidates = candidates.where(s.chunks.c.project_id == project)
        candidate_ids = [r.chunk_id for r in conn.execute(candidates)]
        facts_by_id = load_chunk_facts(conn, candidate_ids, families=STATUS_FAMILIES)
        return {chunk_id: facts.status() for chunk_id, facts in facts_by_id.items()}


_facts_conforms: type[IReadChunkFactsRepository] = ChunkFactsStore
