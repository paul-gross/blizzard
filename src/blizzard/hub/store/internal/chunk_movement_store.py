"""SQLAlchemy adapter for the chunk movement seam (package-private, blizzard#411 Phase 3).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row that happened and status is
derived. Timestamps arrive already stamped (``bzh:injected-clock``).

D6: ``record_transition``, ``record_restart``, and ``record_migration`` are each one
transaction on one connection, unchanged by the seam carve — the shared row helpers below
are plain function calls inside that same ``with self._store.write(...)`` block, never a
second connection."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Connection, select, update

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import MIGRATION_PREFIX, Id
from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.chunks.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.proposals import WorkItemProposalRow
from blizzard.hub.domain.work import MigrationSource
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import (
    DEFAULT_MODEL,
    enqueue_close_intents,
    graph_id_of,
    insert_proposals,
    latest_epoch,
    next_route_seq,
)

# The generic ``merged/<repo>`` landing marker (issue #67) — mirrors domain/work.py's own
# copy (``LandedRepos``'s), which reads it back; each side owns its own constant.
_MARKER_PREFIX = "merged/"


class ChunkMovementStore:
    """The chunk's graph movement — transitions, migrations, restarts, requeues."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def accepted_transition_target(self, chunk_id: str, *, from_node_id: str, epoch: int) -> str | None:
        with self._store.read("accepted_transition_target") as conn:
            row = conn.execute(
                select(s.transitions.c.to_node_id).where(
                    (s.transitions.c.chunk_id == chunk_id)
                    & (s.transitions.c.from_node_id == from_node_id)
                    & (s.transitions.c.epoch == epoch)
                )
            ).first()
            return row.to_node_id if row is not None else None

    def accepted_migration(self, chunk_id: str, *, from_node_id: str, epoch: int) -> bool:
        """True iff a migration is already recorded for ``(chunk_id, from_node_id, epoch)``
        — the idempotency probe a re-applied cross-graph completion short-circuits on (#90).

        A migration writes no ``transitions`` row, so the transition-replay probe cannot
        see it; this is its counterpart, on :meth:`record_migration`'s natural key."""
        with self._store.read("accepted_migration") as conn:
            return self._migration_exists(conn, chunk_id, from_node_id=from_node_id, epoch=epoch)

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
        proposals: list[WorkItemProposalRow],
        decision_id: str | None = None,
    ) -> None:
        with self._store.write("record_transition") as conn:
            conn.execute(
                s.transitions.insert().values(
                    transition_id=transition_id,
                    chunk_id=chunk_id,
                    graph_id=graph_id_of(conn, chunk_id),
                    from_node_id=from_node_id,
                    to_node_id=to_node_id,
                    choice_name=choice_name,
                    decision_id=decision_id,
                    epoch=epoch,
                    runner_id=runner_id,
                    recorded_at=at,
                )
            )
            for row in artifacts:
                conn.execute(
                    s.artifacts.insert().values(
                        artifact_id=row.artifact_id,
                        chunk_id=row.chunk_id,
                        node_id=row.node_id,
                        node_name=row.node_name,
                        epoch=row.epoch,
                        name=row.name,
                        kind=row.kind.value,
                        data=row.data,
                        repo=row.repo,
                        forge=row.forge,
                        produced_at=at,
                    )
                )
            insert_proposals(conn, proposals, at=at)
            if any(row.name.startswith(_MARKER_PREFIX) for row in artifacts):
                enqueue_close_intents(conn, chunk_id, at=at)

    def record_migration(
        self,
        chunk_id: str,
        *,
        from_node_id: str | None,
        from_graph_id: str,
        to_graph_id: str,
        landed_node_id: str | None,
        choice_name: str | None,
        decision_id: str | None = None,
        model: str | None,
        epoch: int,
        at: datetime,
        artifacts: list[ArtifactRow],
        proposals: list[WorkItemProposalRow],
        source: MigrationSource,
        release_route: bool = True,
        clear_intent: bool = False,
        migration_id: str | None = None,
    ) -> str | None:
        """Record a cross-graph migration **atomically and idempotently** (#90).

        One transaction: the fact, the ``chunks.graph_id`` re-pin, the route release
        (unless ``release_route``, #111), this step's artifacts and proposals, and the
        intent clear (``clear_intent``, #124). Keyed ``(chunk_id, from_node_id, epoch)``."""
        with self._store.write("record_migration") as conn:
            if self._migration_exists(conn, chunk_id, from_node_id=from_node_id, epoch=epoch):
                return None
            resolved_migration_id = (
                migration_id if migration_id is not None else Id.mint(MIGRATION_PREFIX, self._clock).value
            )
            conn.execute(
                s.chunk_migrations.insert().values(
                    migration_id=resolved_migration_id,
                    chunk_id=chunk_id,
                    from_node_id=from_node_id,
                    from_graph_id=from_graph_id,
                    to_graph_id=to_graph_id,
                    landed_node_id=landed_node_id,
                    choice_name=choice_name,
                    decision_id=decision_id,
                    model_after=model,
                    epoch=epoch,
                    recorded_at=at,
                    source=source.value,
                )
            )
            values: dict[str, str | None] = {"graph_id": to_graph_id}
            if model is not None:
                # Written INLINE (issue #144): a second transactional write would split the
                # durable fact from the pin it implies (`hub:migration-pin-consistent`).
                values["default_model"] = DEFAULT_MODEL.encode([model])
            if clear_intent:
                values["intended_migration"] = None
            conn.execute(update(s.chunks).where(s.chunks.c.chunk_id == chunk_id).values(**values))
            if release_route:
                conn.execute(
                    s.route_released.insert().values(
                        chunk_id=chunk_id, released_at=at, seq=next_route_seq(conn, chunk_id)
                    )
                )
            for row in artifacts:
                conn.execute(
                    s.artifacts.insert().values(
                        artifact_id=row.artifact_id,
                        chunk_id=row.chunk_id,
                        node_id=row.node_id,
                        node_name=row.node_name,
                        epoch=row.epoch,
                        name=row.name,
                        kind=row.kind.value,
                        data=row.data,
                        repo=row.repo,
                        forge=row.forge,
                        produced_at=at,
                    )
                )
            insert_proposals(conn, proposals, at=at)
            if any(row.name.startswith(_MARKER_PREFIX) for row in artifacts):
                enqueue_close_intents(conn, chunk_id, at=at)
            return resolved_migration_id

    def record_restart(
        self,
        chunk_id: str,
        *,
        from_node_id: str | None,
        to_node_id: str,
        by: str,
        at: datetime,
        decision_id: str | None = None,
        answered_question_ids: Sequence[str] = (),
        answer: str = "",
        to_graph_id: str | None = None,
    ) -> int:
        """Record the forced move and everything it consumes in **one** transaction (#370, #371),
        so a ``kill -9`` cannot leave the chunk moved while an ask still parks it or a graph it no
        longer runs still pins it; each answer keeps first-write-wins. The fence epoch is derived
        HERE rather than handed down — one above every prior attempt is a read-then-write only this
        transaction holds together (``bzh:epoch-fencing``)."""
        with self._store.write("record_restart") as conn:
            epoch = latest_epoch(conn, chunk_id) + 1
            for question_id in answered_question_ids:
                already = conn.execute(
                    select(s.question_answers.c.question_id).where(s.question_answers.c.question_id == question_id)
                ).first()
                if already is None:
                    conn.execute(
                        s.question_answers.insert().values(
                            question_id=question_id, answer=answer, answered_by=by, answered_at=at
                        )
                    )
            from_graph_id = graph_id_of(conn, chunk_id)
            if to_graph_id is not None:
                self._repin_by_restart(
                    conn,
                    chunk_id,
                    from_node_id=from_node_id,
                    from_graph_id=from_graph_id,
                    to_graph_id=to_graph_id,
                    landed_node_id=to_node_id,
                    epoch=epoch,
                    at=at,
                )
            result = conn.execute(
                s.chunk_restarts.insert().values(
                    chunk_id=chunk_id,
                    graph_id=to_graph_id if to_graph_id is not None else from_graph_id,
                    from_node_id=from_node_id,
                    from_graph_id=from_graph_id if to_graph_id is not None else None,
                    to_node_id=to_node_id,
                    epoch=epoch,
                    decision_id=decision_id,
                    restarted_by=by,
                    recorded_at=at,
                )
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_requeue(self, chunk_id: str, *, at: datetime) -> int:
        with self._store.write("record_requeue") as conn:
            result = conn.execute(s.requeues.insert().values(chunk_id=chunk_id, requeued_at=at))
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def _repin_by_restart(
        self,
        conn: Connection,
        chunk_id: str,
        *,
        from_node_id: str | None,
        from_graph_id: str,
        to_graph_id: str,
        landed_node_id: str,
        epoch: int,
        at: datetime,
    ) -> None:
        """A cross-graph restart's migration half (#371), inside the restart's own transaction: the
        fact that owns the re-pin, the pin, and the standing intent this eager move supersedes.
        Stamped at the restart's own ``(recorded_at, epoch)``, so ``latest_movement``'s kind rank
        settles which of the two the chunk stands on. No route release — the holding runner keeps
        it and re-enters — and no ``decision_id``, which is the restart fact's alone."""
        conn.execute(
            s.chunk_migrations.insert().values(
                migration_id=Id.mint(MIGRATION_PREFIX, self._clock).value,
                chunk_id=chunk_id,
                from_node_id=from_node_id,
                from_graph_id=from_graph_id,
                to_graph_id=to_graph_id,
                landed_node_id=landed_node_id,
                choice_name=None,
                decision_id=None,
                model_after=None,
                epoch=epoch,
                recorded_at=at,
                source=MigrationSource.RESTART.value,
            )
        )
        conn.execute(
            update(s.chunks)
            .where(s.chunks.c.chunk_id == chunk_id)
            .values(graph_id=to_graph_id, intended_migration=None)
        )

    @staticmethod
    def _migration_exists(conn: Connection, chunk_id: str, *, from_node_id: str | None, epoch: int) -> bool:
        return (
            conn.execute(
                select(s.chunk_migrations.c.migration_id).where(
                    (s.chunk_migrations.c.chunk_id == chunk_id)
                    & (s.chunk_migrations.c.from_node_id == from_node_id)
                    & (s.chunk_migrations.c.epoch == epoch)
                )
            ).first()
            is not None
        )


def _conforms_movement(x: ChunkMovementStore) -> IWriteChunkMovementRepository:
    return x
