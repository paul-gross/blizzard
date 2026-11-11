"""SQLAlchemy adapter for a chunk's captured repository targets (package-private).

One immutable ``chunk_repository_targets`` row per chunk and repository, inserted once under the chunk's lock and never
updated (``bzh:facts-not-status``)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row
from sqlalchemy.engine import Connection

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.repositories import RepositoryId
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction
from blizzard.hub.domain.projects.targets import BranchTarget, IWriteChunkTargets
from blizzard.hub.store import schema as s
from blizzard.hub.store.scoped import ScopedConnections, statements_of
from blizzard.hub.store.transactions import connection_for, connection_in

_t = s.chunk_repository_targets


def _target(row: Row[Any]) -> BranchTarget:
    return BranchTarget(
        RepositoryId(row.repository_id), row.base_branch, inherited=row.inherited, captured_at=row.captured_at
    )


def _held(conn: Connection, chunk_ids: Sequence[str]) -> dict[str, dict[RepositoryId, BranchTarget]]:
    stmts = statements_of(conn)
    held: dict[str, dict[RepositoryId, BranchTarget]] = {}
    for batch in id_batches(chunk_ids):
        rows = conn.execute(
            stmts.select(
                _t, _t.c.chunk_id, _t.c.repository_id, _t.c.base_branch, _t.c.inherited, _t.c.captured_at
            ).where(_t.c.chunk_id.in_(batch))
        )
        for row in rows:
            held.setdefault(row.chunk_id, {})[RepositoryId(row.repository_id)] = _target(row)
    return held


class ChunkTargetsStore:
    """Read-write adapter over ``chunk_repository_targets``."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def captured_for(
        self, chunk_ids: Sequence[str], *, transaction: Transaction | None = None
    ) -> dict[str, dict[RepositoryId, BranchTarget]]:
        ids = sorted(set(chunk_ids))
        if not ids:
            return {}
        with connection_in(self._store, transaction, "captured_for") as conn:
            return _held(conn, ids)

    def capture(
        self, chunk_id: str, targets: Sequence[BranchTarget], *, transaction: LockedTransaction
    ) -> dict[RepositoryId, BranchTarget]:
        conn = connection_for(self._store, transaction, chunk=chunk_id)
        held = _held(conn, [chunk_id]).get(chunk_id, {})
        fresh = [target for target in targets if target.repository_id not in held]
        if fresh:
            conn.execute(
                self._store.insert_rows(
                    _t,
                    [
                        {
                            "chunk_id": chunk_id,
                            "repository_id": target.repository_id,
                            "base_branch": target.base_branch,
                            "inherited": target.inherited,
                            "captured_at": target.captured_at,
                        }
                        for target in fresh
                    ],
                )
            )
        return {**{target.repository_id: target for target in fresh}, **held}


_chunk_targets_conforms: type[IWriteChunkTargets] = ChunkTargetsStore
